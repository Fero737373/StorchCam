"""Container entry point: prepares the config and starts server plus kiosk browser."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from storchcam.admin import hash_pin
from storchcam.config_store import ConfigError, config_path, load_config, save_config
from storchcam.environment import env
from storchcam.kiosk_app import EXIT_CONFIG_ERROR, main

DISPLAY_WAIT_SECONDS = 300
DISPLAY_POLL_SECONDS = 2
CONTAINER_PORT = 8000


def _enabled(name: str, default: str) -> bool:
    return env(name, default).strip().lower() in {"1", "true", "yes", "on"}


def wait_for_display() -> bool:
    """Wartet, bis die Wayland-Sitzung des Hosts (Autologin) ihren Socket angelegt hat."""
    socket = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    deadline = time.monotonic() + DISPLAY_WAIT_SECONDS
    announced = False
    while not socket.exists():
        if time.monotonic() >= deadline:
            print(f"WARNUNG: {socket} fehlt nach {DISPLAY_WAIT_SECONDS}s; starte ohne Kiosk-Browser", file=sys.stderr)
            return False
        if not announced:
            print(f"Warte auf Display-Socket {socket} …", file=sys.stderr)
            announced = True
        time.sleep(DISPLAY_POLL_SECONDS)
    return True


def _configure_admin(config: dict[str, Any]) -> None:
    # Im Container ist das Menü standardmäßig ohne PIN offen; der Port ist nur auf dem Pi selbst erreichbar.
    admin = config["admin"]
    admin["require_pin"] = _enabled("REQUIRE_PIN", "0")
    if not admin["require_pin"] or admin["pin_hash"]:
        return
    pin = env("ADMIN_PIN").strip()
    if not pin:
        raise ConfigError(
            "Keine Admin-PIN eingerichtet. Entweder STORCHCAM_ADMIN_PIN setzen oder einmalig\n"
            "  docker compose run --rm storchcam --setup\n"
            "ausführen."
        )
    try:
        admin["pin_hash"] = hash_pin(pin)
    except ValueError as exc:
        raise ConfigError(f"STORCHCAM_ADMIN_PIN: {exc}") from exc


def _apply_container_overrides(config: dict[str, Any]) -> None:
    stream_url = env("STREAM_URL").strip()
    if stream_url:
        config["stream"]["url"] = stream_url
    # Im Container muss der Server auf allen Interfaces lauschen, damit der Port nach außen erreichbar ist.
    config["server"]["host"] = "0.0.0.0"
    config["server"]["port"] = CONTAINER_PORT
    config["kiosk"]["enabled"] = _enabled("KIOSK", "1") and wait_for_display()


def prepare_config() -> int | None:
    path = config_path()
    try:
        config = load_config()
        _configure_admin(config)
        _apply_container_overrides(config)
        save_config(config, path)
    except ConfigError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        failure = prepare_config()
        if failure is not None:
            raise SystemExit(failure)
    raise SystemExit(main(args))
