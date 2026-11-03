"""Switch the screen's backlight for the night mode, where the system allows it.

Raspberry Pi touch displays expose ``/sys/class/backlight/*/bl_power`` (0 = on, 4 = off).
Inside a container sysfs is usually read-only; then the kiosk only dims the picture.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)
BACKLIGHT_ROOT = Path("/sys/class/backlight")


def _controls() -> list[Path]:
    try:
        return sorted(path / "bl_power" for path in BACKLIGHT_ROOT.iterdir() if (path / "bl_power").exists())
    except OSError:
        return []


def backlight_available() -> bool:
    return any(os.access(control, os.W_OK) for control in _controls())


def set_backlight(on: bool) -> bool:
    """True, wenn mindestens ein Display geschaltet wurde."""
    switched = False
    for control in _controls():
        try:
            control.write_text("0" if on else "4", encoding="ascii")
            switched = True
        except OSError as exc:
            LOGGER.debug("Hintergrundbeleuchtung %s nicht schaltbar: %s", control, exc)
    if not switched:
        LOGGER.info("Hintergrundbeleuchtung nicht schaltbar; der Nachtmodus dimmt nur das Bild")
    return switched
