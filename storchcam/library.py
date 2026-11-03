"""Browsing YouTube Music and Spotify behind one interface: search, own playlists, pages, likes.

The menu treats both services the same way: a search returns songs, albums, playlists and
artists; opening an album, playlist or artist returns a page with its songs (and for artists
their albums). Each service is one class; the server picks it by name (strategy pattern).
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol

from .music_queue import Track
from .providers import ytmusic

Entry = dict[str, Any]
PAGE_KINDS = {"album", "playlist", "artist"}
PAGE_LIMIT = 200


class LibraryError(RuntimeError):
    """Readable reason why a music service could not answer."""


class MusicLibrary(Protocol):
    service: str

    def search(self, query: str) -> list[Entry]: ...

    def playlists(self) -> list[Entry]: ...

    def open(self, kind: str, identifier: str) -> Entry: ...

    def like(self, track_id: str, liked: bool) -> None: ...

    def add_to_playlist(self, playlist_id: str, track_id: str) -> None: ...


class PageCache:
    """Zuletzt geöffnete Seiten für ein paar Minuten merken.

    Wer ein Album öffnet und dann einen Titel antippt, soll nicht warten, bis YouTube Music
    dieselbe Seite ein zweites Mal liefert.
    """

    def __init__(self, size: int = 12, seconds: float = 600, clock: Callable[[], float] = time.monotonic) -> None:
        self.size, self.seconds, self.clock = size, seconds, clock
        self._pages: OrderedDict[tuple[str, str], tuple[float, Entry]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, kind: str, identifier: str) -> Entry | None:
        with self._lock:
            found = self._pages.get((kind, identifier))
            if not found or self.clock() - found[0] > self.seconds:
                return None
            self._pages.move_to_end((kind, identifier))
            return found[1]

    def put(self, kind: str, identifier: str, page: Entry) -> None:
        with self._lock:
            self._pages[(kind, identifier)] = (self.clock(), page)
            self._pages.move_to_end((kind, identifier))
            while len(self._pages) > self.size:
                self._pages.popitem(last=False)


def entry(kind: str, identifier: str, title: str, subtitle: str = "", cover: str = "", duration: int = 0) -> Entry:
    return {"kind": kind, "id": identifier, "title": title, "subtitle": subtitle, "cover": cover, "duration": duration}


def _names(people: Any) -> str:
    return ", ".join(str(person.get("name", "")) for person in people or [] if isinstance(person, dict))


class YouTubeMusicLibrary:
    service = "ytmusic"
    SEARCH_TYPES = {"song", "video", "album", "playlist", "artist"}
    pages = PageCache()

    def __init__(self, api: Callable[[], Any] | None = None) -> None:
        # Spät auflösen, damit ein Austausch von ytmusic.api (Anmeldung, Tests) sofort greift.
        self._api = api or (lambda: ytmusic.api())

    def _call(self, action: str, work: Callable[[Any], Any]) -> Any:
        try:
            return work(self._api())
        except LibraryError:
            raise
        except Exception as exc:  # ytmusicapi wirft je nach Fehler sehr unterschiedliche Typen
            raise LibraryError(f"{action} fehlgeschlagen: {str(exc)[:160]}") from exc

    @staticmethod
    def cover(item: dict[str, Any]) -> str:
        thumbnails = item.get("thumbnails") or item.get("thumbnail") or []
        url = str(thumbnails[-1].get("url", "")) if isinstance(thumbnails, list) and thumbnails else ""
        return ytmusic.sized_cover(url)

    @staticmethod
    def seconds(item: dict[str, Any]) -> int:
        if item.get("duration_seconds"):
            return int(item["duration_seconds"])
        total = 0
        for part in str(item.get("length") or item.get("duration") or "").split(":"):
            if not part.isdigit():
                return 0
            total = total * 60 + int(part)
        return total

    def track(self, item: dict[str, Any], cover: str = "") -> Track:
        like = item.get("likeStatus")
        return Track(
            id=str(item.get("videoId", "")),
            title=str(item.get("title", "")),
            artist=_names(item.get("artists")),
            cover=self.cover(item) or cover,
            duration=self.seconds(item),
            liked=None if like is None else like == "LIKE",
        )

    def search(self, query: str) -> list[Entry]:
        clean = query.strip()
        if not clean:
            return []
        results = self._call("Suche", lambda api: api.search(clean, limit=20))
        found: list[Entry] = []
        for item in results or []:
            kind = item.get("resultType")
            if kind not in self.SEARCH_TYPES:
                continue
            identifier = item.get("videoId") if kind in {"song", "video"} else item.get("browseId") if kind in {"album", "artist"} else item.get("playlistId") or item.get("browseId")
            if not identifier:
                continue
            title = str(item.get("artist") or _names(item.get("artists"))) if kind == "artist" else str(item.get("title") or "")
            subtitle = "" if kind == "artist" else _names(item.get("artists")) or str(item.get("author") or "")
            found.append(entry(kind, str(identifier), title, subtitle, self.cover(item), self.seconds(item)))
        return found

    def playlists(self) -> list[Entry]:
        items = self._call("Playlists laden", lambda api: api.get_library_playlists(limit=50))
        found = []
        for item in items or []:
            if isinstance(item, dict) and item.get("playlistId"):
                count = item.get("count")
                found.append(entry("playlist", str(item["playlistId"]), str(item.get("title") or ""), f"{count} Songs" if count not in (None, "") else "", self.cover(item)))
        return found

    def tracks(self, kind: str, identifier: str) -> list[Track]:
        """Alle Titel eines Albums, einer Playlist oder (beliebteste) eines Künstlers."""
        return [Track(**{key: value for key, value in song.items() if key in Track.__dataclass_fields__}) for song in self.open(kind, identifier)["tracks"]]

    def open(self, kind: str, identifier: str) -> Entry:
        cached = self.pages.get(kind, identifier)
        if cached is not None:
            return cached
        page = self._load(kind, identifier)
        self.pages.put(kind, identifier, page)
        return page

    def _load(self, kind: str, identifier: str) -> Entry:
        if kind == "album":
            album = self._call("Album öffnen", lambda api: api.get_album(identifier))
            cover = self.cover(album)
            songs = [self.track(item, cover) for item in album.get("tracks") or []]
            subtitle = " · ".join(filter(None, [_names(album.get("artists")), str(album.get("year") or "")]))
            return self._page(kind, identifier, str(album.get("title") or ""), subtitle, cover, songs)
        if kind == "playlist":
            playlist = self._call("Playlist öffnen", lambda api: api.get_playlist(identifier.removeprefix("VL"), limit=PAGE_LIMIT))
            songs = [self.track(item) for item in playlist.get("tracks") or []]
            author = playlist.get("author")
            subtitle = str(author.get("name", "")) if isinstance(author, dict) else str(author or "")
            return self._page(kind, identifier, str(playlist.get("title") or ""), subtitle, self.cover(playlist), songs)
        if kind == "artist":
            artist = self._call("Künstler öffnen", lambda api: api.get_artist(identifier))
            section = artist.get("songs") or {}
            songs = [self.track(item) for item in section.get("results") or []]
            albums = [
                entry("album", str(item.get("browseId")), str(item.get("title") or ""), str(item.get("year") or ""), self.cover(item))
                for group in ("albums", "singles") for item in (artist.get(group) or {}).get("results") or [] if item.get("browseId")
            ]
            page = self._page(kind, identifier, str(artist.get("name") or ""), "Künstler", self.cover(artist), songs)
            page.update({"albums": albums, "play_id": str(section.get("browseId") or "")})
            return page
        raise LibraryError("Nur Alben, Playlists und Künstler lassen sich öffnen")

    def _page(self, kind: str, identifier: str, title: str, subtitle: str, cover: str, songs: list[Track]) -> Entry:
        playable = [song for song in songs if song.id]
        return {**entry(kind, identifier, title, subtitle, cover), "tracks": [song.as_dict() for song in playable], "albums": []}

    def like(self, track_id: str, liked: bool) -> None:
        self._call("Gefällt mir", lambda api: api.rate_song(track_id, "LIKE" if liked else "INDIFFERENT"))

    def add_to_playlist(self, playlist_id: str, track_id: str) -> None:
        if playlist_id in {"LM", "VLLM"}:
            raise LibraryError("„Liked Music“ füllt sich über „Gefällt mir“")
        self._call("Zur Playlist hinzufügen", lambda api: api.add_playlist_items(playlist_id.removeprefix("VL"), [track_id]))


SPOTIFY_URI = re.compile(r"spotify:(track|album|playlist|artist):([A-Za-z0-9]+)")
SPOTIFY_KINDS = {"track": "song", "album": "album", "playlist": "playlist", "artist": "artist"}


def spotify_id(uri: str, expected: str) -> str:
    match = SPOTIFY_URI.fullmatch(uri)
    if not match or match.group(1) != expected:
        raise LibraryError("Unbekannter Spotify-Eintrag")
    return match.group(2)


class SpotifyLibrary:
    service = "spotify"

    def __init__(self, api: Callable[..., Any]) -> None:
        self._api = api  # music._spotify_api: (method, path, payload=None) -> JSON

    @staticmethod
    def image(images: Any, large: bool = False) -> str:
        if not isinstance(images, list) or not images:
            return ""
        chosen = images[0] if large else images[-1]
        return str(chosen.get("url", "")) if isinstance(chosen, dict) else ""

    def song(self, item: dict[str, Any], cover: str = "") -> Track:
        album = item.get("album") or {}
        return Track(id=str(item.get("uri", "")), title=str(item.get("name", "")), artist=_names(item.get("artists")),
                     cover=self.image(album.get("images")) or cover, duration=int(item.get("duration_ms") or 0) // 1000)

    def search(self, query: str) -> list[Entry]:
        clean = query.strip()
        if not clean:
            return []
        params = urllib.parse.urlencode({"q": clean, "type": "track,album,playlist,artist", "limit": "8", "market": "from_token"})
        body = self._api("GET", f"/search?{params}") or {}
        found: list[Entry] = []
        for key, kind in (("tracks", "song"), ("artists", "artist"), ("albums", "album"), ("playlists", "playlist")):
            for item in (body.get(key) or {}).get("items") or []:
                if not isinstance(item, dict) or not item.get("uri"):
                    continue
                if kind == "song":
                    found.append(entry(kind, str(item["uri"]), str(item.get("name", "")), _names(item.get("artists")), self.image((item.get("album") or {}).get("images")), int(item.get("duration_ms") or 0) // 1000))
                elif kind == "playlist":
                    found.append(entry(kind, str(item["uri"]), str(item.get("name", "")), str((item.get("owner") or {}).get("display_name") or ""), self.image(item.get("images"))))
                else:
                    found.append(entry(kind, str(item["uri"]), str(item.get("name", "")), "" if kind == "artist" else _names(item.get("artists")), self.image(item.get("images"))))
        return found

    def playlists(self) -> list[Entry]:
        body = self._api("GET", "/me/playlists?limit=50") or {}
        found = []
        for item in body.get("items") or []:
            if isinstance(item, dict) and item.get("uri"):
                total = (item.get("items") or item.get("tracks") or {}).get("total")
                found.append(entry("playlist", str(item["uri"]), str(item.get("name", "")), f"{total} Songs" if isinstance(total, int) else "", self.image(item.get("images"))))
        return found

    def open(self, kind: str, identifier: str) -> Entry:
        if kind == "album":
            album = self._api("GET", f"/albums/{spotify_id(identifier, 'album')}?market=from_token") or {}
            cover = self.image(album.get("images"), large=True)
            songs = [self.song(item, cover) for item in (album.get("tracks") or {}).get("items") or [] if isinstance(item, dict)]
            subtitle = " · ".join(filter(None, [_names(album.get("artists")), str(album.get("release_date") or "")[:4]]))
            return self._page(kind, identifier, str(album.get("name", "")), subtitle, cover, songs)
        if kind == "playlist":
            playlist = self._api("GET", f"/playlists/{spotify_id(identifier, 'playlist')}?market=from_token") or {}
            # Spotify nennt die Liste inzwischen „items“ (früher „tracks“), den Eintrag „item“ (früher „track“).
            rows = (playlist.get("items") or playlist.get("tracks") or {}).get("items") or []
            songs = [self.song(row.get("item") or row.get("track") or {}) for row in rows if isinstance(row, dict)]
            owner = str((playlist.get("owner") or {}).get("display_name") or "")
            return self._page(kind, identifier, str(playlist.get("name", "")), owner, self.image(playlist.get("images"), large=True), songs)
        if kind == "artist":
            artist_id = spotify_id(identifier, "artist")
            artist = self._api("GET", f"/artists/{artist_id}") or {}
            top = self._api("GET", f"/artists/{artist_id}/top-tracks?market=from_token") or {}
            albums = self._api("GET", f"/artists/{artist_id}/albums?include_groups=album,single&limit=20&market=from_token") or {}
            page = self._page(kind, identifier, str(artist.get("name", "")), "Künstler", self.image(artist.get("images"), large=True), [self.song(item) for item in top.get("tracks") or []])
            page["albums"] = [entry("album", str(item["uri"]), str(item.get("name", "")), str(item.get("release_date") or "")[:4], self.image(item.get("images"))) for item in albums.get("items") or [] if isinstance(item, dict) and item.get("uri")]
            page["play_id"] = identifier
            return page
        raise LibraryError("Nur Alben, Playlists und Künstler lassen sich öffnen")

    def _page(self, kind: str, identifier: str, title: str, subtitle: str, cover: str, songs: list[Track]) -> Entry:
        return {**entry(kind, identifier, title, subtitle, cover), "tracks": [song.as_dict() for song in songs if song.id], "albums": []}

    def like(self, track_id: str, liked: bool) -> None:
        self._api("PUT" if liked else "DELETE", f"/me/tracks?ids={spotify_id(track_id, 'track')}")

    def add_to_playlist(self, playlist_id: str, track_id: str) -> None:
        spotify_id(track_id, "track")
        self._api("POST", f"/playlists/{spotify_id(playlist_id, 'playlist')}/tracks", {"uris": [track_id]})

    def liked(self, track_id: str) -> bool | None:
        try:
            body = self._api("GET", f"/me/tracks/contains?ids={spotify_id(track_id, 'track')}")
        except Exception:  # alte Verbindung ohne Leserecht: Zustand unbekannt
            return None
        return bool(body[0]) if isinstance(body, list) and body else None
