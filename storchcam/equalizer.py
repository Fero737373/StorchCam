"""Six-band equalizer for everything mpv plays on the Pi, plus a level meter for the animation.

mpv runs the bands as ffmpeg ``equalizer`` filters. A negative pre-gain of the largest boost
keeps loud songs from clipping. A second filter (``astats``) measures the level of each audio
frame; mpv exposes it as ``af-metadata/lv`` and the player tab turns it into moving bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

BANDS = (60, 150, 400, 1000, 2400, 15000)
LIMIT_DB = 12.0
LEVEL_LABEL = "lv"
# Voreinstellungen in dB je Band (60 Hz … 15 kHz).
PRESETS: dict[str, tuple[float, ...]] = {
    "flat": (0, 0, 0, 0, 0, 0),
    "bass": (6, 4, 1, 0, 0, 0),
    "pop": (-1, 2, 4, 3, 1, -1),
    "rock": (4, 2, -2, -1, 2, 4),
    "klassik": (3, 1, 0, 0, 1, 3),
    "sprache": (-4, -2, 2, 4, 3, 0),
    "akustik": (2, 1, 1, 2, 2, 2),
    "nacht": (-3, -1, 0, 1, 0, -4),
}
PRESET_NAMES = {
    "flat": "Neutral", "bass": "Mehr Bass", "pop": "Pop", "rock": "Rock", "klassik": "Klassik",
    "sprache": "Sprache", "akustik": "Akustik", "nacht": "Nachts leise",
}
SILENCE_DB = -60.0


@dataclass(frozen=True)
class Equalizer:
    gains: tuple[float, ...] = PRESETS["flat"]

    def __post_init__(self) -> None:
        if len(self.gains) != len(BANDS):
            raise ValueError(f"Der Klangregler hat genau {len(BANDS)} Bänder")
        if any(not isinstance(gain, (int, float)) or isinstance(gain, bool) or math.isnan(gain) or abs(gain) > LIMIT_DB for gain in self.gains):
            raise ValueError(f"Jedes Band liegt zwischen -{LIMIT_DB:g} und +{LIMIT_DB:g} dB")

    @classmethod
    def preset(cls, name: str) -> Equalizer:
        if name not in PRESETS:
            raise ValueError("Unbekannte Voreinstellung")
        return cls(PRESETS[name])

    @property
    def is_flat(self) -> bool:
        return all(gain == 0 for gain in self.gains)

    def filter_chain(self) -> str:
        """Wert für mpv ``af``: Bänder (falls nötig) und immer die Pegelmessung."""
        meter = f"@{LEVEL_LABEL}:lavfi=[astats=metadata=1:reset=1]"
        if self.is_flat:
            return meter
        bands = ",".join(f"equalizer=f={frequency}:t=q:w=1.1:g={gain:g}" for frequency, gain in zip(BANDS, self.gains, strict=True))
        headroom = max(0.0, max(self.gains))
        return f"@eq:lavfi=[{bands},volume={-headroom:g}dB],{meter}"


def level(metadata: Any) -> float:
    """Pegel 0…1 aus den astats-Metadaten von mpv (RMS in dB, -60 dB = Stille)."""
    if not isinstance(metadata, dict):
        return 0.0
    raw = metadata.get("lavfi.astats.Overall.RMS_level") or metadata.get("lavfi.astats.1.RMS_level")
    try:
        decibel = float(str(raw))
    except (TypeError, ValueError):
        return 0.0
    if math.isinf(decibel) or math.isnan(decibel):
        return 0.0
    return round(min(1.0, max(0.0, (decibel - SILENCE_DB) / -SILENCE_DB)), 3)
