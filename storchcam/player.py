"""Playback on the Pi itself: YouTube Music (yt-dlp resolves the audio streams) and internet radio.

One mpv runs idle in the background and is driven over its JSON IPC socket (``MpvClient``).
``PiPlayer`` owns the play queue (``PlayQueue``: shuffle, repeat, reordering), the radio
station, volume and equalizer. ``StreamResolver`` turns video IDs into stream addresses with
the YouTube Music session cookies and caches them; the next track is resolved while the
current one plays. ``RadioCovers`` looks up album art for the song a station is playing.

Audio goes to PulseAudio/PipeWire, which routes it to a connected Bluetooth speaker. The
module-level functions at the end keep the older function interface for the server.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from collections import OrderedDict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config_store import config_path
from .equalizer import LEVEL_LABEL, Equalizer
from .equalizer import level as meter_level
from .music_queue import PlayQueue, Track
from .providers import ytmusic
from .providers.http_json import USER_AGENT, fetch_json

LOGGER = logging.getLogger(__name__)
PAGE_KINDS = {"album", "playlist", "artist"}
SINGLE_KINDS = {"song", "video"}
QUEUE_LIMIT = 200
# Ab so vielen Sekunden springt „Zurück“ an den Anfang des Songs statt zum vorigen.
RESTART_SECONDS = 5
RADIO_RETRIES = 5
RADIO_RETRY_SECONDS = 4
RADIO_HISTORY = 8


class PlayerError(RuntimeError):
    pass


class MpvClient:
    """mpv im Leerlauf mit JSON-IPC; Antworten werden über request_id zugeordnet."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None], volume: int = 100) -> None:
        executable = shutil.which("mpv")
        if not executable:
            raise PlayerError("mpv ist nicht installiert")
        self._folder = tempfile.mkdtemp(prefix="storchcam-mpv.")
        path = os.path.join(self._folder, "ipc")
        self.process = subprocess.Popen(
            [executable, "--idle=yes", "--no-video", "--no-terminal", "--ao=pulse", "--audio-display=no",
             "--volume-max=100", f"--volume={volume}", "--ytdl=no", f"--input-ipc-server={path}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        deadline = time.monotonic() + 5
        while not os.path.exists(path):
            if time.monotonic() > deadline or self.process.poll() is not None:
                self.close()
                raise PlayerError("mpv ließ sich nicht starten")
            time.sleep(0.05)
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            self.close()
            raise PlayerError("Die Wiedergabe auf dem Gerät braucht Linux (mpv-Steuerung über einen Unix-Socket)")
        self._socket = socket.socket(family, socket.SOCK_STREAM)
        self._socket.connect(path)
        self._on_event = on_event
        self._pending: dict[int, dict[str, Any]] = {}
        self._next_id = 0
        self._write_lock = threading.Lock()
        threading.Thread(target=self._reader, name="mpv-ipc", daemon=True).start()

    def alive(self) -> bool:
        return self.process.poll() is None

    def _reader(self) -> None:
        buffer = b""
        while True:
            try:
                chunk = self._socket.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "request_id" in message and message["request_id"] in self._pending:
                    waiter = self._pending.pop(message["request_id"])
                    waiter["reply"] = message
                    waiter["event"].set()
                elif "event" in message:
                    self._on_event(message)

    def command(self, *arguments: Any, timeout: float = 5) -> Any:
        with self._write_lock:
            self._next_id += 1
            request_id = self._next_id
            waiter: dict[str, Any] = {"event": threading.Event()}
            self._pending[request_id] = waiter
            try:
                self._socket.sendall(json.dumps({"command": list(arguments), "request_id": request_id}).encode() + b"\n")
            except OSError as exc:
                raise PlayerError("mpv ist nicht erreichbar") from exc
        if not waiter["event"].wait(timeout):
            self._pending.pop(request_id, None)
            raise PlayerError("mpv antwortet nicht")
        reply = waiter["reply"]
        if reply.get("error") != "success":
            raise PlayerError(f"mpv: {reply.get('error')}")
        return reply.get("data")

    def get(self, name: str) -> Any:
        try:
            return self.command("get_property", name)
        except PlayerError:
            return None

    def close(self) -> None:
        try:
            self._socket.close()
        except (OSError, AttributeError):
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        shutil.rmtree(self._folder, ignore_errors=True)


def cookie_file() -> Path:
    """Netscape-Cookiedatei für yt-dlp aus der YouTube-Music-Anmeldung, nur bei Änderung neu geschrieben."""
    target = config_path().parent / "ytmusic_cookies.txt"
    source = ytmusic.auth_path()
    if not source.is_file():
        raise PlayerError("YouTube Music ist nicht verbunden")
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    cookie = json.loads(source.read_text(encoding="utf-8")).get("cookie", "")
    lines = ["# Netscape HTTP Cookie File"]
    for part in str(cookie).split("; "):
        name, _, value = part.partition("=")
        if name:
            lines.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", "2147483647", name, value]))
    fd, temporary = tempfile.mkstemp(prefix=".ytmusic_cookies.", dir=target.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


class StreamResolver:
    """Video-ID → Audioadresse über yt-dlp, mit Cache bis kurz vor Ablauf der Adresse.

    Löst gerade ein anderer Thread (das Vorladen) dieselbe ID auf, wartet der Aufruf darauf,
    statt yt-dlp ein zweites Mal zu starten.
    """

    WAIT_SECONDS = 25

    def __init__(self) -> None:
        self._urls: dict[str, tuple[str, float]] = {}
        self._running: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def cached(self, video_id: str) -> str | None:
        entry = self._urls.get(video_id)
        return entry[0] if entry and entry[1] - 600 > time.time() else None

    def url(self, video_id: str) -> str:
        with self._lock:
            ready = self.cached(video_id)
            if ready:
                return ready
            running = self._running.get(video_id)
            if running is None:
                self._running[video_id] = threading.Event()
        if running is not None:
            running.wait(self.WAIT_SECONDS)
            ready = self.cached(video_id)
            if ready:
                return ready
        try:
            return self._resolve(video_id)
        finally:
            with self._lock:
                done = self._running.pop(video_id, None)
            if done is not None:
                done.set()

    def _resolve(self, video_id: str) -> str:
        import yt_dlp  # type: ignore[import-untyped]  # erst hier: groß und nur zum Abspielen nötig

        options = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True, "cookiefile": str(cookie_file())}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
        except Exception as exc:  # yt-dlp wirft je nach Fehler sehr unterschiedliche Typen
            raise PlayerError(f"Song lässt sich nicht abspielen: {str(exc)[:160]}") from exc
        url = str(info["url"])
        expires = float(parse_qs(urlparse(url).query).get("expire", [time.time() + 3600])[0])
        self._urls[video_id] = (url, expires)
        return url

    def prefetch(self, video_id: str) -> None:
        if not video_id or self.cached(video_id):
            return
        try:
            self.url(video_id)
        except PlayerError as exc:
            LOGGER.info("Nächsten Song nicht vorbereitet: %s", exc)


class Prefetcher:
    """Löst Songs nacheinander im Hintergrund auf, damit ein Tipp darauf sofort spielt.

    Nur ein Arbeitsthread, damit der Pi nicht mehrere yt-dlp gleichzeitig startet; neue Wünsche
    kommen nach vorn, alte fallen hinten heraus.
    """

    LIMIT = 6

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver
        self._pending: deque[str] = deque()
        self._lock = threading.Lock()
        self._busy = False

    def add(self, video_ids: list[str]) -> None:
        with self._lock:
            for video_id in reversed([video_id for video_id in video_ids if video_id]):
                if video_id in self._pending:
                    self._pending.remove(video_id)
                self._pending.appendleft(video_id)
            while len(self._pending) > self.LIMIT:
                self._pending.pop()
            if self._busy or not self._pending:
                return
            self._busy = True
        threading.Thread(target=self._work, name="player-prefetch", daemon=True).start()

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._busy = False
                    return
                video_id = self._pending.popleft()
            self.resolver.prefetch(video_id)


class RadioCovers:
    """Albumbild zum Radiotitel über die freie iTunes-Suche; Ergebnisse bleiben im Speicher."""

    SEARCH_URL = "https://itunes.apple.com/search"
    LIMIT = 300

    def __init__(self, fetch: Callable[..., Any] = fetch_json) -> None:
        self._fetch = fetch
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def cover(self, title: str) -> str:
        """Bekanntes Bild sofort, sonst leer und Suche im Hintergrund."""
        key = title.strip().casefold()
        if " – " not in title or not key:
            return ""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            self._cache[key] = ""
            while len(self._cache) > self.LIMIT:
                self._cache.popitem(last=False)
        threading.Thread(target=self._lookup, args=(key, title), name="radio-cover", daemon=True).start()
        return ""

    def _lookup(self, key: str, title: str) -> None:
        query = urllib.parse.urlencode({"term": title.replace(" – ", " "), "media": "music", "entity": "song", "limit": "1"})
        try:
            data = self._fetch(f"{self.SEARCH_URL}?{query}", headers={"User-Agent": USER_AGENT}, timeout=6)
            artwork = str((data.get("results") or [{}])[0].get("artworkUrl100") or "")
        except (OSError, ValueError, AttributeError, IndexError):
            return
        if artwork.startswith("https://"):
            with self._lock:
                self._cache[key] = artwork.replace("100x100bb", "300x300bb")


class PiPlayer:
    """Ein mpv für alles, was der Pi selbst spielt: Warteschlange oder Radiosender."""

    def __init__(
        self,
        client_factory: Callable[..., MpvClient] = MpvClient,
        resolver: StreamResolver | None = None,
        covers: RadioCovers | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._client_factory = client_factory
        self._client: MpvClient | None = None
        self.resolver = resolver or StreamResolver()
        self.prefetcher = Prefetcher(self.resolver)
        self.covers = covers or RadioCovers()
        self.queue = PlayQueue()
        self.mode = "idle"  # idle, ytmusic oder radio
        self.station: dict[str, Any] | None = None
        self.volume = 100
        self.equalizer = Equalizer()
        self.loading = False
        self.error = ""
        self._radio_retries = 0
        self.radio_titles: deque[dict[str, Any]] = deque(maxlen=RADIO_HISTORY)

    # ---------- mpv ----------
    def _mpv(self) -> MpvClient:
        with self._lock:
            if self._client is None or not self._client.alive():
                if self._client:
                    self._client.close()
                self._client = self._client_factory(self._on_event, self.volume)
                self._apply_equalizer(self._client)
            return self._client

    def _running(self) -> MpvClient | None:
        with self._lock:
            return self._client if self._client and self._client.alive() else None

    def _apply_equalizer(self, client: MpvClient) -> None:
        try:
            client.command("set_property", "af", self.equalizer.filter_chain())
        except PlayerError as exc:
            LOGGER.warning("Klangregler nicht gesetzt: %s", exc)

    def _on_event(self, message: dict[str, Any]) -> None:
        event = message.get("event")
        if event == "file-loaded" and self.mode == "radio":
            self._radio_retries = 0
        # Nur ein natürliches Ende zählt; „stop“ kommt auch beim Laden des nächsten Songs.
        if event != "end-file" or message.get("reason") not in {"eof", "error"}:
            return
        if self.mode == "radio":
            self._reconnect_radio()
            return
        LOGGER.info("Song zu Ende (%s), nächster folgt", message.get("reason"))
        threading.Thread(target=self.advance, args=(1,), kwargs={"natural": True}, name="player-next", daemon=True).start()

    # ---------- YouTube Music ----------
    def play_tracks(self, tracks: list[Track], start: int = 0, shuffle: bool = False) -> None:
        playable = [track for track in tracks if track.id][:QUEUE_LIMIT]
        if not playable:
            raise PlayerError("Keine abspielbaren Songs gefunden")
        with self._lock:
            self.queue.replace(playable, start, shuffle)
            self.mode, self.station, self.error = "ytmusic", None, ""
            self.loading = True
        threading.Thread(target=self._load_current, name="player-load", daemon=True).start()

    def _load_current(self) -> None:
        with self._lock:
            track = self.queue.current
            self.loading, self.error = True, ""
        if track is None:
            return
        try:
            url = self.resolver.url(track.id)
            client = self._mpv()
            client.command("loadfile", url, "replace")
            client.command("set_property", "pause", False)
        except PlayerError as exc:
            with self._lock:
                self.error = str(exc)
            LOGGER.warning("Wiedergabe fehlgeschlagen: %s", exc)
        finally:
            with self._lock:
                self.loading = False
        self.prefetch_upcoming()

    def prefetch_upcoming(self) -> None:
        """Die nächsten beiden Titel vorbereiten, damit „Vor“ ohne Wartezeit spielt."""
        with self._lock:
            following = self.queue.next_index(natural=False)
            upcoming = [self.queue.tracks[following].id] if following is not None else []
            if following is not None and following + 1 < len(self.queue.tracks):
                upcoming.append(self.queue.tracks[following + 1].id)
        self.prefetcher.add(upcoming)

    def advance(self, step: int, natural: bool = False) -> None:
        with self._lock:
            target = self.queue.next_index(natural=natural) if step > 0 else self.queue.previous_index()
            if target is None:
                return
            self.queue.jump(target)
        self._load_current()

    # ---------- Radio ----------
    def play_radio(self, station: dict[str, Any]) -> None:
        if urlparse(str(station.get("url", ""))).scheme not in {"http", "https"}:
            raise PlayerError("Ungültige Senderadresse")
        with self._lock:
            self.queue.clear()
            self.mode, self.station, self.error = "radio", dict(station), ""
            self.loading = True
            self._radio_retries = 0
            self.radio_titles.clear()
        threading.Thread(target=self._load_radio, name="player-radio", daemon=True).start()

    def _load_radio(self) -> None:
        with self._lock:
            if self.mode != "radio" or not self.station:
                return
            url = str(self.station["url"])
            self.loading, self.error = True, ""
        try:
            client = self._mpv()
            client.command("loadfile", url, "replace")
            client.command("set_property", "pause", False)
        except PlayerError as exc:
            with self._lock:
                self.error = str(exc)
            LOGGER.warning("Radio lässt sich nicht abspielen: %s", exc)
        finally:
            with self._lock:
                self.loading = False

    def _reconnect_radio(self) -> None:
        # Ein Radiostream endet nie von selbst: die Verbindung ist weg, also neu verbinden.
        if self._radio_retries < RADIO_RETRIES and self.station:
            self._radio_retries += 1
            LOGGER.info("Radiostream abgerissen, Versuch %s von %s", self._radio_retries, RADIO_RETRIES)
            threading.Timer(RADIO_RETRY_SECONDS, self._load_radio).start()
        else:
            with self._lock:
                self.error = "Sender nicht erreichbar"

    def current_station(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self.station) if self.mode == "radio" and self.station else None

    # ---------- Steuerung ----------
    def active(self) -> bool:
        with self._lock:
            return self.mode != "idle"

    def control(self, action: str, value: Any = None) -> None:
        if action == "volume":
            self.set_volume(value)
            return
        if action == "shuffle":
            with self._lock:
                self.queue.set_shuffle(bool(value))
            return
        if action == "repeat":
            try:
                with self._lock:
                    self.queue.set_repeat(str(value))
            except ValueError as exc:
                raise PlayerError(str(exc)) from exc
            return
        client = self._running()
        if client is None or self.mode == "idle":
            raise PlayerError("Auf dem Pi läuft gerade nichts")
        if action == "toggle":
            client.command("cycle", "pause")
        elif action == "stop":
            client.command("stop")
            with self._lock:
                self.queue.clear()
                self.mode, self.station = "idle", None
        elif action == "seek":
            client.command("seek", max(0.0, float(value or 0)), "absolute")
        elif self.mode == "radio" and action in {"next", "previous", "jump", "remove", "move"}:
            raise PlayerError("Beim Radio wechseln Vor und Zurück den Lieblingssender")
        elif action == "next":
            threading.Thread(target=self.advance, args=(1,), name="player-next", daemon=True).start()
        elif action == "previous":
            if float(client.get("time-pos") or 0) > RESTART_SECONDS or self.queue.previous_index() is None:
                client.command("seek", 0, "absolute")
            else:
                threading.Thread(target=self.advance, args=(-1,), name="player-previous", daemon=True).start()
        elif action in {"jump", "remove", "move"}:
            self._edit_queue(action, value)
        else:
            raise PlayerError("Unbekannte Aktion")

    def _edit_queue(self, action: str, value: Any) -> None:
        try:
            with self._lock:
                if action == "jump":
                    self.queue.jump(int(value))
                elif action == "remove":
                    self.queue.remove(int(value))
                else:
                    self.queue.move(int(value["from"]), int(value["to"]))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise PlayerError(str(exc) or "Ungültige Position") from exc
        if action == "jump":
            threading.Thread(target=self._load_current, name="player-jump", daemon=True).start()

    def set_volume(self, value: Any) -> None:
        try:
            volume = max(0, min(100, int(value)))
        except (TypeError, ValueError) as exc:
            raise PlayerError("Ungültige Lautstärke") from exc
        self.volume = volume
        client = self._running()
        if client:
            client.command("set_property", "volume", volume)

    def set_equalizer(self, equalizer: Equalizer) -> None:
        self.equalizer = equalizer
        client = self._running()
        if client:
            self._apply_equalizer(client)

    def level(self) -> float:
        """Momentaner Pegel 0…1 für die bewegte Anzeige; 0 ohne Wiedergabe."""
        client = self._running()
        if client is None or self.mode == "idle":
            return 0.0
        return meter_level(client.get(f"af-metadata/{LEVEL_LABEL}"))

    def mark_liked(self, track_id: str, liked: bool) -> None:
        with self._lock:
            for track in self.queue.tracks:
                if track.id == track_id:
                    track.liked = liked

    # ---------- Anzeige ----------
    def now_playing(self) -> dict[str, Any] | None:
        """Was mpv gerade spielt (oder pausiert); None, wenn der Pi nichts abspielt."""
        with self._lock:
            mode, loading, error = self.mode, self.loading, self.error
            track = self.queue.current
        client = self._running()
        if mode == "radio":
            return self._radio_now(client, loading, error)
        if mode != "ytmusic" or track is None:
            return None
        if client and not loading and client.get("idle-active"):
            return None  # Warteschlange zu Ende gespielt
        paused = bool(client.get("pause")) if client else True
        position = client.get("time-pos") if client and not loading else 0
        duration = client.get("duration") if client and not loading else track.duration
        with self._lock:
            queue = self.queue.window()
            shuffle, repeat, index = self.queue.shuffle, self.queue.repeat, self.queue.index
        return {
            "source": "ytmusic", "device": "pi", "track_id": track.id, "liked": track.liked,
            "title": track.title or "Wird geladen …", "artist": track.artist, "album": "", "cover": track.cover,
            "progress_ms": int(float(position or 0) * 1000), "duration_ms": int(float(duration or track.duration or 0) * 1000),
            "paused": paused or loading, "loading": loading, "error": error, "volume": self.volume,
            "shuffle": shuffle, "repeat": repeat, "queue": queue, "queue_index": index, "seekable": True,
            "equalizer": list(self.equalizer.gains), "fetched_at": int(time.time() * 1000),
        }

    @staticmethod
    def _icy_title(client: MpvClient | None, station_name: str) -> str:
        """Aktueller Titel aus den ICY-Metadaten des Streams („Künstler – Titel“), sonst leer."""
        if client is None:
            return ""
        metadata = client.get("metadata")
        title = str(metadata.get("icy-title") or "").strip() if isinstance(metadata, dict) else ""
        if not title:
            media = str(client.get("media-title") or "").strip()
            title = "" if media.startswith(("http://", "https://")) or media == station_name else media
        return " – ".join(part.strip() for part in title.split(" - ", 1)) if " - " in title else title

    def _radio_now(self, client: MpvClient | None, loading: bool, error: str) -> dict[str, Any]:
        station = self.station or {}
        name = str(station.get("name") or "Radio")
        song = "" if loading else self._icy_title(client, name)
        if song and (not self.radio_titles or self.radio_titles[0]["title"] != song):
            self.radio_titles.appendleft({"title": song, "at": int(time.time() * 1000)})
        cover = self.covers.cover(song) if song else ""
        for item in self.radio_titles:
            item["cover"] = item.get("cover") or self.covers.cover(item["title"])
        return {
            "source": "radio", "device": "pi", "title": name, "artist": song if not loading else "Verbinde …",
            "album": "", "cover": cover,
            "station": {key: station.get(key) for key in ("uuid", "name", "url", "country", "codec", "bitrate", "tags")},
            "live": True, "progress_ms": 0, "duration_ms": 0, "seekable": False,
            "paused": (bool(client.get("pause")) if client else True) or loading, "loading": loading, "error": error,
            "volume": self.volume, "recent_titles": list(self.radio_titles), "equalizer": list(self.equalizer.gains),
            "fetched_at": int(time.time() * 1000),
        }

    def shutdown(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self.queue.clear()
            self.mode, self.station = "idle", None
        if client:
            client.close()


def full_output_volume(wait_for_bluetooth: float = 0) -> None:
    """Stellt den Lautsprecher des Pi (PipeWire/Pulse) auf 100 %; leiser gestellt wird nur noch im Player.

    Bluetooth-Lautsprecher starten in PipeWire oft bei 40 %, was dort rund -24 dB bedeutet. Nach dem
    Verbinden erscheint der Lautsprecher erst nach ein paar Sekunden als Ausgabe, darum kann gewartet werden.
    """
    pactl = shutil.which("pactl")
    if not pactl:
        return
    deadline = time.monotonic() + wait_for_bluetooth
    try:
        while time.monotonic() < deadline:
            sink = subprocess.run([pactl, "get-default-sink"], capture_output=True, text=True, timeout=5, check=False).stdout
            if "bluez" in sink:
                break
            time.sleep(1)
        subprocess.run([pactl, "set-sink-volume", "@DEFAULT_SINK@", "100%"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Lautstärke des Lautsprechers nicht gesetzt: %s", exc)


# ---------- Funktionen für den Server (Fassade über den einen Player des Prozesses) ----------
_player = PiPlayer()


def default_player() -> PiPlayer:
    return _player


def play(kind: str, identifier: str, start: int = 0, shuffle: bool = False, single: Track | None = None) -> None:
    """YouTube Music: ein Song spielt allein; Album, Playlist oder Künstler als Warteschlange."""
    from .library import LibraryError, YouTubeMusicLibrary

    if not identifier:
        raise PlayerError("Unbekannter Eintrag")
    if kind in SINGLE_KINDS:
        _player.play_tracks([single or Track(id=identifier)])
        return
    if kind not in PAGE_KINDS:
        raise PlayerError("Unbekannter Eintrag")
    try:
        tracks = YouTubeMusicLibrary().tracks(kind, identifier)
    except LibraryError as exc:
        raise PlayerError(str(exc)) from exc
    _player.play_tracks(tracks, start, shuffle)


def prefetch(video_ids: list[str]) -> None:
    """Songs im Hintergrund vorbereiten, z. B. die ersten Titel einer gerade geöffneten Seite."""
    _player.prefetcher.add(video_ids)


def play_radio(station: dict[str, Any]) -> None:
    _player.play_radio(station)


def current_station() -> dict[str, Any] | None:
    return _player.current_station()


def active() -> bool:
    return _player.active()


def control(action: str, value: Any = None) -> None:
    _player.control(action, value)


def now_playing() -> dict[str, Any] | None:
    return _player.now_playing()


def set_equalizer(gains: list[float]) -> None:
    _player.set_equalizer(Equalizer(tuple(float(gain) for gain in gains)))


def level() -> float:
    return _player.level()


def shutdown() -> None:
    _player.shutdown()
