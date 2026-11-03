"""Thread-safe background state snapshots; API reads never call external providers."""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

from . import gaming, player
from .providers.music import get_now_playing
from .providers.music import refresh_seconds as music_refresh_seconds
from .providers.transit_vrr_smart import get_boards
from .providers.warnings_dwd import get_warnings
from .providers.weather_smart import get_weather
from .system_status import get_system_status

LOGGER = logging.getLogger(__name__)
SYSTEM_REFRESH_SECONDS = 5
WARNINGS_REFRESH_SECONDS = 600
GAMING_REFRESH_SECONDS = 15

ProviderTask = tuple[str, int, Callable[[], Any]]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _system_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("ui", {}).get("system", {}).get("enabled", False))


def _music_enabled(config: dict[str, Any]) -> bool:
    # Radio braucht kein Konto: läuft auf dem Pi etwas, wird es immer angezeigt.
    return config.get("music", {}).get("source", "off") != "off" or player.active()


def _warnings_enabled(config: dict[str, Any]) -> bool:
    return config.get("weather", {}).get("warnings", "moderate") != "off" and bool(config.get("ui", {}).get("weather", {}).get("enabled", True))


def _provider_tasks(config: dict[str, Any]) -> list[ProviderTask]:
    tasks: list[ProviderTask] = [
        ("weather", int(config["weather"]["refresh_seconds"]), partial(get_weather, config, force=True)),
        ("boards", int(config["transit"]["refresh_seconds"]), partial(get_boards, config, force=True)),
    ]
    if _warnings_enabled(config):
        tasks.append(("warnings", WARNINGS_REFRESH_SECONDS, partial(get_warnings, config)))
    if _system_enabled(config):
        tasks.append(("system", SYSTEM_REFRESH_SECONDS, get_system_status))
    if _music_enabled(config):
        tasks.append(("music", music_refresh_seconds(config), partial(get_now_playing, config)))
    if config.get("gaming", {}).get("pause_camera"):
        # Nur nötig, wenn die Kamera beim Spielen pausieren soll; fragt den Hilfsdienst auf dem Pi.
        tasks.append(("gaming", GAMING_REFRESH_SECONDS, gaming.brief))
    return tasks


def _fetch(name: str, task: Callable[[], Any]) -> Any:
    try:
        return task()
    except Exception as exc:
        LOGGER.exception("State-Provider %s fehlgeschlagen", name)
        return [] if name == "boards" else {"ok": False, "error": str(exc)[:160]}


