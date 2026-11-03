"""What was played recently (for „Weiterhören“) and the sleep timer that switches music off.

The history is a small JSON file next to the configuration so it survives restarts without
touching the configuration itself on every play. The sleep timer lives in memory only.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config_store import config_path

LOGGER = logging.getLogger(__name__)
HISTORY_SIZE = 12
SOURCES = {"radio", "ytmusic", "spotify"}
FIELDS = ("source", "kind", "id", "title", "subtitle", "cover")

_lock = threading.Lock()
_sleep: dict[str, Any] = {"end": 0.0, "label": "", "timer": None}


def history_path() -> Path:
    return config_path().parent / "history.json"


def recent() -> list[dict[str, Any]]:
    try:
        data = json.loads(history_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("source") in SOURCES][:HISTORY_SIZE] if isinstance(data, list) else []


def remember(entry: dict[str, Any]) -> None:
    """Eintrag vorne anfügen; derselbe Titel rückt nur nach vorn."""
    if entry.get("source") not in SOURCES or not entry.get("id"):
        return
    clean: dict[str, Any] = {key: str(entry.get(key) or "")[:300] for key in FIELDS}
    if isinstance(entry.get("station"), dict):
        clean["station"] = {key: entry["station"].get(key) for key in ("uuid", "name", "url", "country", "codec", "bitrate", "tags")}
    with _lock:
        items = [item for item in recent() if (item.get("source"), item.get("id")) != (clean["source"], clean["id"])]
        items.insert(0, clean)
        target = history_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".history.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(items[:HISTORY_SIZE], handle, ensure_ascii=False)
            os.replace(temporary, target)
        except OSError as exc:
            LOGGER.warning("Verlauf nicht gespeichert: %s", exc)
            Path(temporary).unlink(missing_ok=True)


def set_sleep(minutes: int, label: str, switch_off: Callable[[], None]) -> float:
    """Musik nach ``minutes`` ausschalten; 0 hebt den Timer auf. Gibt das Ende als Unix-Zeit zurück."""
    with _lock:
        if _sleep["timer"]:
            _sleep["timer"].cancel()
        _sleep.update({"end": 0.0, "label": "", "timer": None})
        if minutes <= 0:
            return 0.0

        def fire() -> None:
            with _lock:
                _sleep.update({"end": 0.0, "label": "", "timer": None})
            LOGGER.info("Ausschalt-Timer abgelaufen, Musik aus")
            try:
                switch_off()
            except Exception:  # Timer darf nie still sterben, aber auch nichts anderes mitreißen
                LOGGER.exception("Musik ließ sich nicht ausschalten")

        timer = threading.Timer(minutes * 60, fire)
        timer.daemon = True
        end = time.time() + minutes * 60
        _sleep.update({"end": end, "label": label, "timer": timer})
    # Außerhalb der Sperre starten: der Timer greift beim Auslösen selbst auf sie zu.
    timer.start()
    return end


def sleep_state() -> dict[str, Any]:
    with _lock:
        return {"sleep_end": int(_sleep["end"] * 1000), "sleep_label": _sleep["label"]} if _sleep["end"] else {"sleep_end": 0, "sleep_label": ""}
