"""Currently playing song from Spotify (Web API, PKCE login) or YouTube Music.

Spotify is queried directly, YouTube Music through the browser sign-in in ytmusic.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .. import listening, player, spotify_connect
from ..config_store import config_path
from . import ytmusic
from .http_json import USER_AGENT

LOGGER = logging.getLogger(__name__)
SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PLAYING_URL = "https://api.spotify.com/v1/me/player?additional_types=track,episode"
SPOTIFY_API = "https://api.spotify.com/v1"
# Lesen, was läuft, und Abspielen/Steuern (Suche braucht keine eigene Berechtigung).
# Gefällt mir und Playlists bearbeiten brauchen die Bibliotheks- und Playlist-Schreibrechte.
SPOTIFY_SCOPE = (
    "user-read-currently-playing user-read-playback-state user-modify-playback-state"
    " playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"
    " user-library-read user-library-modify"
)
SPOTIFY_REPEAT = {"off": "off", "all": "context", "one": "track"}
_liked: dict[str, bool | None] = {}
SPOTIFY_CALLBACK_PATH = "/api/music/spotify/callback"
PENDING_SECONDS = 600
TIMEOUT = 8

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}
_access: dict[str, Any] = {"token": "", "expires": 0.0}


class MusicError(RuntimeError):
    pass


def token_path() -> Path:
    return config_path().parent / "spotify_token.json"


def _read_token() -> dict[str, Any]:
    try:
        data = json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_token(data: dict[str, Any]) -> None:
    target = token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".spotify_token.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def spotify_connected() -> bool:
    return bool(_read_token().get("refresh_token"))


def spotify_disconnect() -> None:
    with _lock:
        token_path().unlink(missing_ok=True)
        _access.update({"token": "", "expires": 0.0})


def _request(
    url: str, *, form: dict[str, str] | None = None, token: str = "", method: str = "", payload: Any = None,
) -> tuple[int, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif method in {"PUT", "POST"} and data is None:
        data = b""
    request = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if form is not None else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"null")
        except ValueError:
            body = None
        return exc.code, body


def _error_text(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or fallback)
        return str(body.get("error_description") or body.get("message") or error or fallback)
    return fallback


def spotify_authorize_url(client_id: str, redirect_uri: str) -> str:
    """Start of the PKCE login; the matching verifier stays on the server until the callback."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        for key in [key for key, item in _pending.items() if now - item["created"] > PENDING_SECONDS]:
            del _pending[key]
        _pending[state] = {"created": now, "verifier": verifier, "client_id": client_id, "redirect_uri": redirect_uri}
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SPOTIFY_SCOPE,
        "state": state,
    })
    return f"{SPOTIFY_AUTHORIZE_URL}?{query}"


def spotify_finish(state: str, code: str) -> None:
    with _lock:
        pending = _pending.pop(state, None)
    if not pending or time.monotonic() - pending["created"] > PENDING_SECONDS:
        raise MusicError("Anmeldung abgelaufen oder ungültig, bitte erneut verbinden")
    status, body = _request(SPOTIFY_TOKEN_URL, form={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": pending["client_id"],
        "code_verifier": pending["verifier"],
    })
    if status != 200 or not isinstance(body, dict) or not body.get("refresh_token"):
        raise MusicError(f"Spotify-Anmeldung fehlgeschlagen: {_error_text(body, f'HTTP {status}')}")
    with _lock:
        _write_token({"client_id": pending["client_id"], "refresh_token": body["refresh_token"]})
        _access.update({"token": body["access_token"], "expires": time.monotonic() + int(body.get("expires_in", 3600))})


def _spotify_access_token() -> str:
    with _lock:
        if _access["token"] and _access["expires"] - 60 > time.monotonic():
            return str(_access["token"])
        stored = _read_token()
    if not stored.get("refresh_token"):
        raise MusicError("Spotify ist nicht verbunden")
    status, body = _request(SPOTIFY_TOKEN_URL, form={
        "grant_type": "refresh_token",
        "refresh_token": stored["refresh_token"],
        "client_id": stored.get("client_id", ""),
    })
    if status == 400 and isinstance(body, dict) and body.get("error") == "invalid_grant":
        raise MusicError("Spotify-Zugang abgelaufen, bitte im Menü neu verbinden")
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise MusicError(f"Spotify-Token: {_error_text(body, f'HTTP {status}')}")
    with _lock:
        # Spotify kann beim Erneuern einen neuen Refresh-Token ausgeben.
        if body.get("refresh_token") and body["refresh_token"] != stored["refresh_token"]:
            _write_token({**stored, "refresh_token": body["refresh_token"]})
        _access.update({"token": body["access_token"], "expires": time.monotonic() + int(body.get("expires_in", 3600))})
        return str(body["access_token"])


