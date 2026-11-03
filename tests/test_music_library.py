"""Play queue rules, the two music libraries and their endpoints."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from storchcam import player
from storchcam.library import LibraryError, SpotifyLibrary, YouTubeMusicLibrary
from storchcam.music_queue import PlayQueue, Track
from storchcam.providers import music, ytmusic
from tests.test_server import post_json, request, running_server


def songs(count: int) -> list[Track]:
    return [Track(id=f"v{index}") for index in range(count)]


def ids(queue: PlayQueue) -> list[str]:
    return [track.id for track in queue.tracks]


def test_shuffle_mixes_only_what_comes_and_restores_the_order() -> None:
    queue = PlayQueue(_random=random.Random(4))
    queue.replace(songs(8), start=2)
    queue.set_shuffle(True)
    assert ids(queue)[:3] == ["v0", "v1", "v2"] and sorted(ids(queue)) == sorted(f"v{i}" for i in range(8))
    assert ids(queue) != [f"v{i}" for i in range(8)]
    queue.jump(5)
    current = queue.current
    queue.set_shuffle(False)
    assert ids(queue) == [f"v{i}" for i in range(8)] and queue.current is current


def test_repeat_modes_and_ends() -> None:
    queue = PlayQueue()
    queue.replace(songs(2), start=1)
    assert queue.next_index(natural=True) is None and queue.previous_index() == 0
    queue.set_repeat("all")
    assert queue.next_index(natural=True) == 0
    queue.set_repeat("one")
    assert queue.next_index(natural=True) == 1 and queue.next_index(natural=False) == 0
    with pytest.raises(ValueError):
        queue.set_repeat("forever")
    assert PlayQueue().next_index(natural=False) is None


def test_moving_and_removing_keep_the_current_song() -> None:
    queue = PlayQueue()
    queue.replace(songs(5), start=2)
    queue.move(0, 4)
    assert ids(queue) == ["v1", "v2", "v3", "v4", "v0"] and queue.current.id == "v2"
    queue.move(1, 3)
    assert queue.current.id == "v2" and queue.index == 3
    queue.remove(0)
    assert queue.current.id == "v2" and queue.index == 2
    with pytest.raises(IndexError):
        queue.remove(queue.index)
    with pytest.raises(IndexError):
        queue.move(0, 9)


class FakeYouTube:
    def get_album(self, browse_id: str) -> dict[str, Any]:
        return {"title": "Platte", "artists": [{"name": "Band"}], "year": "2024", "thumbnails": [{"url": "https://lh3.googleusercontent.com/a=w60-h60"}],
                "tracks": [{"videoId": "a1", "title": "Eins", "artists": [{"name": "Band"}], "duration_seconds": 180, "likeStatus": "LIKE"}, {"title": "ohne ID"}]}

    def get_playlist(self, playlist_id: str, limit: int) -> dict[str, Any]:
        assert playlist_id == "PL1"
        return {"title": "Mix", "author": {"name": "Ich"}, "tracks": [{"videoId": "p1", "title": "Zwei", "artists": [], "length": "3:05"}]}

    def get_artist(self, channel_id: str) -> dict[str, Any]:
        return {"name": "Band", "songs": {"browseId": "VLOLAK", "results": [{"videoId": "s1", "title": "Hit", "artists": [{"name": "Band"}]}]},
                "albums": {"results": [{"title": "Platte", "browseId": "MPRE1", "year": "2024"}]}, "singles": {"results": [{"title": "Single"}]}}


def test_youtube_pages_for_album_playlist_and_artist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ytmusic, "api", lambda: FakeYouTube())
    library = YouTubeMusicLibrary()
    album = library.open("album", "MPRE1")
    assert album["title"] == "Platte" and album["subtitle"] == "Band · 2024" and [track["id"] for track in album["tracks"]] == ["a1"]
    assert album["tracks"][0]["liked"] is True and album["tracks"][0]["duration"] == 180
    assert library.open("playlist", "VLPL1")["tracks"][0]["duration"] == 185
    artist = library.open("artist", "UC1")
    assert artist["play_id"] == "VLOLAK" and [item["id"] for item in artist["albums"]] == ["MPRE1"]
    assert [track.id for track in library.tracks("album", "MPRE1")] == ["a1"]
    with pytest.raises(LibraryError):
        library.open("song", "x")
    with pytest.raises(LibraryError, match="Liked Music"):
        library.add_to_playlist("LM", "a1")


def test_spotify_pages_like_and_add(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []
    responses = {
        "/playlists/abc?": {"name": "Liste", "owner": {"display_name": "Ich"}, "images": [{"url": "big"}],
                            "items": {"items": [{"item": {"uri": "spotify:track:t1", "name": "Neu", "artists": [{"name": "A"}], "duration_ms": 61000, "album": {"images": [{"url": "c"}]}}},
                                                {"track": {"uri": "spotify:track:t2", "name": "Alt", "artists": [], "album": {}}}]}},
        "/albums/def?": {"name": "Album", "artists": [{"name": "A"}], "release_date": "2021-05-01", "images": [{"url": "cover"}],
                         "tracks": {"items": [{"uri": "spotify:track:t3", "name": "Drei", "artists": [{"name": "A"}], "duration_ms": 1000}]}},
    }

    def api(method: str, path: str, payload: Any = None) -> Any:
        calls.append((method, path, payload))
        return next((value for prefix, value in responses.items() if path.startswith(prefix)), None)

    library = SpotifyLibrary(api)
    playlist = library.open("playlist", "spotify:playlist:abc")
    assert [track["id"] for track in playlist["tracks"]] == ["spotify:track:t1", "spotify:track:t2"] and playlist["tracks"][0]["duration"] == 61
    album = library.open("album", "spotify:album:def")
    assert album["subtitle"] == "A · 2021" and album["tracks"][0]["cover"] == "cover"
    library.like("spotify:track:t1", True)
    library.like("spotify:track:t1", False)
    library.add_to_playlist("spotify:playlist:abc", "spotify:track:t3")
    assert calls[-3:] == [("PUT", "/me/tracks?ids=t1", None), ("DELETE", "/me/tracks?ids=t1", None), ("POST", "/playlists/abc/tracks", {"uris": ["spotify:track:t3"]})]
    with pytest.raises(LibraryError):
        library.open("playlist", "spotify:album:def")


def test_open_like_add_and_level_endpoints(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    monkeypatch.setattr(ytmusic, "api", lambda: FakeYouTube())
    rated: list[tuple[str, str]] = []
    monkeypatch.setattr(FakeYouTube, "rate_song", lambda self, video_id, rating: rated.append((video_id, rating)), raising=False)
    monkeypatch.setattr(music, "_spotify_api", lambda method, path, payload=None: (_ for _ in ()).throw(music.MusicError("Spotify erlaubt das nicht")))
    monkeypatch.setattr(player, "level", lambda: 0.5)
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, page = post_json(address, "/api/player/open", {"service": "ytmusic", "kind": "album", "id": "MPRE1"})
        assert status == 200 and page["results"]["tracks"][0]["title"] == "Eins"
        assert post_json(address, "/api/player/open", {"service": "ytmusic", "kind": "song", "id": "x"})[0] == 400
        assert post_json(address, "/api/player/like", {"service": "ytmusic", "id": "a1", "liked": True})[0] == 200
        assert rated == [("a1", "LIKE")]
        status, _headers, failed = post_json(address, "/api/player/add", {"service": "spotify", "playlist": "spotify:playlist:abc", "id": "spotify:track:t1"})
        assert status == 502 and "erlaubt" in failed["error"]
        status, _headers, meter = request(address, "GET", "/api/player/level")
        assert status == 200 and meter["level"] == 0.5


def test_control_answers_with_the_new_queue_at_once(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nach Verschieben oder Zufall zeigt die Oberfläche sofort die neue Reihenfolge, ohne auf die nächste Abfrage zu warten."""
    from tests.test_new_features import FakeMpv

    config["admin"]["require_pin"] = False
    mpv = FakeMpv()
    resolver = type("Resolver", (), {"url": lambda self, video_id: f"https://audio/{video_id}", "prefetch": lambda self, video_id: None})()
    pi = player.PiPlayer(client_factory=lambda *_a: mpv, resolver=resolver, covers=player.RadioCovers(fetch=lambda *_a, **_k: {"results": []}))
    monkeypatch.setattr(player, "_player", pi)
    pi.play_tracks(songs(4))
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, result = post_json(address, "/api/player/control", {"target": "pi", "action": "move", "value": {"from": 3, "to": 1}})
        assert status == 200 and [item["id"] for item in result["music"]["queue"]] == ["v0", "v3", "v1", "v2"]
        status, _headers, result = post_json(address, "/api/player/control", {"target": "pi", "action": "repeat", "value": "one"})
        assert result["music"]["repeat"] == "one" and result["music"]["controls"] == "pi"