def _health(name: str, value: Any, interval: int, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Zustand einer Datenquelle für die Übersicht im Menü: ok, veraltet oder gestört."""
    if isinstance(value, list):
        failed = [board for board in value if isinstance(board, dict) and not board.get("ok", True)]
        ok, error = not failed, "; ".join(str(board.get("error", "")) for board in failed)[:160]
        detail = f"{len(failed)} von {len(value)} gestört" if failed else ""
    else:
        ok = bool(value.get("ok", True)) and not value.get("stale")
        error, detail = str(value.get("error") or "")[:160], ""
    now = _utc_timestamp()
    last_ok = now if ok else (previous or {}).get("last_ok", "")
    return {"ok": ok, "checked_at": now, "last_ok": last_ok, "error": error, "detail": detail, "interval": interval}


_RELEVANT: dict[str, tuple[tuple[str, ...], ...]] = {
    "weather": (("weather",), ("location",), ("app", "timezone")),
    "boards": (("transit",),),
    "warnings": (("weather", "warnings"), ("location",), ("ui", "weather")),
    "music": (("music",),),
    "system": (("ui", "system"),),
    "gaming": (("gaming",),),
}


def _relevant(config: dict[str, Any], name: str) -> list[Any]:
    values: list[Any] = []
    for path in _RELEVANT[name]:
        value: Any = config
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        values.append(value)
    return values


class StateManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self._lock = threading.RLock()
        self._config = copy.deepcopy(config)
        self._snapshot: dict[str, Any] = self._empty_snapshot(config)
        self._last_run: dict[str, float] = {}
        self._generation = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @staticmethod
    def _public_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({
            "ui": config.get("ui", {}),
            "screen": config.get("screen", {}),
            "stream": config.get("stream", {}),
            "location": config.get("location", {}),
            "timezone": config.get("app", {}).get("timezone", "Europe/Berlin"),
            "transit_provider": config.get("transit", {}).get("provider", "vrr"),
            "transit_stop_count": len(config.get("transit", {}).get("stops", [])),
            "transit_leave_mode": bool(config.get("transit", {}).get("leave_mode", True)),
            "music_source": config.get("music", {}).get("source", "off"),
            "warnings": config.get("weather", {}).get("warnings", "moderate"),
            "gaming": {"pause_camera": bool(config.get("gaming", {}).get("pause_camera"))},
        })

    def _empty_snapshot(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "generated_at": None,
            "config": self._public_runtime_config(config),
            "weather": {"ok": False, "text": "Wetter wird geladen"},
            "boards": [],
            "system": {"enabled": _system_enabled(config)},
            "music": {"enabled": False},
            "warnings": {"ok": True, "enabled": False, "alerts": []},
            "health": {},
        }

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="storchcam-state", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def update_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            if config != self._config:
                # Nur Quellen neu abfragen, deren Einstellungen sich geändert haben; ein Theme-Wechsel
                # soll nicht Wetter, VRR und DWD auf einmal anfragen.
                changed = {name for name in _RELEVANT if _relevant(self._config, name) != _relevant(config, name)}
                self._config = copy.deepcopy(config)
                self._snapshot["config"] = self._public_runtime_config(config)
                if not _music_enabled(config):
                    self._snapshot["music"] = {"enabled": False}
                for name in changed:
                    self._last_run.pop(name, None)
                    if name in {"weather", "boards", "warnings"}:
                        self._generation += 1

    def refresh_soon(self, name: str) -> None:
        """Provider beim nächsten Durchlauf (spätestens nach einer Sekunde) neu abfragen."""
        with self._lock:
            self._last_run.pop(name, None)

    def refresh_now(self, name: str) -> Any:
        """Einen Provider sofort abfragen, etwa nach einem Tipp auf Vor oder Zufall; liefert den neuen Wert."""
        config, generation = self._config_and_generation()
        for task_name, interval, task in _provider_tasks(config):
            if task_name == name:
                value = _fetch(name, task)
                self._store(name, value, interval, generation)
                return value
        return None

    def _store(self, name: str, value: Any, interval: int, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._snapshot[name] = value
            health = self._snapshot.setdefault("health", {})
            health[name] = _health(name, value, interval, health.get(name))
            self._snapshot["generated_at"] = _utc_timestamp()
            self._last_run[name] = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def _config_and_generation(self) -> tuple[dict[str, Any], int]:
        with self._lock:
            return copy.deepcopy(self._config), self._generation

    def refresh_all(self) -> None:
        config, generation = self._config_and_generation()
        tasks = _provider_tasks(config)
        updates: dict[str, Any] = {name: _fetch(name, task) for name, _interval, task in tasks}
        with self._lock:
            health = dict(self._snapshot.get("health", {}))
        for name, interval, _task in tasks:
            health[name] = _health(name, updates[name], interval, health.get(name))
        updates["health"] = health
        if not _system_enabled(config):
            updates["system"] = {"enabled": False}
        if not _music_enabled(config):
            updates["music"] = {"enabled": False}
        if not _warnings_enabled(config):
            updates["warnings"] = {"ok": True, "enabled": False, "alerts": []}
        updates["generated_at"] = _utc_timestamp()
        with self._lock:
            if generation == self._generation:
                self._snapshot.update(updates)
                completed = time.monotonic()
                self._last_run.update({name: completed for name, _interval, _task in tasks})
        self._ready.set()

    def _due(self, name: str, interval: int, now: float) -> bool:
        with self._lock:
            return now - self._last_run.get(name, 0) >= interval

    def _refresh_due(self) -> None:
        config, generation = self._config_and_generation()
        now = time.monotonic()
        if not _music_enabled(config):
            with self._lock:
                if self._snapshot.get("music", {}).get("enabled"):
                    self._snapshot["music"] = {"enabled": False}
        for name, interval, task in _provider_tasks(config):
            if not self._due(name, interval, now):
                continue
            self._store(name, _fetch(name, task), interval, generation)

    def _run(self) -> None:
        self._ready.set()
        self.refresh_all()
        while not self._stop.wait(1):
            self._refresh_due()
