"""Pair a phone with the kiosk: a one-time code on the screen grants a menu session on the phone.

The code proves someone stands in front of the screen. It is valid for ten minutes, works once
and is thrown away after five wrong attempts. The address for the QR code comes from the
configuration or STORCHCAM_PUBLIC_URL; outside Docker the LAN address is detected.
"""

from __future__ import annotations

import hmac
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import segno

from .environment import env

CODE_SECONDS = 600
MAX_ATTEMPTS = 5

_lock = threading.Lock()
_state: dict[str, Any] = {"code": "", "expires": 0.0, "attempts": 0, "claimed": 0.0}


def _lan_ip() -> str:
    """Eigene Adresse im Heimnetz (ohne Verbindung aufzubauen: UDP wählt nur die Route)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return str(probe.getsockname()[0])
    except OSError:
        return ""
    finally:
        probe.close()


def public_url(config: dict[str, Any]) -> str:
    configured = str(config.get("server", {}).get("public_url") or env("PUBLIC_URL")).strip().rstrip("/")
    if configured:
        return configured
    if Path("/.dockerenv").exists():
        return ""  # im Container ist die Adresse des Pi im Heimnetz unbekannt
    address = _lan_ip()
    if not address or address.startswith("127."):
        return ""
    return f"http://{address}:{int(config.get('server', {}).get('port', 8000))}"


def start(config: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _state.update({"code": f"{secrets.randbelow(1_000_000):06d}", "expires": time.monotonic() + CODE_SECONDS, "attempts": 0, "claimed": 0.0})
    return status(config)


def status(config: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        left = int(_state["expires"] - time.monotonic())
        code = _state["code"] if left > 0 else ""
        claimed = _state["claimed"]
    base = public_url(config)
    result: dict[str, Any] = {"active": bool(code), "code": code, "expires_in": max(0, left), "url": base, "claimed": bool(claimed)}
    if code and base:
        link = f"{base}/?{urlencode({'koppeln': code})}"
        result["link"] = link
        result["qr"] = str(segno.make(link, error="m").svg_data_uri(scale=6, border=2, dark="#0c1821", light="#fff"))
    return result


def claim(code: str) -> bool:
    """True genau einmal für den richtigen, noch gültigen Code."""
    clean = "".join(character for character in str(code) if character.isdigit())
    with _lock:
        if not _state["code"] or time.monotonic() > _state["expires"]:
            return False
        if hmac.compare_digest(clean, _state["code"]):
            _state.update({"code": "", "expires": 0.0, "attempts": 0, "claimed": time.monotonic()})
            return True
        _state["attempts"] += 1
        if _state["attempts"] >= MAX_ATTEMPTS:
            _state.update({"code": "", "expires": 0.0})
        return False


def cancel() -> None:
    with _lock:
        _state.update({"code": "", "expires": 0.0, "attempts": 0})
