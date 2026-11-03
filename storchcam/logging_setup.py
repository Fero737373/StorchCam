"""Central rotating application logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config_store import cache_dir


def configure_logging(config: dict[str, Any]) -> Path:
    settings = config.get("logging", {})
    level = getattr(logging, str(settings.get("level", "INFO")).upper(), logging.INFO)
    target = cache_dir(config) / "storchcam.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        target,
        maxBytes=int(settings.get("max_bytes", 2_097_152)),
        backupCount=int(settings.get("backups", 3)),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)
    return target
