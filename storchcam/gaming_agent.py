#!/usr/bin/env python3
"""Gaming-Hilfsdienst für StorchCam.

Läuft direkt auf dem Raspberry Pi (nicht im Container) und verwaltet genau einen Gaming-Container
mit RetroArch (lscr.io/linuxserver/retroarch). StorchCam spricht ihn über einen Unix-Socket an.

Sicherheitsmodell:
- Kein Netzwerkport. Der Socket gehört dem Benutzer des Dienstes; der StorchCam-Container
  (Benutzer 1000:1000) erreicht ihn über ein eingebundenes Verzeichnis.
- Nur eine feste Liste von Aktionen. Docker wird ausschließlich mit Argumentlisten aufgerufen,
  nie über eine Shell, und nie mit Werten, die nicht vorher geprüft wurden.
- Ordner nur unterhalb freigegebener Wurzeln, keine Systemverzeichnisse, keine Symlink-Ausbrüche.
- Verändert nur den eigenen Container (storchcam-gaming) oder einen ausdrücklich übernommenen
  RetroArch-Container. Andere Container werden höchstens gelesen.
- Löscht weder Container noch Daten. Ein Update sichert die vorige Version als Image-Tag.

Nur Python-Standardbibliothek, damit die Datei allein auf den Pi kopiert werden kann.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    raise ImportError("Der Gaming-Hilfsdienst läuft nur unter Linux (Raspberry Pi OS)")
assert sys.platform != "win32"  # sagt mypy, dass der Rest nur für Linux geprüft wird

LOGGER = logging.getLogger("storchcam-gaming")
AGENT_VERSION = "2"  # 2: RetroArch direkt am Bildschirm des Pi
IMAGE = "lscr.io/linuxserver/retroarch:latest"
IMAGE_MATCH = "linuxserver/retroarch"


@dataclass(frozen=True)
class Names:
    """Namen des verwalteten Containers, seines Compose-Projekts und der gesicherten Vorversion."""

    container: str
    project: str
    previous: str
    label: str


NAMES = Names("storchcam-gaming", "storchcam-gaming", "storchcam-gaming/retroarch:previous", "de.storchcam.gaming")
# Bis Version 0.x hieß das Projekt BI-StorchCam. So eingerichtete Container behalten ihren Namen und werden weiter verwaltet.
LEGACY_NAMES = Names("bi-storchcam-gaming", "bi-storchcam-gaming", "bi-storchcam-gaming/retroarch:previous", "de.bi-storchcam.gaming")
CONTAINER, PROJECT, PREVIOUS_TAG, MANAGED_LABEL = NAMES.container, NAMES.project, NAMES.previous, NAMES.label
GUI_PORT = 3001  # HTTPS-Oberfläche (Selkies) im Container
DEFAULT_PORT = 3001
READY_SECONDS = 240
MAX_ROM_BYTES = 4 * 1024**3
SUBFOLDERS = ("config", "roms", "bios", "saves", "states")
# Wohin die Unterordner im Container zeigen. /config ist laut Image-Doku das Home-Verzeichnis;
# die übrigen Pfade sind die üblichen RetroArch-Standardordner unter ~/.config/retroarch.
MOUNTS = (
    ("config", "/config"),
    ("roms", "/config/roms"),
    ("bios", "/config/.config/retroarch/system"),
    ("saves", "/config/.config/retroarch/saves"),
    ("states", "/config/.config/retroarch/states"),
)
FORBIDDEN = ("/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var", "/tmp")
FOLDER_NAME = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß ._-]{0,63}$")
# Endung → Konsole. Nur zur Anzeige; ob RetroArch einen passenden Kern hat, entscheidet RetroArch.
ROM_SYSTEMS = {
    ".nes": "NES", ".fds": "NES", ".sfc": "SNES", ".smc": "SNES", ".gb": "Game Boy", ".gbc": "Game Boy Color",
    ".gba": "Game Boy Advance", ".n64": "Nintendo 64", ".z64": "Nintendo 64", ".v64": "Nintendo 64",
    ".nds": "Nintendo DS", ".md": "Mega Drive", ".gen": "Mega Drive", ".smd": "Mega Drive", ".sms": "Master System",
    ".gg": "Game Gear", ".32x": "Sega 32X", ".pce": "PC Engine", ".a26": "Atari 2600", ".a78": "Atari 7800",
    ".lnx": "Atari Lynx", ".ws": "WonderSwan", ".wsc": "WonderSwan Color", ".ngp": "Neo Geo Pocket", ".ngc": "Neo Geo Pocket Color",
    ".cue": "PlayStation / CD", ".chd": "CD-Image", ".pbp": "PlayStation", ".m3u": "Mehrere Discs", ".iso": "CD/DVD-Image",
    ".zip": "Archiv (z. B. Arcade)", ".7z": "Archiv (z. B. Arcade)",
}
ROM_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,180}$")


class AgentError(RuntimeError):
    """Verständlicher Grund, warum eine Aktion nicht geht; status wird als HTTP-Status zurückgegeben."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class AgentConfig:
    allowed_roots: list[str] = field(default_factory=lambda: [str(Path.home()), "/mnt", "/media"])
    state_dir: str = "/var/lib/storchcam-gaming"
    socket_path: str = "/var/lib/storchcam-gaming/socket/agent.sock"
    image: str = IMAGE
    timezone: str = "Europe/Berlin"
    docker: str = "docker"
    # Spielen direkt am Bildschirm des Pi: RetroArch aus den Paketen des Systems (install-agent.sh --screen).
    retroarch: str = "retroarch"
    cores_dir: str = ""  # leer: Ordner der Systempakete suchen
    runtime_dir: str = ""  # leer: /run/user/<eigene UID>, dort liegt der Wayland-Socket der Desktop-Sitzung
    wayland_display: str = "wayland-0"

    @classmethod
    def load(cls, path: Path | None) -> AgentConfig:
        if path is None or not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {key: raw[key] for key in cls.__dataclass_fields__ if key in raw}
        return cls(**known)


