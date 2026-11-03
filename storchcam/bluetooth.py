"""Bluetooth speakers through BlueZ on the host, driven by bluetoothctl over the system D-Bus.

Scanning, pairing and connecting take several seconds, so they run in a background thread
and the settings menu polls status(). PipeWire on the host picks up a connected speaker as
audio output by itself; nothing here touches audio routing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from typing import Any

from . import player

SCAN_SECONDS = 12
PAIR_SECONDS = 25
MAC_PATTERN = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}")
ANSI = re.compile(r"\x1b\[[0-9;]*m|\x01|\x02")

_lock = threading.Lock()
_job: dict[str, Any] = {"busy": False, "action": "", "mac": "", "message": "", "ok": True}


class BluetoothError(RuntimeError):
    pass


def _run(*arguments: str, timeout: float = 8, duration: int = 0) -> str:
    """Ein bluetoothctl-Befehl; duration hält ihn so lange offen (Suche), sonst endet er mit der Antwort."""
    executable = shutil.which("bluetoothctl")
    if not executable:
        raise BluetoothError("Bluetooth-Werkzeug fehlt (Paket bluez)")
    command = [executable, "--timeout", str(duration), *arguments] if duration else [executable, *arguments]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=max(timeout, duration) + 5, check=False, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise BluetoothError("Der Bluetooth-Dienst antwortet nicht") from exc
    output = ANSI.sub("", result.stdout + result.stderr)
    if "Waiting to connect to bluetoothd" in output:
        raise BluetoothError("Der Bluetooth-Dienst des Geräts ist nicht erreichbar")
    return output


def _info(mac: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"uuids": []}
    for line in _run("info", mac, timeout=4).splitlines():
        key, separator, value = line.strip().partition(": ")
        if not separator:
            continue
        if key == "UUID":
            fields["uuids"].append(value)
        else:
            fields.setdefault(key, value)
    return fields


def _adapter() -> dict[str, str]:
    if not MAC_PATTERN.search(_run("list", timeout=4)):
        raise BluetoothError("Dieses Gerät hat kein Bluetooth (kein Adapter gefunden)")
    shown = dict(line.strip().split(": ", 1) for line in _run("show", timeout=4).splitlines() if ": " in line)
    return shown


def _kind(info: dict[str, Any]) -> str:
    """Lautsprecher (Ton des Pi) oder Controller (Gaming am Bildschirm); andere Geräte zeigt das Menü nicht."""
    icon = str(info.get("Icon", ""))
    uuids = [str(uuid) for uuid in info["uuids"]]
    if icon.startswith("audio") or any("Audio Sink" in uuid for uuid in uuids):
        return "audio"
    if icon == "input-gaming" or (any("Human Interface Device" in uuid for uuid in uuids)
                                  and not icon.startswith(("input-keyboard", "input-mouse", "input-tablet"))):
        return "gamepad"
    return ""


def devices() -> list[dict[str, Any]]:
    result = []
    for line in _run("devices", timeout=4).splitlines():
        match = re.match(rf"Device ({MAC_PATTERN.pattern}) (.+)", line.strip())
        if not match:
            continue
        info = _info(match.group(1))
        # Geräte ohne echten Namen (nur Adresse) sind fast immer fremde Handys oder Sensoren.
        if "Name" not in info:
            continue
        kind = _kind(info)
        paired = info.get("Paired") == "yes"
        if not kind and not paired:
            continue
        result.append({
            "mac": match.group(1),
            "name": str(info.get("Alias") or info["Name"]),
            "kind": kind or "other",
            "paired": paired,
            "connected": info.get("Connected") == "yes",
        })
    return sorted(result, key=lambda device: (not device["connected"], not device["paired"], device["name"].lower()))


def status() -> dict[str, Any]:
    with _lock:
        job = dict(_job)
    try:
        adapter = _adapter()
        return {"available": True, "powered": adapter.get("Powered") == "yes", "devices": devices(), **job}
    except BluetoothError as exc:
        return {"available": False, "error": str(exc), "devices": [], **job}


def _start(action: str, mac: str, work: Any) -> None:
    with _lock:
        if _job["busy"]:
            raise BluetoothError("Bluetooth ist gerade beschäftigt, bitte kurz warten")
        _job.update({"busy": True, "action": action, "mac": mac, "message": "", "ok": True})

    def run() -> None:
        try:
            message, ok = str(work()), True
        except BluetoothError as exc:
            message, ok = str(exc), False
        with _lock:
            _job.update({"busy": False, "message": message, "ok": ok})

    threading.Thread(target=run, name=f"bluetooth-{action}", daemon=True).start()


def _checked_mac(mac: str) -> str:
    mac = mac.strip().upper()
    if not MAC_PATTERN.fullmatch(mac):
        raise BluetoothError("Ungültige Bluetooth-Adresse")
    return mac


def scan() -> None:
    _adapter()

    def work() -> str:
        if "Powered: yes" not in _run("show", timeout=4):
            _run("power", "on", timeout=6)
        _run("scan", "on", duration=SCAN_SECONDS)
        return "Suche beendet"

    _start("scan", "", work)


def connect(mac: str) -> None:
    mac = _checked_mac(mac)
    _adapter()

    def work() -> str:
        info = _info(mac)
        noun = "der Controller" if _kind(info) == "gamepad" else "der Lautsprecher"
        if info.get("Paired") != "yes":
            output = _run("--agent", "NoInputNoOutput", "pair", mac, timeout=PAIR_SECONDS)
            if "Pairing successful" not in output and "AlreadyExists" not in output:
                raise BluetoothError(f"Koppeln fehlgeschlagen. Ist {noun} im Kopplungsmodus?")
            # Vertrauen: Controller und Lautsprecher verbinden sich danach beim Einschalten von selbst wieder.
            _run("trust", mac, timeout=6)
        output = _run("connect", mac, timeout=PAIR_SECONDS)
        if "Connection successful" not in output and _info(mac).get("Connected") != "yes":
            raise BluetoothError(f"Verbinden fehlgeschlagen. Ist {noun} eingeschaltet und in Reichweite?")
        if _kind(info) == "audio":
            player.full_output_volume(wait_for_bluetooth=8)
        return "Verbunden"

    _start("connect", mac, work)


def disconnect(mac: str) -> None:
    mac = _checked_mac(mac)
    _start("disconnect", mac, lambda: (_run("disconnect", mac, timeout=10), "Getrennt")[1])


def remove(mac: str) -> None:
    mac = _checked_mac(mac)
    _start("remove", mac, lambda: (_run("remove", mac, timeout=10), "Entfernt")[1])