def _cover(images: Any) -> str:
    if not isinstance(images, list) or not images:
        return ""
    # Spotify sortiert absteigend (640, 300, 64); 300 px reicht für die Karte.
    image = images[1] if len(images) > 1 else images[0]
    return str(image.get("url", "")) if isinstance(image, dict) else ""


def spotify_now_playing() -> dict[str, Any] | None:
    status, body = _request(SPOTIFY_PLAYING_URL, token=_spotify_access_token())
    if status == 401:
        with _lock:
            _access.update({"token": "", "expires": 0.0})
        raise MusicError("Spotify-Token ungültig, wird erneuert")
    if status == 204 or body is None:
        return None
    if status != 200 or not isinstance(body, dict):
        raise MusicError(f"Spotify: {_error_text(body, f'HTTP {status}')}")
    item = body.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") == "episode":
        show = item.get("show") or {}
        artist, album, images = str(show.get("name", "")), "", item.get("images") or show.get("images")
    else:
        artist = ", ".join(str(a.get("name", "")) for a in item.get("artists", []) if isinstance(a, dict))
        album_data = item.get("album") or {}
        album, images = str(album_data.get("name", "")), album_data.get("images")
    device = body.get("device") or {}
    track_uri = str(item.get("uri") or "")
    return {
        "source": "spotify",
        "controls": "spotify",
        "track_id": track_uri,
        "liked": spotify_liked(track_uri),
        "shuffle": bool(body.get("shuffle_state")),
        "repeat": {value: key for key, value in SPOTIFY_REPEAT.items()}.get(str(body.get("repeat_state") or "off"), "off"),
        "seekable": True,
        "device": "pi" if device.get("name") == spotify_connect.DEVICE_NAME else str(device.get("name") or ""),
        "title": str(item.get("name", "")),
        "artist": artist,
        "album": album,
        "cover": _cover(images),
        "progress_ms": int(body.get("progress_ms") or 0),
        "duration_ms": int(item.get("duration_ms") or 0),
        "paused": not body.get("is_playing"),
        "volume": device.get("volume_percent"),
        "fetched_at": int(time.time() * 1000),
    }


def _spotify_api(method: str, path: str, payload: Any = None) -> Any:
    status, body = _request(f"{SPOTIFY_API}{path}", token=_spotify_access_token(), method=method, payload=payload)
    if status == 401:
        with _lock:
            _access.update({"token": "", "expires": 0.0})
        raise MusicError("Spotify-Anmeldung abgelaufen, bitte erneut versuchen")
    if status == 403:
        raise MusicError("Spotify erlaubt das nicht. Ist Spotify im Menü mit den neuen Rechten verbunden (neu verbinden) und Premium aktiv?")
    if status == 404 and "device" in str(body).lower():
        raise MusicError("Kein aktives Spotify-Gerät gefunden")
    if status >= 400:
        raise MusicError(f"Spotify: {_error_text(body, f'HTTP {status}')}")
    return body


def _pi_device_id() -> str:
    body = _spotify_api("GET", "/me/player/devices") or {}
    for device in body.get("devices") or []:
        if isinstance(device, dict) and device.get("name") == spotify_connect.DEVICE_NAME and device.get("id"):
            return str(device["id"])
    raise MusicError("Der Pi ist noch nicht als Spotify-Lautsprecher bereit. Bitte im Menü unter Musik koppeln.")


def spotify_play(kind: str, uri: str, start: int = 0, shuffle: bool = False) -> None:
    """Song allein; Album, Playlist oder Künstler als Kontext ab ``start``, auf Wunsch gemischt."""
    if not re.fullmatch(r"spotify:(track|album|playlist|artist):[A-Za-z0-9]+", uri):
        raise MusicError("Unbekannter Spotify-Eintrag")
    device = urllib.parse.quote(_pi_device_id())
    if kind != "song":
        _spotify_api("PUT", f"/me/player/shuffle?state={'true' if shuffle else 'false'}&device_id={device}")
    payload: dict[str, Any] = {"uris": [uri]} if kind == "song" else {"context_uri": uri}
    if kind in {"album", "playlist"} and start > 0 and not shuffle:
        payload["offset"] = {"position": int(start)}
    _spotify_api("PUT", f"/me/player/play?device_id={device}", payload)