# ---------------------------------------------------------------- Docker

class Docker:
    """Docker-Befehle als feste Argumentlisten. ``run`` lässt sich für Tests austauschen."""

    def __init__(self, binary: str = "docker", run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 popen: Callable[..., Any] = subprocess.Popen) -> None:
        self.binary = binary
        self._run = run
        self._popen = popen

    def call(self, *arguments: str, timeout: float = 60) -> str:
        try:
            result = self._run([self.binary, *arguments], capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError as exc:
            raise AgentError("Docker ist auf dem Pi nicht installiert", HTTPStatus.SERVICE_UNAVAILABLE) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"Docker antwortet nicht ({arguments[0]})", HTTPStatus.GATEWAY_TIMEOUT) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip().splitlines()
            raise AgentError(message[-1][:300] if message else f"docker {arguments[0]} fehlgeschlagen", HTTPStatus.BAD_GATEWAY)
        return result.stdout

    def version(self) -> str:
        return self.call("version", "--format", "{{.Server.Version}}", timeout=10).strip()

    def containers(self) -> list[dict[str, Any]]:
        lines = self.call("ps", "-a", "--no-trunc", "--format", "{{json .}}", timeout=20).splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def inspect(self, name: str) -> dict[str, Any] | None:
        try:
            found = json.loads(self.call("inspect", "--type", "container", name, timeout=20))
        except AgentError:
            return None
        return found[0] if found else None

    def image_id(self, reference: str) -> str:
        try:
            return self.call("image", "inspect", "--format", "{{.Id}}", reference, timeout=20).strip()
        except AgentError:
            return ""

    def pull(self, image: str, progress: Callable[[str], None]) -> None:
        """Image laden und jede Ausgabezeile weiterreichen (echter Fortschritt, nichts simuliert)."""
        try:
            process = self._popen([self.binary, "pull", image], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError as exc:
            raise AgentError("Docker ist auf dem Pi nicht installiert", HTTPStatus.SERVICE_UNAVAILABLE) from exc
        last = ""
        for line in process.stdout:
            line = line.strip()
            if line:
                last = line
                progress(line[:200])
        if process.wait() != 0:
            raise AgentError(f"Image ließ sich nicht laden: {last[:200]}", HTTPStatus.BAD_GATEWAY)

    def compose_up(self, compose_file: Path, project: str = PROJECT) -> None:
        self.call("compose", "-p", project, "-f", str(compose_file), "up", "-d", timeout=300)

    def start(self, name: str) -> None:
        self.call("start", name, timeout=60)

    def stop(self, name: str) -> None:
        self.call("stop", name, timeout=90)

    def tag(self, source: str, target: str) -> None:
        self.call("tag", source, target, timeout=30)


# ---------------------------------------------------------------- Ordner

class Folders:
    """Ordnerwahl auf dem Pi: nur unterhalb freigegebener Wurzeln, ohne Systempfade und Symlink-Ausbrüche."""

    def __init__(self, roots: list[str]) -> None:
        self.roots = [Path(root).resolve() for root in roots if Path(root).is_dir()]

    def allowed(self, path: Path) -> bool:
        if str(path) in FORBIDDEN:
            return False
        return any(path == root or root in path.parents for root in self.roots)

    def resolve(self, raw: str) -> Path:
        if not raw or "\x00" in raw:
            raise AgentError("Kein Ordner angegeben")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise AgentError("Bitte einen vollständigen Pfad auf dem Pi angeben")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, RuntimeError) as exc:
            raise AgentError("Diesen Ordner gibt es nicht", HTTPStatus.NOT_FOUND) from exc
        if not resolved.is_dir():
            raise AgentError("Das ist kein Ordner")
        if not self.allowed(resolved):
            raise AgentError("Dieser Ordner ist nicht freigegeben", HTTPStatus.FORBIDDEN)
        return resolved

    @staticmethod
    def describe(path: Path) -> dict[str, Any]:
        usage = shutil.disk_usage(path)
        return {"path": str(path), "free_bytes": usage.free, "total_bytes": usage.total, "writable": os.access(path, os.W_OK)}

    def listing(self, raw: str) -> dict[str, Any]:
        if not raw:
            return {"path": "", "parent": "", "roots": [self.describe(root) for root in self.roots], "entries": []}
        folder = self.resolve(raw)
        entries = []
        try:
            children = sorted(folder.iterdir(), key=lambda child: child.name.lower())
        except PermissionError as exc:
            raise AgentError("Keine Leserechte für diesen Ordner", HTTPStatus.FORBIDDEN) from exc
        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                real = child.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if self.allowed(real):
                entries.append({"name": child.name, "path": str(real), "writable": os.access(real, os.W_OK), "gaming": looks_like_gaming(real)})
        parent = folder.parent
        return {**self.describe(folder), "parent": str(parent) if self.allowed(parent) and parent != folder else "",
                "roots": [str(root) for root in self.roots], "entries": entries[:300], "gaming": looks_like_gaming(folder)}

    def create(self, parent_raw: str, name: str) -> dict[str, Any]:
        parent = self.resolve(parent_raw)
        clean = name.strip()
        if not FOLDER_NAME.match(clean) or ".." in clean:
            raise AgentError("Ordnernamen bitte nur mit Buchstaben, Ziffern, Leerzeichen, Punkt, Minus und Unterstrich")
        target = parent / clean
        if target.exists():
            raise AgentError("Diesen Ordner gibt es schon", HTTPStatus.CONFLICT)
        if not os.access(parent, os.W_OK):
            raise AgentError("Keine Schreibrechte in diesem Ordner", HTTPStatus.FORBIDDEN)
        target.mkdir(mode=0o755)
        return self.describe(target)


