"""Hardened local HTTP server for static UI, snapshots and authenticated config."""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import mimetypes
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import segno

from . import bluetooth, display, gaming, hls_proxy, listening, pairing, player, spotify_connect
from .admin import SessionStore, hash_pin, verify_pin
from .config_store import ConfigError, config_path, public_config, save_config, validate_config, youtube_api_key
from .equalizer import Equalizer
from .library import LibraryError, MusicLibrary, SpotifyLibrary, YouTubeMusicLibrary
from .music_queue import Track
from .providers import music, radio, stream_source, ytmusic
from .providers.geocoding import search_places, timezone_for
from .providers.transit_vrr_smart import search_station
from .providers.youtube import YouTubeError, search_live
from .state import StateManager
from .version import __version__

LOGGER = logging.getLogger(__name__)
# Python 3.10/3.11 kennen woff2 nicht; ohne Typ würde nosniff die Schrift blockieren.
mimetypes.add_type("font/woff2", ".woff2")

QueryParams = dict[str, list[str]]
GetRoute = Callable[[QueryParams], Any]
PostRoute = Callable[[dict[str, Any]], Any]


def web_directory() -> Path:
    return Path(__file__).resolve().parent / "web"


# Theme-Bilder, die man selbst in <Konfigordner>/theme/ ablegen kann (z. B. data/theme/dagobert-tag.png);
# fehlt die Datei, liefert der Server die mitgelieferte Zeichnung aus web/theme/.
THEME_IMAGES = {"dagobert-tag", "dagobert-nacht"}
THEME_IMAGE_SUFFIXES = (".png", ".webp", ".svg")


def theme_image(name: str) -> Path | None:
    if name not in THEME_IMAGES:
        return None
    custom_dir = config_path().parent / "theme"
    for suffix in THEME_IMAGE_SUFFIXES:
        candidate = custom_dir / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    builtin = web_directory() / "theme" / f"{name}.svg"
    return builtin if builtin.is_file() else None


def _query_value(params: QueryParams, name: str) -> str:
    return params.get(name, [""])[0]


@dataclass
class ServerContext:
    config: dict[str, Any]
    state: StateManager
    started_at: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock)
    sessions: SessionStore = field(init=False)

    def __post_init__(self) -> None:
        ttl = int(self.config.get("server", {}).get("admin_session_minutes", 30)) * 60
        self.sessions = SessionStore(ttl)
        self.apply_equalizer(self.config)

    @staticmethod
    def apply_equalizer(config: dict[str, Any]) -> None:
        gains = config.get("music", {}).get("equalizer", {}).get("gains")
        if gains:
            player.default_player().set_equalizer(Equalizer(tuple(float(gain) for gain in gains)))

    def get_config(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.config)

    def replace_config(self, config: dict[str, Any]) -> None:
        with self.lock:
            changed_sound = self.config.get("music", {}).get("equalizer") != config.get("music", {}).get("equalizer")
            self.config = copy.deepcopy(config)
        self.state.update_config(config)
        if changed_sound:
            self.apply_equalizer(config)


class StorchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], context: ServerContext) -> None:
        self.context = context
        super().__init__(address, StorchHandler)