def spotify_control(action: str, value: Any = None) -> None:
    if action == "toggle":
        current = _spotify_api("GET", "/me/player") or {}
        _spotify_api("PUT", "/me/player/pause" if current.get("is_playing") else "/me/player/play")
    elif action == "next":
        _spotify_api("POST", "/me/player/next")
    elif action == "previous":
        _spotify_api("POST", "/me/player/previous")
    elif action == "volume":
        try:
            volume = max(0, min(100, int(value)))
        except (TypeError, ValueError) as exc:
            raise MusicError("Ungültige Lautstärke") from exc
        _spotify_api("PUT", f"/me/player/volume?volume_percent={volume}")
    elif action == "stop":
        _spotify_api("PUT", "/me/player/pause")
    elif action == "seek":
        _spotify_api("PUT", f"/me/player/seek?position_ms={max(0, int(float(value or 0) * 1000))}")
    elif action == "shuffle":
        _spotify_api("PUT", f"/me/player/shuffle?state={'true' if value else 'false'}")
    elif action == "repeat":
        if value not in SPOTIFY_REPEAT:
            raise MusicError("Wiederholen kennt nur off, all und one")
        _spotify_api("PUT", f"/me/player/repeat?state={SPOTIFY_REPEAT[str(value)]}")
    elif action in {"jump", "remove", "move"}:
        raise MusicError("Spotify erlaubt das Umsortieren der Warteschlange nicht")
    else:
        raise MusicError("Unbekannte Aktion")


def spotify_queue() -> list[dict[str, Any]]:
    """Was in Spotify als Nächstes kommt (nur lesen; Spotify erlaubt kein Umsortieren)."""
    body = _spotify_api("GET", "/me/player/queue") or {}
    found = []
    for position, item in enumerate(body.get("queue") or []):
        if isinstance(item, dict):
            album = item.get("album") or {}
            images = album.get("images") or item.get("images") or []
            found.append({
                "position": position, "id": str(item.get("uri", "")), "title": str(item.get("name", "")),
                "artist": ", ".join(str(a.get("name", "")) for a in item.get("artists", []) if isinstance(a, dict)),
                "cover": str(images[-1].get("url", "")) if images and isinstance(images[-1], dict) else "",
                "duration": int(item.get("duration_ms") or 0) // 1000,
            })
    return found[:40]


def spotify_liked(track_uri: str) -> bool | None:
    """Gefällt-mir-Zustand, je Song nur einmal gefragt (alte Verbindungen ohne Recht: unbekannt)."""
    if not track_uri.startswith("spotify:track:"):
        return None
    if track_uri not in _liked:
        try:
            body = _spotify_api("GET", f"/me/tracks/contains?ids={track_uri.rsplit(':', 1)[1]}")
            _liked[track_uri] = bool(body[0]) if isinstance(body, list) and body else None
        except MusicError:
            _liked[track_uri] = None
    return _liked[track_uri]


def remember_liked(track_uri: str, liked: bool) -> None:
    _liked[track_uri] = liked


def refresh_seconds(_config: dict[str, Any]) -> int:
    # Spielt der Pi selbst, kostet die Abfrage nichts; dann öfter, damit Titel und Zeit nicht hinterherhängen.
    return 2 if player.active() else 5


def get_now_playing(config: dict[str, Any]) -> dict[str, Any]:
    spotify_connect.ensure_running()
    # Was der Pi selbst abspielt, ist immer bekannt und hat Vorrang vor allen Diensten.
    sleep = listening.sleep_state()
    local = player.now_playing()
    if local:
        return {"enabled": True, "ok": True, "playing": True, "controls": "pi", **local, **sleep}
    music = config.get("music", {})
    source = music.get("source", "off")
    if source == "off":
        return {"enabled": False}
    errors: list[str] = []
    lookups = []
    if source in {"auto", "spotify"} and spotify_connected():
        lookups.append(spotify_now_playing)
    if source in {"auto", "ytmusic"} and ytmusic.connected():
        lookups.append(ytmusic.now_playing)
    paused: dict[str, Any] | None = None
    for lookup in lookups:
        try:
            track = lookup()
        except (MusicError, ytmusic.YTMusicError, OSError, ValueError) as exc:
            LOGGER.warning("Musikquelle fehlgeschlagen: %s", exc)
            errors.append(str(exc)[:160])
            continue
        if track and not track.get("paused"):
            return {"enabled": True, "ok": True, "playing": True, **track, **sleep}
        paused = paused or track
    # Pausiertes Spotify nur zeigen, wenn sonst nichts läuft (mit Knopf zum Weiterspielen).
    if paused:
        return {"enabled": True, "ok": True, "playing": True, **paused, **sleep}
    result: dict[str, Any] = {"enabled": True, "ok": not errors, "playing": False}
    if errors:
        result["error"] = "; ".join(errors)
    elif not lookups:
        result["error"] = "Keine Musikquelle verbunden"
    return result