def looks_like_gaming(folder: Path) -> bool:
    """Ordner, der schon einmal für Gaming eingerichtet wurde (vorhandene Daten bleiben erhalten)."""
    return all((folder / name).is_dir() for name in ("config", "roms"))


# ---------------------------------------------------------------- Am Bildschirm des Pi spielen

SCREEN_INSTALL_COMMAND = "sudo ./scripts/gaming/install-agent.sh --screen"
# Hier legen die libretro-Pakete von Debian bzw. Raspberry Pi OS ihre Emulator-Kerne ab.
CORE_DIRS = ("/usr/lib/aarch64-linux-gnu/libretro", "/usr/lib/x86_64-linux-gnu/libretro", "/usr/lib/libretro")
CORE_INFO_DIR = "/usr/share/libretro/info"
AUTOCONFIG_DIRS = ("/usr/share/libretro/autoconfig", "/usr/share/retroarch/autoconfig")
SYSTEM_CONFIG = Path("/etc/retroarch.cfg")
STOP_SECONDS = 8
EARLY_EXIT_SECONDS = 10


def cfg_line(key: str, value: str) -> str:
    return f'{key} = "{value}"'


class Screen:
    """RetroArch direkt im Vollbild auf dem Bildschirm des Pi (Wayland), als Programm des Desktop-Benutzers.

    Der Hilfsdienst läuft als derselbe Benutzer wie die Desktop-Sitzung; RetroArch erscheint dadurch über dem
    Kiosk-Browser und liest Controller (USB und Bluetooth) direkt über udev. Spiele, BIOS und Spielstände liegen
    im selben Gaming-Ordner wie beim Gaming-Container; Einstellungen in einem eigenen Unterordner ``pi``.
    """

    def __init__(self, config: AgentConfig, popen: Callable[..., Any] = subprocess.Popen,
                 which: Callable[[str], str | None] = shutil.which, clock: Callable[[], float] = time.time,
                 kill: Callable[[int, int], None] = os.killpg) -> None:
        self.config = config
        self._popen = popen
        self._kill = kill
        self._which = which
        self._clock = clock
        self._lock = threading.Lock()
        self.process: Any = None
        self.started_at = 0.0
        self.last_exit: dict[str, Any] | None = None
        self.log_path = Path(config.state_dir) / "retroarch.log"

    def binary(self) -> str:
        return self._which(self.config.retroarch) or ""

    def cores_dir(self) -> Path | None:
        candidates = [self.config.cores_dir] if self.config.cores_dir else list(CORE_DIRS)
        for candidate in candidates:
            path = Path(candidate)
            if path.is_dir() and any(path.glob("*.so")):
                return path
        return None

    def wayland_socket(self) -> Path:
        runtime = self.config.runtime_dir or f"/run/user/{os.getuid()}"
        return Path(runtime) / self.config.wayland_display

    def _collect(self) -> None:
        """Beendetes RetroArch einsammeln und merken, wie es endete."""
        if self.process is None or self.process.poll() is None:
            return
        code = self.process.returncode
        ran = self._clock() - self.started_at
        tail = ""
        if code not in (0, -signal.SIGTERM) and ran < EARLY_EXIT_SECONDS:
            try:
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-600:].strip()
            except OSError:
                tail = ""
        self.last_exit = {"code": code, "seconds": round(ran), "error": tail}
        self.process = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._collect()
            cores = self.cores_dir()
            socket_path = self.wayland_socket()
            return {
                "installed": bool(self.binary()),
                "cores": len(list(cores.glob("*.so"))) if cores else 0,
                "display": socket_path.exists() and stat.S_ISSOCK(socket_path.stat().st_mode),
                "running": self.process is not None,
                "started_at": self.started_at if self.process is not None else 0,
                "last_exit": self.last_exit,
                "install_command": SCREEN_INSTALL_COMMAND,
            }

    def write_config(self, folder: Path, cores: Path) -> tuple[Path, Path]:
        """Eigene RetroArch-Einstellungen im Gaming-Ordner; die Ordner-Zeilen schreibt StorchCam bei jedem Start neu."""
        directory = folder / "pi"
        directory.mkdir(exist_ok=True)
        for name in ("roms", "bios", "saves", "states"):
            (folder / name).mkdir(exist_ok=True)
        main = directory / "retroarch.cfg"
        if not main.exists():
            # Grundeinstellungen des Systempakets (Menü-Grafiken usw.) übernehmen; danach gehört die Datei RetroArch.
            base = SYSTEM_CONFIG.read_text(encoding="utf-8", errors="replace") if SYSTEM_CONFIG.is_file() else ""
            main.write_text(base, encoding="utf-8")
        lines = [
            "# Von StorchCam bei jedem Start am Bildschirm geschrieben. Eigene Einstellungen macht RetroArch in retroarch.cfg.",
            cfg_line("video_fullscreen", "true"),
            cfg_line("libretro_directory", str(cores)),
            cfg_line("rgui_browser_directory", str(folder / "roms")),
            cfg_line("system_directory", str(folder / "bios")),
            cfg_line("savefile_directory", str(folder / "saves")),
            cfg_line("savestate_directory", str(folder / "states")),
            cfg_line("input_joypad_driver", "udev"),
            cfg_line("input_autodetect_enable", "true"),
            cfg_line("config_save_on_exit", "true"),
        ]
        if Path(CORE_INFO_DIR).is_dir():
            lines.append(cfg_line("libretro_info_path", CORE_INFO_DIR))
        autoconfig = next((path for path in AUTOCONFIG_DIRS if Path(path).is_dir()), "")
        if autoconfig:
            lines.append(cfg_line("joypad_autoconfig_dir", autoconfig))
        overrides = directory / "storchcam.cfg"
        overrides.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return main, overrides

    def environment(self) -> dict[str, str]:
        socket_path = self.wayland_socket()
        env = {key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"}}
        env.update(XDG_RUNTIME_DIR=str(socket_path.parent), WAYLAND_DISPLAY=socket_path.name, XDG_SESSION_TYPE="wayland",
                   HOME=os.environ.get("HOME") or str(Path.home()))
        return env

    def start(self, folder: Path) -> dict[str, Any]:
        with self._lock:
            self._collect()
            if self.process is not None:
                raise AgentError("RetroArch läuft schon am Bildschirm", HTTPStatus.CONFLICT)
            binary = self.binary()
            if not binary:
                raise AgentError(f"RetroArch ist auf dem Pi nicht installiert. Im StorchCam-Ordner ausführen: {SCREEN_INSTALL_COMMAND}",
                                 HTTPStatus.SERVICE_UNAVAILABLE)
            cores = self.cores_dir()
            if cores is None:
                raise AgentError(f"Keine Emulator-Kerne gefunden. Im StorchCam-Ordner ausführen: {SCREEN_INSTALL_COMMAND}",
                                 HTTPStatus.SERVICE_UNAVAILABLE)
            if not self.wayland_socket().exists():
                raise AgentError("Kein Bildschirm gefunden: Die Desktop-Sitzung des Pi läuft nicht oder gehört einem anderen Benutzer",
                                 HTTPStatus.SERVICE_UNAVAILABLE)
            main, overrides = self.write_config(folder, cores)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "w", encoding="utf-8") as log:
                self.process = self._popen([binary, "--fullscreen", f"--config={main}", f"--appendconfig={overrides}"],
                                           env=self.environment(), cwd=str(folder), stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            self.started_at = self._clock()
            self.last_exit = None
            LOGGER.info("RetroArch am Bildschirm gestartet (PID %s)", getattr(self.process, "pid", "?"))
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._collect()
            process = self.process
            if process is None:
                raise AgentError("RetroArch läuft nicht am Bildschirm", HTTPStatus.CONFLICT)
            # SIGTERM: RetroArch beendet sich regulär und schreibt dabei die Spielstände (SRAM).
            try:
                self._kill(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self._kill(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            self._collect()
        return self.status()


# ---------------------------------------------------------------- Zustand und Aufgaben

class Store:
    """Gemerkte Einrichtung: Ordner, Port, Zugangsdaten. Datei nur für den Dienst lesbar."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "state.json"
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

    def update(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                current = {}
            current.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            return current


@dataclass
class Step:
    key: str
    label: str
    status: str = "pending"  # pending, running, done, error, skipped
    detail: str = ""


class Job:
    """Eine laufende Installation oder ein Update mit echten Schritten."""

    def __init__(self, kind: str, steps: list[tuple[str, str]]) -> None:
        self.kind = kind
        self.steps = [Step(key, label) for key, label in steps]
        self.state = "running"
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self._lock = threading.Lock()

    def step(self, key: str) -> Step:
        return next(item for item in self.steps if item.key == key)

    def begin(self, key: str, detail: str = "") -> None:
        with self._lock:
            item = self.step(key)
            item.status, item.detail = "running", detail

    def note(self, key: str, detail: str) -> None:
        with self._lock:
            self.step(key).detail = detail

    def done(self, key: str, detail: str = "", status: str = "done") -> None:
        with self._lock:
            item = self.step(key)
            item.status = status
            if detail:
                item.detail = detail

    def fail(self, message: str) -> None:
        with self._lock:
            for item in self.steps:
                if item.status == "running":
                    item.status, item.detail = "error", message
            self.state, self.error, self.finished = "error", message, time.time()

    def finish(self) -> None:
        with self._lock:
            self.state, self.finished = "done", time.time()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"kind": self.kind, "state": self.state, "error": self.error, "started": self.started, "finished": self.finished,
                    "steps": [{"key": item.key, "label": item.label, "status": item.status, "detail": item.detail} for item in self.steps]}


def probe(port: int, timeout: float = 4) -> bool:
    """Antwortet die Gaming-Oberfläche? 401 zählt als erreichbar (Passwortschutz ist aktiv)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Das Image nutzt ein selbstsigniertes Zertifikat.
    try:
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=timeout, context=context) as response:  # noqa: S310
            return 200 <= response.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code in {401, 403}
    except (OSError, ssl.SSLError):
        return False


def lan_ip() -> str:
    """Adresse des Pi im Heimnetz (UDP wählt nur die Route, es wird nichts gesendet).

    Die IP funktioniert auf jedem Gerät im Heimnetz; Namen wie raspberrypi.local lösen manche Handys und
    Container nicht auf.
    """
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe_socket.connect(("192.0.2.1", 9))
        return str(probe_socket.getsockname()[0])
    except OSError:
        return ""
    finally:
        probe_socket.close()


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        try:
            probe_socket.bind(("0.0.0.0", port))  # noqa: S104 - nur Prüfen, ob der Port frei ist
        except OSError:
            return False
    return True


def yaml_string(value: str) -> str:
    # JSON-Strings sind gültige YAML-Strings in doppelten Anführungszeichen.
    return json.dumps(value, ensure_ascii=False)


def render_compose(*, image: str, folder: Path, port: int, uid: int, gid: int, timezone: str, user: str, password: str, gpu: bool,
                   names: Names = NAMES) -> str:
    """Deklarative Compose-Datei für genau einen Container; erneutes Ausführen ändert nichts Unerwartetes."""
    lines = [
        "# Erzeugt vom StorchCam-Gaming-Hilfsdienst. Änderungen hier werden beim nächsten Update überschrieben.",
        "services:",
        "  retroarch:",
        f"    image: {yaml_string(image)}",
        f"    container_name: {names.container}",
        "    restart: unless-stopped",
        '    shm_size: "1gb"',
        "    labels:",
        f"      {names.label}: \"managed\"",
        "    environment:",
        f"      PUID: \"{uid}\"",
        f"      PGID: \"{gid}\"",
        f"      TZ: {yaml_string(timezone)}",
        f"      CUSTOM_USER: {yaml_string(user)}",
        f"      PASSWORD: {yaml_string(password)}",
        "    ports:",
        f"      - \"{port}:{GUI_PORT}\"",
        "    volumes:",
    ]
    lines += [f"      - {yaml_string(f'{folder / name}:{target}')}" for name, target in MOUNTS]
    if gpu and Path("/dev/dri").exists():
        lines += ["    devices:", '      - "/dev/dri:/dev/dri"']
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- Dienst

class GamingAgent:
    def __init__(self, config: AgentConfig, docker: Docker | None = None, ready: Callable[[int], bool] = probe,
                 port_check: Callable[[int], bool] = port_free, spawn: Callable[[Callable[[], None]], None] | None = None,
                 address: Callable[[], str] = lan_ip, screen: Screen | None = None) -> None:
        self.config = config
        self.docker = docker or Docker(config.docker)
        self.folders = Folders(config.allowed_roots)
        self.store = Store(Path(config.state_dir))
        self.ready = ready
        self.port_check = port_check
        self.address = address
        self.spawn = spawn or (lambda work: threading.Thread(target=work, name="gaming-job", daemon=True).start())
        self.job: Job | None = None
        self._lock = threading.Lock()
        self._legacy_checked = False
        self.screen = screen or Screen(config)

    # ---------- Lesen
    def names(self) -> Names:
        setup = self.store.read()
        saved = setup.get("names")
        if isinstance(saved, dict):
            try:
                return Names(**saved)
            except TypeError:
                pass
        if setup.get("folder") and not self._legacy_checked:
            # Einrichtung von vor Version 1.0: Container heißt noch bi-storchcam-gaming.
            try:
                legacy = self.docker.inspect(LEGACY_NAMES.container)
            except AgentError:
                return NAMES
            self._legacy_checked = True
            if legacy:
                self.store.update(names=asdict(LEGACY_NAMES))
                return LEGACY_NAMES
        return NAMES

    def container_name(self) -> str:
        return str(self.store.read().get("adopted") or self.names().container)

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "agent": AGENT_VERSION, "docker": {"ok": False, "version": "", "error": ""},
                                  "container": None, "managed": False, "candidates": [], "job": self.job.as_dict() if self.job else None,
                                  "screen": self.screen.status()}
        try:
            result["docker"].update(ok=True, version=self.docker.version())
        except AgentError as exc:
            result["docker"]["error"] = str(exc)
            return result
        name = self.container_name()
        details = self.docker.inspect(name)
        if details:
            result["container"] = self.describe(details)
            result["managed"] = True
            result["setup"] = self.public_setup()
        else:
            result["candidates"] = [
                {"name": item.get("Names", ""), "image": item.get("Image", ""), "state": item.get("State", ""), "status": item.get("Status", "")}
                for item in self.docker.containers() if IMAGE_MATCH in str(item.get("Image", ""))
            ]
        return result

    def describe(self, details: dict[str, Any]) -> dict[str, Any]:
        state = details.get("State") or {}
        config = details.get("Config") or {}
        labels = config.get("Labels") or {}
        ports = ((details.get("NetworkSettings") or {}).get("Ports") or {}).get(f"{GUI_PORT}/tcp") or []
        host_port = int(ports[0]["HostPort"]) if ports and str(ports[0].get("HostPort", "")).isdigit() else 0
        running = state.get("Status") == "running"
        return {
            "name": str(details.get("Name", "")).lstrip("/"),
            "state": state.get("Status", "unknown"),
            "running": running,
            "error": state.get("Error", ""),
            "started_at": state.get("StartedAt", ""),
            "image": config.get("Image", ""),
            "version": labels.get("org.opencontainers.image.version") or labels.get("build_version", ""),
            "managed_label": labels.get(self.names().label) == "managed",
            "port": host_port,
            "reachable": bool(running and host_port and self.ready(host_port)),
            "mounts": [{"source": mount.get("Source", ""), "target": mount.get("Destination", "")} for mount in details.get("Mounts") or []],
        }

    def public_setup(self) -> dict[str, Any]:
        setup = self.store.read()
        return {key: setup.get(key) for key in ("folder", "port", "gpu", "installed_at", "adopted", "previous") if key in setup}

    def access(self) -> dict[str, Any]:
        setup = self.store.read()
        details = self.docker.inspect(self.container_name())
        if not details:
            raise AgentError("Gaming ist noch nicht eingerichtet", HTTPStatus.NOT_FOUND)
        info = self.describe(details)
        return {"port": info["port"], "user": setup.get("user", ""), "password": setup.get("password", ""), "running": info["running"],
                "address": self.address(), "hostname": socket.gethostname()}

    def ports(self, port: int) -> dict[str, Any]:
        if not 1024 <= port <= 65535:
            raise AgentError("Port muss zwischen 1024 und 65535 liegen")
        return {"port": port, "free": self.port_check(port)}

    # ---------- Einrichten
    def install(self, folder_raw: str, port: int, gpu: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.job and self.job.state == "running":
                raise AgentError("Es läuft schon eine Installation", HTTPStatus.CONFLICT)
            self.docker.version()
            if self.docker.inspect(self.container_name()):
                raise AgentError("Gaming ist bereits eingerichtet", HTTPStatus.CONFLICT)
            folder = self.folders.resolve(folder_raw)
            if not os.access(folder, os.W_OK):
                raise AgentError("Keine Schreibrechte in diesem Ordner", HTTPStatus.FORBIDDEN)
            self.ports(port)
            job = Job("install", [("folders", "Ordner vorbereiten"), ("port", f"Port {port} prüfen"), ("pull", "Image laden"),
                                  ("create", "Container erstellen und starten"), ("ready", "Oberfläche prüfen")])
            self.job = job
        self.spawn(lambda: self._install(job, folder, port, gpu))
        return job.as_dict()

    def _install(self, job: Job, folder: Path, port: int, gpu: bool) -> None:
        try:
            job.begin("folders")
            owner = folder.stat()
            for name in SUBFOLDERS:
                path = folder / name
                path.mkdir(exist_ok=True)
                if os.geteuid() == 0:
                    os.chown(path, owner.st_uid, owner.st_gid)
            job.done("folders", ", ".join(SUBFOLDERS))
            job.begin("port")
            if not self.port_check(port):
                raise AgentError(f"Port {port} ist schon belegt; bitte einen anderen wählen")
            job.done("port", "frei")
            job.begin("pull", self.config.image)
            self.docker.pull(self.config.image, lambda line: job.note("pull", line))
            job.done("pull", self.config.image)
            setup = self.store.read()
            user = setup.get("user") or "spieler"
            password = setup.get("password") or secrets.token_urlsafe(12)
            self.store.update(folder=str(folder), port=port, gpu=gpu, user=user, password=password, uid=owner.st_uid, gid=owner.st_gid,
                              image=self.config.image, installed_at=time.time(), adopted="", names=asdict(NAMES))
            job.begin("create")
            self.compose_up(self.config.image)
            job.done("create", NAMES.container)
            self._wait_ready(job, port)
            job.finish()
        except AgentError as exc:
            job.fail(str(exc))
        except OSError as exc:
            job.fail(f"Dateisystem: {exc.strerror or exc}")

    def _wait_ready(self, job: Job, port: int) -> None:
        job.begin("ready", f"https://…:{port}")
        deadline = time.monotonic() + READY_SECONDS
        while time.monotonic() < deadline:
            if self.ready(port):
                job.done("ready", "erreichbar")
                return
            time.sleep(3)
        raise AgentError("Container läuft, aber die Oberfläche antwortet noch nicht; bitte später erneut prüfen")

    def compose_up(self, image: str) -> None:
        names = self.names()
        self.docker.compose_up(self.write_compose(image, names), names.project)

    def write_compose(self, image: str, names: Names = NAMES) -> Path:
        setup = self.store.read()
        compose = render_compose(image=image, folder=Path(setup["folder"]), port=int(setup["port"]), uid=int(setup["uid"]), gid=int(setup["gid"]),
                                 timezone=self.config.timezone, user=setup["user"], password=setup["password"], gpu=bool(setup.get("gpu")),
                                 names=names)
        directory = Path(self.config.state_dir) / "compose"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "compose.yaml"
        target.write_text(compose, encoding="utf-8")
        os.chmod(target, 0o600)  # enthält das Passwort der Oberfläche
        return target

    def adopt(self, name: str) -> dict[str, Any]:
        """Vorhandenen RetroArch-Container übernehmen: nur starten/stoppen, nie neu erstellen."""
        details = self.docker.inspect(name)
        if not details or IMAGE_MATCH not in str((details.get("Config") or {}).get("Image", "")):
            raise AgentError("Das ist kein RetroArch-Container von linuxserver", HTTPStatus.BAD_REQUEST)
        self.store.update(adopted=str(details.get("Name", "")).lstrip("/"))
        return self.status()

    # ---------- Starten, Stoppen, Update
    def control(self, action: str) -> dict[str, Any]:
        name = self.container_name()
        if not self.docker.inspect(name):
            raise AgentError("Gaming ist noch nicht eingerichtet", HTTPStatus.NOT_FOUND)
        if action == "start":
            self.docker.start(name)
        elif action == "stop":
            self.docker.stop(name)
        else:
            raise AgentError("Unbekannte Aktion")
        return self.status()

    def update(self, rollback: bool = False) -> dict[str, Any]:
        with self._lock:
            setup = self.store.read()
            if setup.get("adopted"):
                raise AgentError("Übernommene Container aktualisiert StorchCam nicht; bitte wie bisher aktualisieren", HTTPStatus.CONFLICT)
            names = self.names()
            if not setup.get("folder") or not self.docker.inspect(names.container):
                raise AgentError("Gaming ist noch nicht eingerichtet", HTTPStatus.NOT_FOUND)
            if self.job and self.job.state == "running":
                raise AgentError("Es läuft schon eine Aufgabe", HTTPStatus.CONFLICT)
            if rollback and not self.docker.image_id(names.previous):
                raise AgentError("Es gibt keine gesicherte vorige Version", HTTPStatus.NOT_FOUND)
            steps = [("create", "Vorige Version starten"), ("ready", "Oberfläche prüfen")] if rollback else [
                ("pull", "Neue Version laden"), ("backup", "Bisherige Version sichern"), ("create", "Container neu erstellen"), ("ready", "Oberfläche prüfen")]
            job = Job("rollback" if rollback else "update", steps)
            self.job = job
        self.spawn(lambda: self._update(job, rollback))
        return job.as_dict()

    def _update(self, job: Job, rollback: bool) -> None:
        setup = self.store.read()
        port = int(setup["port"])
        names = self.names()
        try:
            if rollback:
                job.begin("create")
                self.compose_up(names.previous)
                self.store.update(previous=False)
                job.done("create", "vorige Version")
            else:
                running_image = str((self.docker.inspect(names.container) or {}).get("Image", ""))
                job.begin("pull", self.config.image)
                self.docker.pull(self.config.image, lambda line: job.note("pull", line))
                fresh = self.docker.image_id(self.config.image)
                if fresh and fresh == running_image:
                    job.done("pull", "bereits aktuell")
                    for key in ("backup", "create", "ready"):
                        job.done(key, "nicht nötig", status="skipped")
                    job.finish()
                    return
                job.done("pull", "neue Version geladen")
                job.begin("backup")
                if running_image:
                    self.docker.tag(running_image, names.previous)
                    self.store.update(previous=True)
                job.done("backup", names.previous)
                job.begin("create")
                self.compose_up(self.config.image)
                job.done("create", names.container)
            self._wait_ready(job, port)
            job.finish()
        except AgentError as exc:
            job.fail(str(exc))

    # ---------- Am Bildschirm des Pi spielen
    def screen_action(self, action: str) -> dict[str, Any]:
        if action == "stop":
            return self.screen.stop()
        if action != "start":
            raise AgentError("Unbekannte Aktion")
        folder = self.store.read().get("folder")
        if not folder:
            raise AgentError("Gaming zuerst einrichten: Der Speicherordner für Spiele fehlt noch", HTTPStatus.CONFLICT)
        return self.screen.start(self.folders.resolve(str(folder)))

    # ---------- Spiele
    def rom_folder(self) -> Path:
        setup = self.store.read()
        if not setup.get("folder"):
            raise AgentError("Gaming ist noch nicht mit einem Ordner eingerichtet", HTTPStatus.NOT_FOUND)
        return Path(setup["folder"]) / "roms"

    def roms(self) -> dict[str, Any]:
        folder = self.rom_folder()
        files = []
        if folder.is_dir():
            for path in sorted(folder.rglob("*"), key=lambda item: str(item).lower()):
                if path.is_file() and not path.name.startswith("."):
                    system = ROM_SYSTEMS.get(path.suffix.lower())
                    if system:
                        files.append({"name": str(path.relative_to(folder)), "size": path.stat().st_size, "system": system})
                if len(files) >= 2000:
                    break
        return {"folder": str(folder), "files": files, "extensions": sorted(ROM_SYSTEMS)}

    def save_rom(self, name: str, length: int, chunks: Iterator[bytes], replace: bool = False) -> dict[str, Any]:
        clean = Path(name).name.strip()
        if not ROM_NAME.match(clean) or clean.startswith(".") or clean != name.strip():
            raise AgentError("Ungültiger Dateiname")
        if Path(clean).suffix.lower() not in ROM_SYSTEMS:
            raise AgentError(f"Unbekannte Spieldatei; erlaubt sind {', '.join(sorted(ROM_SYSTEMS))}")
        if not 0 < length <= MAX_ROM_BYTES:
            raise AgentError("Datei ist leer oder größer als 4 GB", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        folder = self.rom_folder()
        folder.mkdir(exist_ok=True)
        target = folder / clean
        if target.exists() and not replace:
            raise AgentError("Diese Datei gibt es schon", HTTPStatus.CONFLICT)
        if shutil.disk_usage(folder).free < length + 50 * 1024**2:
            raise AgentError("Zu wenig freier Speicher", HTTPStatus.INSUFFICIENT_STORAGE)
        temporary = folder / f".{clean}.upload"
        written = 0
        try:
            with open(temporary, "wb") as handle:
                for chunk in chunks:
                    written += len(chunk)
                    if written > length:
                        raise AgentError("Mehr Daten als angekündigt")
                    handle.write(chunk)
            if written != length:
                raise AgentError("Übertragung abgebrochen")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {"name": clean, "size": written, "system": ROM_SYSTEMS[target.suffix.lower()]}


# ---------------------------------------------------------------- HTTP über den Unix-Socket

def number(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise AgentError("Ungültige Zahl") from exc


class AgentHandler(BaseHTTPRequestHandler):
    server_version = f"StorchCam-Gaming/{AGENT_VERSION}"
    sys_version = ""

    @property
    def agent(self) -> GamingAgent:
        return self.server.agent  # type: ignore[attr-defined]

    def address_string(self) -> str:  # Unix-Socket hat keine IP-Adresse
        return "local"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        LOGGER.debug("%s %s", self.command, self.path)

    def _send(self, value: Any, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 65536:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        return value if isinstance(value, dict) else {}

    def _chunks(self, length: int) -> Iterator[bytes]:
        left = length
        while left > 0:
            chunk = self.rfile.read(min(1024 * 1024, left))
            if not chunk:
                return
            left -= len(chunk)
            yield chunk

    def _handle(self, work: Callable[[], Any]) -> None:
        try:
            self._send(work())
        except AgentError as exc:
            self._send({"ok": False, "error": str(exc)}, int(exc.status))
        except Exception:
            LOGGER.exception("Fehler bei %s %s", self.command, self.path)
            self._send({"ok": False, "error": "Interner Fehler im Gaming-Hilfsdienst"}, 500)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        routes: dict[str, Callable[[], Any]] = {
            "/status": self.agent.status,
            "/folders": lambda: self.agent.folders.listing(query.get("path", [""])[0]),
            "/ports": lambda: self.agent.ports(number(query.get("port", ["0"])[0])),
            "/job": lambda: {"job": self.agent.job.as_dict() if self.agent.job else None},
            "/roms": self.agent.roms,
            "/access": self.agent.access,
            "/screen": self.agent.screen.status,
        }
        route = routes.get(parsed.path)
        self._handle(route if route else lambda: (_ for _ in ()).throw(AgentError("Unbekannt", HTTPStatus.NOT_FOUND)))

    def do_POST(self) -> None:  # noqa: N802
        def work() -> Any:
            body = self._body()
            path = urllib.parse.urlparse(self.path).path
            if path == "/folders":
                return self.agent.folders.create(str(body.get("path", "")), str(body.get("name", "")))
            if path == "/install":
                return self.agent.install(str(body.get("folder", "")), number(body.get("port") or DEFAULT_PORT), bool(body.get("gpu")))
            if path == "/control":
                return self.agent.control(str(body.get("action", "")))
            if path == "/update":
                return self.agent.update(rollback=bool(body.get("rollback")))
            if path == "/adopt":
                return self.agent.adopt(str(body.get("name", "")))
            if path == "/screen":
                return self.agent.screen_action(str(body.get("action", "")))
            raise AgentError("Unbekannt", HTTPStatus.NOT_FOUND)
        self._handle(work)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/roms":
            self._handle(lambda: (_ for _ in ()).throw(AgentError("Unbekannt", HTTPStatus.NOT_FOUND)))
            return
        length = int(self.headers.get("Content-Length") or 0)
        self._handle(lambda: self.agent.save_rom(query.get("name", [""])[0], length, self._chunks(length), query.get("replace", [""])[0] == "1"))


class AgentServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, agent: GamingAgent) -> None:
        self.agent = agent
        socket_path = Path(path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists() and stat.S_ISSOCK(socket_path.stat().st_mode):
            socket_path.unlink()
        previous = os.umask(0o007)
        try:
            super().__init__(path, AgentHandler)
        finally:
            os.umask(previous)
        os.chmod(path, 0o660)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gaming-Hilfsdienst für StorchCam")
    parser.add_argument("--config", type=Path, default=Path("/etc/storchcam-gaming/agent.json"))
    parser.add_argument("--verbose", action="store_true")
    options = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if options.verbose else logging.INFO, format="%(levelname)s %(message)s")
    config = AgentConfig.load(options.config)
    server = AgentServer(config.socket_path, GamingAgent(config))
    LOGGER.info("Gaming-Hilfsdienst lauscht auf %s (Ordner: %s)", config.socket_path, ", ".join(config.allowed_roots))
    server.serve_forever()


if __name__ == "__main__":
    main()
