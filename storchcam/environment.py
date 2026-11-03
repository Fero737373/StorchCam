"""Umgebungsvariablen von StorchCam.

Bis Version 0.x hieß das Projekt BI-StorchCam; die alten Namen (BI_STORCHCAM_…) gelten weiter,
damit eine bestehende .env-Datei nach dem Update unverändert funktioniert.
"""

from __future__ import annotations

import os

PREFIX = "STORCHCAM_"
LEGACY_PREFIX = "BI_STORCHCAM_"


def env(name: str, default: str = "") -> str:
    """Wert von STORCHCAM_<name>, sonst vom früheren BI_STORCHCAM_<name>, sonst ``default``."""
    for prefix in (PREFIX, LEGACY_PREFIX):
        value = os.environ.get(f"{prefix}{name}")
        if value is not None and value != "":
            return value
    return default
