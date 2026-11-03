"""The Pi as a Spotify Connect speaker, played by librespot (Spotify Premium required).

Pairing uses librespot's device code flow: it prints a spotify.com/pair address, which
the settings menu shows as a QR code; after confirming on the phone librespot caches the
credentials and signs in by itself on every later start. Playback is then started and
controlled through the Spotify Web API (providers/music.py) on the device named DEVICE_NAME.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import segno

from .config_store import config_path

LOGGER = logging.getLogger(__name__)
DEVICE_NAME = "StorchCam"
RESTART_SECONDS = 30
URL_PATTERN = re.compile(r"Browse to:\s*(https://\S+)")
CODE_PATTERN = re.compile(r"enter code:\s*(\S+)", re.IGNORECASE)

_lock = threading.Lock()
_state: dict[str, Any] = {"process": None, "status": "stopped", "url": "", "code": "", "error": "", "started": 0.0}


class SpotifyConnectError(RuntimeError):
    pass


def cache_dir() -> Path:
    return config_path().parent / "librespot"


def paired() -> bool:
    return (cache_dir() / "credentials.json").is_file()


def _running() -> bool:
    process = _state["process"]
    return process is not None and process.poll() is None


def _start(pairing: bool) -> None:
    executable = shutil.which("librespot")
    if not executable:
        raise SpotifyConnectError("librespot ist nicht installiert")
    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    cache.chmod(0o700)
    arguments = [
        executable, "--name", DEVICE_NAME, "--device-type", "speaker", "--backend", "pulseaudio",
        "--cache", str(cache), "--disable-audio-cache", "--disable-discovery", "--bitrate", "320", "--initial-volume", "100",
    ]
    if pairing:
        arguments.append("--enable-device-auth")
    process = subprocess.Popen(
        arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
    )
    _state.update({
        "process": process, "status": "pairing" if pairing else "starting", "url": "", "code": "", "error": "",
        "started": time.monotonic(),
    })
    threading.Thread(target=_read_output, args=(process,), name="librespot-output", daemon=True).start()


def _read_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        with _lock:
            if _state["process"] is not process:
                continue
            if match := URL_PATTERN.search(line):
                _state["url"] = match.group(1)
            elif match := CODE_PATTERN.search(line):
                _state["code"] = match.group(1)
            elif "Authenticated as" in line:
                _state.update({"status": "ready", "url": "", "code": "", "error": ""})
            elif " ERROR " in line:
                _state["error"] = line.split("]", 1)[-1].strip()[:200]
    process.wait()
    with _lock:
        if _state["process"] is process and _state["status"] != "stopped":
            _state["status"] = "error" if _state["status"] == "pairing" else "crashed"
            LOGGER.warning("librespot beendet (Code %s): %s", process.returncode, _state["error"] or "ohne Meldung")


def _stop_locked() -> None:
    process = _state["process"]
    _state.update({"process": None, "status": "stopped"})
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def ensure_running() -> None:
    """Startet den Spotify-Lautsprecher, wenn er gekoppelt ist und nicht läuft (nach Absturz höchstens alle 30 s)."""
    with _lock:
        if not paired() or _running() or _state["status"] == "pairing":
            return
        if time.monotonic() - _state["started"] < RESTART_SECONDS and _state["started"]:
            return
        try:
            _start(pairing=False)
        except (OSError, SpotifyConnectError) as exc:
            _state.update({"status": "error", "error": str(exc), "started": time.monotonic()})


def pair() -> dict[str, Any]:
    with _lock:
        _stop_locked()
        (cache_dir() / "credentials.json").unlink(missing_ok=True)
        try:
            _start(pairing=True)
        except OSError as exc:
            raise SpotifyConnectError(f"librespot ließ sich nicht starten: {exc}") from exc
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = status()
        if current["url"] or current["status"] != "pairing":
            return current
        time.sleep(0.2)
    raise SpotifyConnectError("librespot hat keinen Kopplungscode geliefert")


def unpair() -> None:
    with _lock:
        _stop_locked()
    shutil.rmtree(cache_dir(), ignore_errors=True)


def status() -> dict[str, Any]:
    with _lock:
        state = {key: _state[key] for key in ("status", "url", "code", "error")}
        running = _running()
    result: dict[str, Any] = {**state, "paired": paired(), "running": running, "name": DEVICE_NAME}
    if state["status"] == "pairing" and state["url"]:
        result["qr"] = str(segno.make(state["url"], error="m").svg_data_uri(scale=6, border=2, dark="#000", light="#fff"))
    return result


def shutdown() -> None:
    with _lock:
        _stop_locked()
