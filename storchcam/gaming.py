"""StorchCam-Seite der Gaming-Verwaltung: spricht den Gaming-Hilfsdienst auf dem Pi über seinen Unix-Socket an.

Der Container selbst bekommt keinen Docker-Zugriff. Alles, was Docker betrifft, erledigt der
Hilfsdienst (gaming_agent.py) mit seiner festen Aktionsliste; hier wird nur weitergereicht.
"""

from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .environment import env

DEFAULT_SOCKET = "/run/gaming-agent/agent.sock"
# Im StorchCam-Ordner auf dem Pi ausführen (dort, wo docker-compose.yml liegt).
INSTALL_COMMAND = "sudo ./scripts/gaming/install-agent.sh"


class GamingError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class AgentUnavailable(GamingError):
    """Der Hilfsdienst ist nicht installiert oder läuft nicht."""


def socket_path() -> Path:
    return Path(env("GAMING_SOCKET", DEFAULT_SOCKET))


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("gaming-agent", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        family = getattr(socket, "AF_UNIX", None)
        if family is None:  # Windows: dort gibt es den Hilfsdienst nicht
            raise ConnectionRefusedError("Unix-Sockets fehlen auf diesem System")
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self._path))


class AgentClient:
    def __init__(self, path: Path | None = None, timeout: float = 30) -> None:
        self.path = path or socket_path()
        self.timeout = timeout

    def available(self) -> bool:
        return self.path.exists()

    def request(self, method: str, target: str, body: dict[str, Any] | None = None, *,
                stream: Iterator[bytes] | None = None, length: int = 0, timeout: float | None = None) -> Any:
        if not self.available():
            raise AgentUnavailable("Der Gaming-Hilfsdienst ist auf dem Pi nicht eingerichtet", 503)
        connection = _UnixConnection(self.path, timeout or self.timeout)
        try:
            if stream is not None:
                connection.putrequest(method, target)
                connection.putheader("Content-Type", "application/octet-stream")
                connection.putheader("Content-Length", str(length))
                connection.endheaders()
                for chunk in stream:
                    connection.send(chunk)
            else:
                payload = json.dumps(body).encode("utf-8") if body is not None else None
                headers = {"Content-Type": "application/json"} if payload is not None else {}
                connection.request(method, target, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise AgentUnavailable("Der Gaming-Hilfsdienst läuft nicht", 503) from exc
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise GamingError(f"Gaming-Hilfsdienst antwortet nicht: {exc}", 504) from exc
        finally:
            connection.close()
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GamingError("Ungültige Antwort vom Gaming-Hilfsdienst") from exc
        if response.status >= 400:
            raise GamingError(str(value.get("error") or "Gaming-Hilfsdienst meldet einen Fehler"), response.status)
        return value


def brief(client: AgentClient | None = None) -> dict[str, Any]:
    """Kurzer Stand für den Kiosk (Kamera pausieren, solange Gaming läuft); nie eine Ausnahme."""
    client = client or AgentClient(timeout=8)
    if not client.available():
        return {"available": False, "running": False}
    try:
        status = client.request("GET", "/status")
    except GamingError as exc:
        return {"available": False, "running": False, "error": str(exc)[:160]}
    container = status.get("container") or {}
    return {"available": True, "running": bool(container.get("running")), "reachable": bool(container.get("reachable"))}


def screen_running(client: AgentClient | None = None) -> bool:
    """Läuft RetroArch gerade direkt auf dem Bildschirm des Pi? Im Zweifel nein, nie eine Ausnahme."""
    client = client or AgentClient(timeout=3)
    if not client.available():
        return False
    try:
        return bool(client.request("GET", "/screen").get("running"))
    except GamingError:
        return False
