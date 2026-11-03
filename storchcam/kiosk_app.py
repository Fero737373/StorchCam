"""The single official StorchCam application entry point."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import player, spotify_connect
from .admin import hash_pin
from .config_store import ConfigError, config_path, load_config, public_config, save_config
from .kiosk import BrowserManager
from .logging_setup import configure_logging
from .providers.transit_vrr_smart import get_boards, search_station
from .providers.weather_smart import get_weather
from .server import StorchServer, create_server, run_server
from .state import StateManager
from .version import DISPLAY_VERSION, __version__

LOGGER = logging.getLogger(__name__)
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_PORT_UNAVAILABLE = 3
EXIT_NOT_READY = 4
EXIT_SERVER_FAILED = 5
PORT_IN_USE_ERRNOS = {48, 98, 10048}  # EADDRINUSE auf macOS, Linux und Windows
SUPERVISOR_INTERVAL_SECONDS = 0.5


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=f"StorchCam {DISPLAY_VERSION}")
    result.add_argument("--setup", action="store_true", help="Lokale Grundkonfiguration und Admin-PIN einrichten")
    result.add_argument("--no-browser", action="store_true", help="Nur lokalen Server starten")
    result.add_argument("--test-config", action="store_true", help="Validierte Config ohne Geheimnisse anzeigen")
    result.add_argument("--test-weather", action="store_true", help="Open-Meteo einmalig testen")
    result.add_argument("--test-transit", action="store_true", help="VRR einmalig testen")
    result.add_argument("--station-search", default="", help="VRR-Haltestelle suchen")
    return result


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def setup_interactive(config: dict[str, Any], path: Path) -> None:
    print(f"StorchCam {DISPLAY_VERSION} – lokales Setup")
    label = input(f"Standort [{config['location']['label']}]: ").strip()
    if label:
        config["location"]["label"] = label
    stream = input(f"Stream-Embed-URL [{config['stream']['url']}]: ").strip()
    if stream:
        config["stream"]["url"] = stream
    if not config.get("admin", {}).get("pin_hash"):
        while True:
            pin = input("Neue Admin-PIN (4–12 Ziffern): ").strip()
            confirmation = input("PIN wiederholen: ").strip()
            if pin != confirmation:
                print("PINs stimmen nicht überein.")
                continue
            try:
                config["admin"]["pin_hash"] = hash_pin(pin)
                break
            except ValueError as exc:
                print(exc)
    save_config(config, path)
    print(f"Konfiguration gespeichert: {path}")


def _base_url(config: dict[str, Any]) -> str:
    """Adresse, unter der der eigene Server lokal erreichbar ist (Wildcard-Bindungen über 127.0.0.1)."""
    host = str(config["server"]["host"])
    request_host = "127.0.0.1" if host in {"0.0.0.0", "::", "::1", "localhost"} else host
    return f"http://{request_host}:{int(config['server']['port'])}/"


def _readiness_url(config: dict[str, Any]) -> str:
    return _base_url(config) + "api/health"


def wait_for_health(config: dict[str, Any], timeout: float = 45) -> bool:
    deadline = time.monotonic() + timeout
    url = _readiness_url(config)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") and payload.get("state_ready"):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def _server_thread(server: StorchServer, errors: list[BaseException]) -> None:
    try:
        run_server(server)
    except BaseException as exc:
        errors.append(exc)
        LOGGER.exception("HTTP-Server wurde unerwartet beendet")


def _diagnostic_command(args: argparse.Namespace, config: dict[str, Any], path: Path) -> int | None:
    if args.test_config:
        _print_json({"version": __version__, "display_version": DISPLAY_VERSION, "path": str(path), "config": public_config(config)})
        return EXIT_OK
    if args.test_weather:
        _print_json(get_weather(config, force=True))
        return EXIT_OK
    if args.test_transit:
        _print_json(get_boards(config, force=True))
        return EXIT_OK
    if args.station_search:
        _print_json(search_station(args.station_search))
        return EXIT_OK
    return None


def _load_initial_config(path: Path) -> dict[str, Any]:
    config = load_config()
    if not path.exists():
        save_config(config, path)
    return config


def _shutdown_server(server: StorchServer, state: StateManager) -> None:
    server.shutdown()
    server.server_close()
    state.stop()
    player.shutdown()
    spotify_connect.shutdown()


def _start_browser(args: argparse.Namespace, config: dict[str, Any], url: str) -> BrowserManager | None:
    if args.no_browser or not config.get("kiosk", {}).get("enabled", True):
        return None
    browser = BrowserManager(config, url)
    browser.start()
    return browser


def _install_stop_signals() -> threading.Event:
    stop_requested = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_requested.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
    return stop_requested


def _supervise(
    server: StorchServer,
    thread: threading.Thread,
    errors: list[BaseException],
    browser: BrowserManager | None,
    stop_requested: threading.Event,
) -> None:
    while thread.is_alive() and not errors and not stop_requested.is_set():
        if browser:
            browser.config = server.context.get_config()
            browser.tick()
        time.sleep(SUPERVISOR_INTERVAL_SECONDS)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    path = config_path()
    try:
        config = _load_initial_config(path)
    except ConfigError as exc:
        print(f"FEHLER: {exc}")
        return EXIT_CONFIG_ERROR

    configure_logging(config)
    LOGGER.info("Starte StorchCam %s", DISPLAY_VERSION)
    if args.setup:
        setup_interactive(config, path)
        return EXIT_OK
    diagnostic = _diagnostic_command(args, config, path)
    if diagnostic is not None:
        return diagnostic

    state = StateManager(config)
    state.start()
    try:
        server = create_server(config, state)
    except OSError as exc:
        state.stop()
        message = "Port ist bereits belegt" if exc.errno in PORT_IN_USE_ERRNOS else str(exc)
        LOGGER.error("Serverstart fehlgeschlagen auf %s:%s: %s", config["server"]["host"], config["server"]["port"], message)
        return EXIT_PORT_UNAVAILABLE

    errors: list[BaseException] = []
    thread = threading.Thread(target=_server_thread, args=(server, errors), name="storchcam-http", daemon=True)
    thread.start()
    if not wait_for_health(config):
        LOGGER.error("Server/State wurde nicht innerhalb des Readiness-Zeitfensters bereit")
        _shutdown_server(server, state)
        return EXIT_NOT_READY

    url = _base_url(config)
    LOGGER.info("Server bereit: %s", url)
    LOGGER.info("Konfiguration: %s", path)

    browser = _start_browser(args, config, url)
    stop_requested = _install_stop_signals()
    try:
        _supervise(server, thread, errors, browser, stop_requested)
    except KeyboardInterrupt:
        LOGGER.info("Beenden angefordert")
    finally:
        if browser:
            browser.stop()
        _shutdown_server(server, state)
        thread.join(timeout=5)
    if errors:
        LOGGER.error("Serverfehler wurde an den Hauptprozess weitergegeben: %s", errors[0])
        return EXIT_SERVER_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