class RequestError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class StorchHandler(BaseHTTPRequestHandler):
    server_version = f"StorchCam/{__version__}"
    sys_version = ""

    @property
    def context(self) -> ServerContext:
        return self.server.context  # type: ignore[attr-defined]

    def _config(self) -> dict[str, Any]:
        return self.context.get_config()

    def _security_headers(self) -> None:
        config = self._config()
        stream_url = str(config.get("stream", {}).get("url", ""))
        frame_origin = ""
        parsed = urlparse(stream_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            frame_origin = f" {parsed.scheme}://{parsed.netloc}"
        policy = (
            "default-src 'self'; "
            "script-src 'self'; style-src 'self'; "
            # Albumcover von Spotify (i.scdn.co) und YouTube Music (googleusercontent).
            "img-src 'self' data: https://i.ytimg.com https://i.scdn.co "
            "https://*.googleusercontent.com https://yt3.ggpht.com https://*.mzstatic.com; "
            # YouTube fest erlaubt, damit ein im Menü gewählter Stream ohne Neuladen der Seite eingebettet werden darf.
            f"frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com{frame_origin}; "
            # Eigener Player: hls.js hängt den Stream als blob: an das video-Element und entpackt ihn in einem Worker.
            "media-src 'self' blob:; worker-src 'self' blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _send_body(self, data: bytes, content_type: str, status: int, *, head_only: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _json(self, value: Any, status: int = 200, *, head_only: bool = False) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_body(raw, "application/json; charset=utf-8", status, head_only=head_only)

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Request-Body fehlt")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültige Content-Length") from exc
        maximum = int(self._config().get("server", {}).get("max_request_bytes", 262144))
        if length > maximum:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request ist zu groß")
        if length <= 0:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Request-Body fehlt")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültiges JSON") from exc
        if not isinstance(value, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "JSON muss ein Objekt sein")
        return value

    def _token(self) -> str:
        header = self.headers.get("Authorization", "")
        return header[7:].strip() if header.startswith("Bearer ") else ""

    def _pin_required(self) -> bool:
        return bool(self._config().get("admin", {}).get("require_pin", True))

    def _authenticated(self) -> bool:
        if self.context.sessions.valid(self._token()):
            return True
        # Ohne PIN darf der Bildschirm selbst alles; Geräte im Heimnetz brauchen eine Sitzung
        # (Kopplungscode vom Bildschirm oder PIN).
        return not self._pin_required() and self._loopback_client()

    def _loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _requires_auth(self) -> bool:
        return not self._pin_required() or bool(self._config().get("admin", {}).get("pin_hash"))

    def _guard_admin(self) -> None:
        if not self._authenticated():
            raise RequestError(HTTPStatus.UNAUTHORIZED, "Admin-Anmeldung erforderlich")

    def _new_session(self) -> dict[str, Any]:
        token, expires = self.context.sessions.create()
        return {"ok": True, "token": token, "expires_in": expires}

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def _get_routes(self) -> dict[str, GetRoute]:
        return {
            "/api/health": self._get_health,
            "/api/state": self._get_state,
            "/api/admin/status": self._get_admin_status,
            "/api/config": self._get_config,
            "/api/station/search": self._get_station_search,
            "/api/live/search": self._get_live_search,
            "/api/location/search": self._get_location_search,
            "/api/location/timezone": self._get_location_timezone,
            "/api/music/ytmusic/login": self._get_ytmusic_login,
            "/api/music/spotify/pair": self._get_spotify_pair,
            "/api/bluetooth": self._get_bluetooth,
            "/api/stream/source": self._get_stream_source,
            "/api/music/recent": self._get_music_recent,
            "/api/player/queue": self._get_player_queue,
            "/api/player/level": self._get_player_level,
            "/api/pair/status": self._get_pair_status,
            "/api/system/info": self._get_system_info,
            "/api/gaming/status": self._get_gaming_status,
            "/api/gaming/folders": lambda params: self._gaming("GET", f"/folders?{urlencode({'path': _query_value(params, 'path')})}"),
            "/api/gaming/ports": lambda params: self._gaming("GET", f"/ports?{urlencode({'port': _query_value(params, 'port')})}"),
            "/api/gaming/job": lambda _params: self._gaming("GET", "/job"),
            "/api/gaming/roms": lambda _params: self._gaming("GET", "/roms"),
            "/api/gaming/access": self._get_gaming_access,
            "/api/gaming/screen": lambda _params: self._gaming("GET", "/screen"),
        }

    def _dispatch(self, *, head_only: bool) -> None:
        try:
            parsed = urlparse(self.path)
            route = self._get_routes().get(parsed.path)
            if parsed.path == music.SPOTIFY_CALLBACK_PATH:
                self._spotify_callback(parse_qs(parsed.query))
                return
            if parsed.path in {hls_proxy.PLAYLIST_PATH, hls_proxy.SEGMENT_PATH}:
                self._serve_hls(parsed.path, parse_qs(parsed.query), head_only=head_only)
                return
            if parsed.path.startswith("/theme/"):
                self._serve_theme_image(parsed.path.removeprefix("/theme/"), head_only=head_only)
                return
            if route is None:
                self._serve_static(parsed.path, head_only=head_only)
                return
            self._json(route(parse_qs(parsed.query)), head_only=head_only)
        except RequestError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            LOGGER.exception("Unbehandelter GET-Fehler für %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Interner Serverfehler")

    def _get_health(self, _params: QueryParams) -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "uptime_seconds": int(time.monotonic() - self.context.started_at),
            "state_ready": self.context.state.ready,
        }

    def _get_state(self, _params: QueryParams) -> dict[str, Any]:
        return self.context.state.snapshot()

    def _get_admin_status(self, _params: QueryParams) -> dict[str, Any]:
        return {
            "ok": True,
            "pin_required": self._pin_required(),
            "pin_configured": self._requires_auth(),
            "authenticated": self._authenticated(),
            # Handy im Heimnetz: Kopplungscode vom Bildschirm, PIN nur wenn dauerhaft erlaubt.
            "remote": not self._loopback_client(),
            "pin_login": self._loopback_client() or bool(self._config().get("server", {}).get("lan_access")),
        }

    def _get_config(self, _params: QueryParams) -> dict[str, Any]:
        if self._requires_auth():
            self._guard_admin()
        elif not self._loopback_client():
            raise RequestError(HTTPStatus.FORBIDDEN, "Erstsetup ist nur lokal erlaubt")
        return {
            "path": str(config_path()),
            "config": public_config(self._config()),
            "youtube_key_set": bool(youtube_api_key(self._config())),
            "spotify_connected": music.spotify_connected(),
            "ytmusic_connected": ytmusic.connected(),
        }

    def _get_stream_source(self, params: QueryParams) -> dict[str, Any]:
        """Wie der Kiosk den Stream abspielt: eigener Player (HLS oder Videodatei über den lokalen Proxy) oder eingebettet."""
        stream = self._config().get("stream", {})
        configured = str(stream.get("url", ""))
        url = _query_value(params, "url").strip() or configured
        favorites = {str(item.get("url", "")) for item in stream.get("favorites", [])}
        # Gemerkte Kameras darf der Kiosk bei einem Ausfall selbst als Ersatz abspielen.
        if url != configured and url not in favorites:
            # Eine noch nicht gespeicherte Adresse ausprobieren darf nur das Menü.
            self._guard_admin()
            if urlparse(url).scheme not in {"http", "https"}:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Stream-URL muss mit http:// oder https:// beginnen")
        quality = stream.get("quality", "auto")
        source = stream_source.resolve(
            url,
            refresh=_query_value(params, "refresh") == "1",
            height_limit=stream_source.max_height(quality),
            fps_limit=int(stream.get("max_fps", 30)),
        )
        if source["kind"] not in {"hls", "file"}:
            return source
        return {
            "kind": source["kind"],
            "url": hls_proxy.local_address(source["upstream"], playlist=source["kind"] == "hls"),
            "title": source.get("title", ""),
            "channel": source.get("channel", ""),
            "live": bool(source.get("live", True)),
            "expires_in": max(60, int(source["expires"] - time.time() - stream_source.REFRESH_MARGIN)),
        }

    def _get_station_search(self, params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        query = _query_value(params, "q")
        return {"query": query, "results": search_station(query)}

    def _get_live_search(self, params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        query = _query_value(params, "q")
        try:
            results = search_live(query, youtube_api_key(self._config()))
        except YouTubeError as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
        return {"query": query, "results": results}

    def _get_location_search(self, params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        query = _query_value(params, "q")
        try:
            results = search_places(query)
        except Exception as exc:
            LOGGER.warning("Ortssuche fehlgeschlagen: %s", exc)
            raise RequestError(HTTPStatus.BAD_GATEWAY, "Ortssuche ist gerade nicht erreichbar") from exc
        return {"query": query, "results": results}

    def _get_location_timezone(self, params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        try:
            latitude = float(_query_value(params, "lat"))
            longitude = float(_query_value(params, "lon"))
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "lat und lon müssen Zahlen sein") from exc
        try:
            timezone = timezone_for(latitude, longitude)
        except Exception as exc:
            LOGGER.warning("Zeitzonenabfrage fehlgeschlagen: %s", exc)
            raise RequestError(HTTPStatus.BAD_GATEWAY, "Zeitzone konnte nicht ermittelt werden") from exc
        return {"timezone": timezone}

    def _post_routes(self) -> dict[str, PostRoute]:
        return {
            "/api/admin/setup": self._post_admin_setup,
            "/api/admin/login": self._post_admin_login,
            "/api/admin/logout": self._post_admin_logout,
            "/api/config/save": self._post_config_save,
            "/api/music/spotify/authorize": self._post_spotify_authorize,
            "/api/music/spotify/disconnect": self._post_spotify_disconnect,
            "/api/music/ytmusic/login": self._post_ytmusic_login,
            "/api/music/ytmusic/cancel": self._post_ytmusic_cancel,
            "/api/music/ytmusic/disconnect": self._post_ytmusic_disconnect,
            "/api/music/spotify/pair": self._post_spotify_pair,
            "/api/music/spotify/unpair": self._post_spotify_unpair,
            "/api/player/search": self._post_player_search,
            "/api/player/playlists": self._post_player_playlists,
            "/api/player/play": self._post_player_play,
            "/api/player/control": self._post_player_control,
            "/api/bluetooth/scan": self._post_bluetooth_scan,
            "/api/bluetooth/connect": self._post_bluetooth_connect,
            "/api/bluetooth/disconnect": self._post_bluetooth_disconnect,
            "/api/bluetooth/remove": self._post_bluetooth_remove,
            "/api/radio/search": self._post_radio_search,
            "/api/radio/play": self._post_radio_play,
            "/api/player/sleep": self._post_player_sleep,
            "/api/player/open": self._post_player_open,
            "/api/player/like": self._post_player_like,
            "/api/player/add": self._post_player_add,
            "/api/pair/start": self._post_pair_start,
            "/api/pair/cancel": self._post_pair_cancel,
            "/api/pair/claim": self._post_pair_claim,
            "/api/display/power": self._post_display_power,
            "/api/gaming/folders": lambda body: self._gaming("POST", "/folders", {"path": str(body.get("path", "")), "name": str(body.get("name", ""))}),
            "/api/gaming/install": self._post_gaming_install,
            "/api/gaming/control": lambda body: self._gaming("POST", "/control", {"action": str(body.get("action", ""))}),
            "/api/gaming/screen": lambda body: self._gaming("POST", "/screen", {"action": str(body.get("action", ""))}),
            "/api/gaming/update": self._post_gaming_update,
            "/api/gaming/adopt": self._post_gaming_adopt,
        }

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._read_json()
            route = self._post_routes().get(urlparse(self.path).path)
            if route is None:
                raise RequestError(HTTPStatus.NOT_FOUND, "Unbekannter Endpunkt")
            self._json(route(body))
        except RequestError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            LOGGER.exception("Unbehandelter POST-Fehler für %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Interner Serverfehler")

    def do_PUT(self) -> None:  # noqa: N802
        """Nur für Spieldateien: der Inhalt wird in Stücken an den Gaming-Hilfsdienst durchgereicht."""
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/api/gaming/roms":
                raise RequestError(HTTPStatus.NOT_FOUND, "Unbekannter Endpunkt")
            self._guard_admin()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültige Content-Length") from exc
            if length <= 0:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Leere Datei")
            params = parse_qs(parsed.query)
            target = f"/roms?{urlencode({'name': _query_value(params, 'name'), 'replace': _query_value(params, 'replace')})}"
            self._json(self._gaming("PUT", target, stream=self._upload_chunks(length), length=length, timeout=600))
        except RequestError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            LOGGER.exception("Unbehandelter PUT-Fehler für %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Interner Serverfehler")

    def _upload_chunks(self, length: int) -> Iterator[bytes]:
        left = length
        while left > 0:
            chunk = self.rfile.read(min(1024 * 1024, left))
            if not chunk:
                return
            left -= len(chunk)
            yield chunk

    # ---------- Gaming: alles Docker-Bezogene erledigt der Hilfsdienst auf dem Pi
    def _gaming(self, method: str, target: str, body: dict[str, Any] | None = None, **options: Any) -> Any:
        self._guard_admin()
        try:
            return gaming.AgentClient().request(method, target, body, **options)
        except gaming.GamingError as exc:
            raise RequestError(exc.status, str(exc)) from exc

    def _get_gaming_status(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        client = gaming.AgentClient(timeout=20)
        settings = {"pause_camera": bool(self._config().get("gaming", {}).get("pause_camera"))}
        if not client.available():
            return {"ok": True, "agent": False, "install_command": gaming.INSTALL_COMMAND, "settings": settings}
        try:
            status = client.request("GET", "/status")
        except gaming.AgentUnavailable as exc:
            return {"ok": True, "agent": False, "agent_error": str(exc), "install_command": gaming.INSTALL_COMMAND, "settings": settings}
        except gaming.GamingError as exc:
            raise RequestError(exc.status, str(exc)) from exc
        return {**status, "agent": True, "settings": settings}

    def _gaming_hosts(self, lan_ip: str) -> list[str]:
        """Adressen, unter denen Geräte im Heimnetz die Gaming-Oberfläche erreichen.

        Die IP des Pi (vom Hilfsdienst, der auf dem Pi läuft) geht überall; der Name aus der Anfrage
        zusätzlich, weil das Gerät ihn offensichtlich kennt. Am Bildschirm selbst zählt nur die IP:
        Der Kiosk läuft im Container und kennt weder localhost des Pi noch Namen wie raspberrypi.local.
        """
        hosts: list[str] = []
        if not self._loopback_client():
            hosts.append(urlparse(f"//{self.headers.get('Host', '')}").hostname or "")
        hosts.append(lan_ip)
        if not any(hosts):
            hosts.append(urlparse(pairing.public_url(self._config())).hostname or "")
        return [host for index, host in enumerate(hosts) if host and host not in hosts[:index]]

    def _get_gaming_access(self, _params: QueryParams) -> dict[str, Any]:
        access = self._gaming("GET", "/access")
        port = int(access.get("port") or 0)
        lan_ip = str(access.get("address") or "")
        urls = [f"https://{host}:{port}/" for host in self._gaming_hosts(lan_ip)]
        # QR-Code mit der IP: Handys lösen Namen wie raspberrypi.local oft nicht auf.
        scan = f"https://{lan_ip}:{port}/" if lan_ip else (urls[0] if urls else "")
        qr = str(segno.make(scan, error="m").svg_data_uri(scale=6, border=2, dark="#0c1821", light="#fff")) if scan else ""
        return {"ok": True, "url": urls[0] if urls else "", "urls": urls, "scan_url": scan, "user": access.get("user", ""),
                "password": access.get("password", ""), "running": bool(access.get("running")), "qr": qr, "on_pi": self._loopback_client()}

    @staticmethod
    def _confirmed(body: dict[str, Any]) -> None:
        # Installieren, Aktualisieren und Übernehmen verändern den Pi: nur nach ausdrücklicher Bestätigung.
        if body.get("confirm") is not True:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Bitte zuerst bestätigen")

    def _post_gaming_install(self, body: dict[str, Any]) -> Any:
        self._confirmed(body)
        try:
            port = int(body.get("port") or 3001)
        except (TypeError, ValueError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültiger Port") from exc
        return self._gaming("POST", "/install", {"folder": str(body.get("folder", "")), "port": port, "gpu": bool(body.get("gpu"))})

    def _post_gaming_update(self, body: dict[str, Any]) -> Any:
        self._confirmed(body)
        return self._gaming("POST", "/update", {"rollback": bool(body.get("rollback"))})

    def _post_gaming_adopt(self, body: dict[str, Any]) -> Any:
        self._confirmed(body)
        return self._gaming("POST", "/adopt", {"name": str(body.get("name", ""))})

    def _post_admin_setup(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._requires_auth():
            raise RequestError(HTTPStatus.FORBIDDEN, "Admin-PIN ist bereits eingerichtet")
        if not self._loopback_client():
            raise RequestError(HTTPStatus.FORBIDDEN, "Erstsetup ist nur lokal erlaubt")
        try:
            encoded = hash_pin(str(body.get("pin", "")))
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        config = self._config()
        config.setdefault("admin", {})["pin_hash"] = encoded
        saved = validate_config(config)
        save_config(saved)
        self.context.replace_config(saved)
        return self._new_session()

    def _post_admin_login(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self._loopback_client() and not self._config().get("server", {}).get("lan_access"):
            raise RequestError(HTTPStatus.FORBIDDEN, "Anmeldung mit PIN aus dem Heimnetz ist aus; bitte den Kopplungscode vom Bildschirm eingeben")
        encoded = str(self._config().get("admin", {}).get("pin_hash", ""))
        if not encoded:
            raise RequestError(HTTPStatus.CONFLICT, "Admin-PIN wurde noch nicht eingerichtet")
        if not verify_pin(str(body.get("pin", "")), encoded):
            raise RequestError(HTTPStatus.UNAUTHORIZED, "PIN ist falsch")
        return self._new_session()

    def _post_admin_logout(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        self.context.sessions.revoke(self._token())
        return {"ok": True}

    def _post_config_save(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        candidate = body.get("config")
        if not isinstance(candidate, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "config fehlt")
        current = self._config()
        candidate.setdefault("admin", {})["pin_hash"] = current.get("admin", {}).get("pin_hash", "")
        candidate["admin"]["require_pin"] = current.get("admin", {}).get("require_pin", True)
        # Der Schlüssel wird nie an die Oberfläche geschickt; leer heißt „unverändert lassen“.
        youtube = candidate.setdefault("youtube", {})
        if not str(youtube.get("api_key", "")).strip():
            youtube["api_key"] = current.get("youtube", {}).get("api_key", "")
        try:
            validated = validate_config(candidate)
            save_config(validated)
        except ConfigError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        self.context.replace_config(validated)
        return {"ok": True, "path": str(config_path()), "config": public_config(validated)}

    def _post_spotify_authorize(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        client_id = str(body.get("client_id", "")).strip()
        redirect_uri = str(body.get("redirect_uri", "")).strip()
        parsed = urlparse(redirect_uri)
        if parsed.scheme not in {"http", "https"} or parsed.path != music.SPOTIFY_CALLBACK_PATH or parsed.query:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültige Weiterleitungsadresse")
        # Die Client-ID wird gleich mitgespeichert, damit sie nach dem Login nicht verloren geht.
        config = self._config()
        config.setdefault("music", {}).setdefault("spotify", {})["client_id"] = client_id
        try:
            validated = validate_config(config)
        except ConfigError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if not client_id:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Bitte zuerst die Spotify-Client-ID eintragen")
        if validated != self._config():
            save_config(validated)
            self.context.replace_config(validated)
        return {"ok": True, "url": music.spotify_authorize_url(client_id, redirect_uri)}

    def _post_spotify_disconnect(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        music.spotify_disconnect()
        self.context.state.refresh_soon("music")
        return {"ok": True}

    def _post_ytmusic_login(self, _body: dict[str, Any]) -> dict[str, Any]:
        """Öffnet auf dem Bildschirm ein Fenster mit der Google-Anmeldung für YouTube Music."""
        self._guard_admin()
        try:
            return {"ok": True, **ytmusic.start_login(self._config())}
        except ytmusic.YTMusicError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def _get_ytmusic_login(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        status = ytmusic.login_status()
        if status.get("status") == "connected":
            self.context.state.refresh_soon("music")
        return {"ok": True, **status}

    def _post_ytmusic_cancel(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        ytmusic.cancel_login()
        return {"ok": True}

    def _post_ytmusic_disconnect(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        ytmusic.disconnect()
        self.context.state.refresh_soon("music")
        return {"ok": True}

    def _get_spotify_pair(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        return {"ok": True, **spotify_connect.status()}

    def _post_spotify_pair(self, _body: dict[str, Any]) -> dict[str, Any]:
        """Startet librespot mit Gerätecode; das Menü zeigt die Kopplungsadresse als QR-Code."""
        self._guard_admin()
        try:
            return {"ok": True, **spotify_connect.pair()}
        except spotify_connect.SpotifyConnectError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def _post_spotify_unpair(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        spotify_connect.unpair()
        return {"ok": True}

    @staticmethod
    def _service(body: dict[str, Any]) -> str:
        service = str(body.get("service", ""))
        if service not in {"ytmusic", "spotify"}:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Unbekannter Musikdienst")
        return service

    def _library(self, body: dict[str, Any]) -> MusicLibrary:
        """Strategie je Musikdienst: YouTube Music oder Spotify hinter derselben Schnittstelle."""
        if self._service(body) == "spotify":
            return SpotifyLibrary(music._spotify_api)
        return YouTubeMusicLibrary()

    def _browse(self, work: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "results": work()}
        except (LibraryError, music.MusicError, player.PlayerError, ytmusic.YTMusicError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc

    def _post_player_search(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        library = self._library(body)
        result = self._browse(lambda: library.search(str(body.get("query", ""))[:120]))
        if library.service == "ytmusic":
            player.prefetch([item["id"] for item in result["results"] if item.get("kind") in {"song", "video"}][:1])
        return result

    def _post_player_playlists(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        library = self._library(body)
        return self._browse(library.playlists)

    def _post_player_open(self, body: dict[str, Any]) -> dict[str, Any]:
        """Seite eines Albums, einer Playlist oder eines Künstlers mit allen Titeln."""
        self._guard_admin()
        library = self._library(body)
        kind, identifier = str(body.get("kind", "")), str(body.get("id", ""))[:200]
        if kind not in {"album", "playlist", "artist"}:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Nur Alben, Playlists und Künstler lassen sich öffnen")
        result = self._browse(lambda: library.open(kind, identifier))
        if library.service == "ytmusic":
            # Die ersten Titel vorbereiten: wer eine Seite öffnet, spielt meist gleich etwas davon.
            player.prefetch([track["id"] for track in result["results"].get("tracks", [])[:2]])
        return result

    def _post_player_like(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        library = self._library(body)
        track_id, liked = str(body.get("id", ""))[:200], bool(body.get("liked", True))
        result = self._browse(lambda: library.like(track_id, liked))
        player.default_player().mark_liked(track_id, liked)
        music.remember_liked(track_id, liked)
        self.context.state.refresh_soon("music")
        return result

    def _post_player_add(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        library = self._library(body)
        return self._browse(lambda: library.add_to_playlist(str(body.get("playlist", ""))[:200], str(body.get("id", ""))[:200]))

    def _get_player_queue(self, params: QueryParams) -> dict[str, Any]:
        """Spotify-Warteschlange auf Nachfrage (YouTube Music und Radio stehen im Musik-Zustand)."""
        if not self._loopback_client():
            self._guard_admin()
        return self._browse(music.spotify_queue)

    def _get_player_level(self, _params: QueryParams) -> dict[str, Any]:
        """Pegel des Pi-Players für die bewegte Equalizer-Anzeige (einige Male pro Sekunde)."""
        if not self._loopback_client():
            self._guard_admin()
        return {"ok": True, "level": player.level()}

    def _stop_spotify_on_pi(self) -> None:
        current = self.context.state.snapshot().get("music", {})
        if current.get("controls") == "spotify" and current.get("device") == "pi" and not current.get("paused"):
            music.spotify_control("stop")

    def _post_player_play(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        kind, identifier = str(body.get("kind", "")), str(body.get("id", ""))[:200]
        try:
            start = max(0, int(body.get("start") or 0))
        except (TypeError, ValueError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültige Startposition") from exc
        shuffle = bool(body.get("shuffle"))
        listening.remember({
            "source": self._service(body), "kind": kind, "id": identifier,
            "title": body.get("title"), "subtitle": body.get("subtitle"), "cover": body.get("cover"),
        })
        try:
            if self._service(body) == "spotify":
                # Nur ein Player gleichzeitig: YouTube Music auf dem Pi anhalten.
                if player.active():
                    player.control("stop")
                music.spotify_play(kind, identifier, start, shuffle)
            else:
                self._stop_spotify_on_pi()
                single = Track(id=identifier, title=str(body.get("title") or "")[:200], artist=str(body.get("subtitle") or "")[:200], cover=str(body.get("cover") or "")[:500])
                player.play(kind, identifier, start, shuffle, single)
        except (music.MusicError, player.PlayerError, ytmusic.YTMusicError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
        player.full_output_volume()
        return self._fresh_music(self._service(body) == "ytmusic")

    def _post_player_control(self, body: dict[str, Any]) -> dict[str, Any]:
        # Die Knöpfe auf der Songkarte gehen am Pi-Bildschirm selbst auch ohne Admin-Anmeldung.
        if not self._loopback_client():
            self._guard_admin()
        target, action = str(body.get("target", "")), str(body.get("action", ""))
        LOGGER.info("Wiedergabe steuern: %s %s (von %s)", target, action, self.client_address[0])
        try:
            if target == "pi" and action in {"next", "previous"} and player.current_station():
                self._switch_station(1 if action == "next" else -1)
            elif target == "pi":
                player.control(action, body.get("value"))
            elif target == "spotify":
                music.spotify_control(action, body.get("value"))
            else:
                raise RequestError(HTTPStatus.BAD_REQUEST, "Unbekanntes Ziel")
        except (music.MusicError, player.PlayerError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
        return self._fresh_music(target == "pi")

    def _fresh_music(self, local: bool) -> dict[str, Any]:
        """Der Pi-Player kennt seinen neuen Zustand sofort; Spotify braucht einen Moment und kommt mit der nächsten Runde."""
        if local:
            return {"ok": True, "music": self.context.state.refresh_now("music")}
        self.context.state.refresh_soon("music")
        return {"ok": True}

    def _switch_station(self, step: int) -> None:
        """Vor und Zurück beim Radio: nächster bzw. vorheriger Lieblingssender."""
        favorites = self._config().get("music", {}).get("radio", {}).get("favorites", [])
        if not favorites:
            raise player.PlayerError("Noch keine Lieblingssender gemerkt")
        current = (player.current_station() or {}).get("uuid")
        uuids = [item["uuid"] for item in favorites]
        index = uuids.index(current) + step if current in uuids else 0
        station = favorites[index % len(favorites)]
        player.play_radio(station)
        listening.remember({"source": "radio", "kind": "station", "id": station["uuid"], "title": station["name"], "subtitle": station.get("country", ""), "station": station})

    def _post_radio_search(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        try:
            return {"ok": True, "results": radio.search(str(body.get("query", ""))[:80], str(body.get("filter", "")))}
        except radio.RadioError as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc

    def _post_radio_play(self, body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        raw = body.get("station")
        station = radio.station({
            "stationuuid": raw.get("uuid"), "name": raw.get("name"), "url": raw.get("url"), "country": raw.get("country"),
            "codec": raw.get("codec"), "bitrate": raw.get("bitrate"), "tags": ",".join(raw.get("tags") or []),
        }) if isinstance(raw, dict) else None
        if station is None:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültiger Sender")
        try:
            self._stop_spotify_on_pi()
            player.play_radio(station)
        except (music.MusicError, player.PlayerError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
        radio.count_click(station["uuid"])
        listening.remember({"source": "radio", "kind": "station", "id": station["uuid"], "title": station["name"], "subtitle": station["country"], "station": station})
        player.full_output_volume()
        return {**self._fresh_music(True), "station": station}

    def _post_player_sleep(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self._loopback_client():
            self._guard_admin()
        try:
            minutes = max(0, min(600, int(body.get("minutes", 0))))
        except (TypeError, ValueError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Ungültige Minutenzahl") from exc

        def switch_off() -> None:
            if player.active():
                player.control("stop")
            current = self.context.state.snapshot().get("music", {})
            if current.get("controls") == "spotify" and not current.get("paused"):
                music.spotify_control("stop")
            self.context.state.refresh_soon("music")

        end = listening.set_sleep(minutes, str(body.get("label", ""))[:20], switch_off)
        self.context.state.refresh_soon("music")
        return {"ok": True, "sleep_end": int(end * 1000)}

    def _get_music_recent(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        return {"ok": True, "results": listening.recent()}

    def _post_pair_start(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        return {"ok": True, **pairing.start(self._config())}

    def _post_pair_cancel(self, _body: dict[str, Any]) -> dict[str, Any]:
        self._guard_admin()
        pairing.cancel()
        return {"ok": True}

    def _get_pair_status(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        return {"ok": True, **pairing.status(self._config())}

    def _post_pair_claim(self, body: dict[str, Any]) -> dict[str, Any]:
        if not pairing.claim(str(body.get("code", ""))[:12]):
            raise RequestError(HTTPStatus.FORBIDDEN, "Code falsch oder abgelaufen; am Bildschirm einen neuen erzeugen")
        LOGGER.info("Handy gekoppelt: %s", self.client_address[0])
        return self._new_session()

    def _get_system_info(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        try:
            saved = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(config_path().stat().st_mtime))
        except OSError:
            saved = ""
        return {
            "ok": True,
            "version": __version__,
            "uptime_seconds": int(time.monotonic() - self.context.started_at),
            "config_saved": saved,
            "backlight": display.backlight_available(),
            "public_url": pairing.public_url(self._config()),
            "remote": not self._loopback_client(),
        }

    def _post_display_power(self, body: dict[str, Any]) -> dict[str, Any]:
        # Nur der Bildschirm selbst schaltet sein Licht (Nachtmodus).
        if not self._loopback_client():
            raise RequestError(HTTPStatus.FORBIDDEN, "Nur am Bildschirm selbst möglich")
        on = bool(body.get("on", True))
        if not on and gaming.screen_running():
            # Wird am Bildschirm gespielt, schaltet der Nachtmodus das Licht nicht ab.
            return {"ok": True, "switched": False, "gaming": True}
        return {"ok": True, "switched": display.set_backlight(on)}

    def _get_bluetooth(self, _params: QueryParams) -> dict[str, Any]:
        self._guard_admin()
        return {"ok": True, **bluetooth.status()}

    def _bluetooth(self, action: Callable[..., None], *arguments: str) -> dict[str, Any]:
        self._guard_admin()
        try:
            action(*arguments)
        except bluetooth.BluetoothError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return {"ok": True}

    def _post_bluetooth_scan(self, _body: dict[str, Any]) -> dict[str, Any]:
        return self._bluetooth(bluetooth.scan)

    def _post_bluetooth_connect(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._bluetooth(bluetooth.connect, str(body.get("mac", "")))

    def _post_bluetooth_disconnect(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._bluetooth(bluetooth.disconnect, str(body.get("mac", "")))

    def _post_bluetooth_remove(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._bluetooth(bluetooth.remove, str(body.get("mac", "")))

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _spotify_callback(self, params: QueryParams) -> None:
        """Rücksprung von Spotify; geschützt über den einmaligen state-Wert aus der Anmeldung."""
        error = _query_value(params, "error")
        try:
            if error:
                raise music.MusicError("Anmeldung abgebrochen" if error == "access_denied" else f"Spotify meldet: {error}")
            music.spotify_finish(_query_value(params, "state"), _query_value(params, "code"))
        except (music.MusicError, OSError) as exc:
            LOGGER.warning("Spotify-Anmeldung fehlgeschlagen: %s", exc)
            self._redirect("/?" + urlencode({"music": "error", "reason": str(exc)[:160]}))
            return
        self.context.state.refresh_soon("music")
        self._redirect("/?music=connected")

    def _serve_static(self, path: str, *, head_only: bool) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        relative = Path(path.lstrip("/"))
        if ".." in relative.parts:
            raise RequestError(HTTPStatus.FORBIDDEN, "Ungültiger Pfad")
        target = web_directory() / relative
        if not target.is_file():
            raise RequestError(HTTPStatus.NOT_FOUND, "Datei nicht gefunden")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self._send_body(target.read_bytes(), content_type, HTTPStatus.OK, head_only=head_only)

    def _serve_hls(self, path: str, params: QueryParams, *, head_only: bool) -> None:
        try:
            url = hls_proxy.verified_url(_query_value(params, "u"), _query_value(params, "s"))
            if path == hls_proxy.PLAYLIST_PATH:
                stream = self._config().get("stream", {})
                body = hls_proxy.fetch_playlist(url, stream_source.max_height(stream.get("quality", "auto")), int(stream.get("max_fps", 30)))
                self._send_body(body, "application/vnd.apple.mpegurl", HTTPStatus.OK, head_only=head_only)
                return
            upstream = hls_proxy.open_upstream(url, byte_range=hls_proxy.bounded_range(self.headers.get("Range", "")))
        except hls_proxy.ProxyError as exc:
            raise RequestError(exc.status, str(exc)) from exc
        with upstream:
            # Videodateien kommen in Stücken (206); so kann der Player springen und die Schleife neu starten.
            partial = upstream.status == HTTPStatus.PARTIAL_CONTENT
            self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
            self.send_header("Content-Type", upstream.headers.get("Content-Type") or "video/mp2t")
            for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if head_only:
                return
            try:
                while chunk := upstream.read(hls_proxy.CHUNK_BYTES):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                LOGGER.debug("Player hat ein Segment abgebrochen")

    def _serve_theme_image(self, name: str, *, head_only: bool) -> None:
        target = theme_image(name)
        if target is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "Datei nicht gefunden")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_body(target.read_bytes(), content_type, HTTPStatus.OK, head_only=head_only)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.client_address[0], fmt % args)


def create_server(config: dict[str, Any], state: StateManager) -> StorchServer:
    host = str(config["server"]["host"])
    port = int(config["server"]["port"])
    return StorchServer((host, port), ServerContext(config, state))


def run_server(server: StorchServer) -> None:
    server.serve_forever(poll_interval=0.5)