def test_opened_pages_are_kept_for_a_while() -> None:
    from storchcam.library import PageCache

    now = [0.0]
    cache = PageCache(size=2, seconds=60, clock=lambda: now[0])
    cache.put("album", "a", {"title": "A"})
    cache.put("album", "b", {"title": "B"})
    assert cache.get("album", "a") == {"title": "A"}
    cache.put("album", "c", {"title": "C"})
    # „b“ war am längsten unbenutzt und fällt heraus; nach Ablauf der Zeit auch der Rest.
    assert cache.get("album", "b") is None and cache.get("album", "c") == {"title": "C"}
    now[0] = 61
    assert cache.get("album", "a") is None


def test_youtube_page_is_loaded_once_when_played_after_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class CountingYouTube(FakeYouTube):
        def get_album(self, browse_id: str) -> dict[str, Any]:
            calls.append(browse_id)
            return super().get_album(browse_id)

    monkeypatch.setattr(ytmusic, "api", lambda: CountingYouTube())
    YouTubeMusicLibrary().open("album", "MPRE1")
    assert [track.id for track in YouTubeMusicLibrary().tracks("album", "MPRE1")] == ["a1"]
    assert calls == ["MPRE1"]


def test_prefetcher_resolves_newest_wishes_first_and_only_once() -> None:
    from storchcam.player import Prefetcher, StreamResolver

    class Resolver(StreamResolver):
        def __init__(self) -> None:
            super().__init__()
            self.resolved: list[str] = []

        def _resolve(self, video_id: str) -> str:
            self.resolved.append(video_id)
            self._urls[video_id] = (f"https://audio/{video_id}", 1e12)
            return self._urls[video_id][0]

    resolver = Resolver()
    prefetcher = Prefetcher(resolver)
    prefetcher._busy = True  # Arbeitsthread noch nicht starten, erst sammeln
    prefetcher.add(["a", "b"])
    prefetcher.add(["c", "a"])
    assert list(prefetcher._pending) == ["c", "a", "b"]
    prefetcher._work()
    assert resolver.resolved == ["c", "a", "b"] and not prefetcher._busy
    prefetcher._busy = True
    prefetcher.add(["a"])
    prefetcher._work()
    # Schon aufgelöst: kein zweiter yt-dlp-Lauf, und ein direkter Aufruf nutzt den Cache.
    assert resolver.resolved == ["c", "a", "b"] and resolver.url("a") == "https://audio/a"
