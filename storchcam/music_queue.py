"""The play queue of the Pi player: order, shuffle and repeat, without any I/O.

Kept free of mpv and network access so the rules (what comes next, what happens on
shuffle or when a track is removed) can be tested on their own.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

REPEAT_MODES = ("off", "all", "one")


@dataclass
class Track:
    """One playable song; ``id`` is the YouTube video ID."""

    id: str
    title: str = ""
    artist: str = ""
    cover: str = ""
    duration: int = 0
    liked: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlayQueue:
    tracks: list[Track] = field(default_factory=list)
    index: int = -1
    shuffle: bool = False
    repeat: str = "off"
    _original: list[Track] = field(default_factory=list, repr=False)
    _random: random.Random = field(default_factory=random.Random, repr=False)

    @property
    def current(self) -> Track | None:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    def replace(self, tracks: list[Track], start: int = 0, shuffle: bool = False) -> None:
        """Neue Warteschlange; mit Zufall beginnt sie beim gewählten Titel und mischt den Rest."""
        self.tracks = list(tracks)
        self._original = list(tracks)
        self.index = min(max(start, 0), len(self.tracks) - 1) if self.tracks else -1
        self.shuffle = False
        if shuffle:
            self.set_shuffle(True)

    def clear(self) -> None:
        self.tracks, self._original, self.index = [], [], -1

    def next_index(self, *, natural: bool) -> int | None:
        """Position des nächsten Titels; ``natural`` = der laufende Titel ist zu Ende gespielt."""
        if not self.tracks:
            return None
        if natural and self.repeat == "one":
            return self.index
        if self.index + 1 < len(self.tracks):
            return self.index + 1
        # Am Ende: bei „alle“ von vorn; „einen“ wiederholt nur von selbst, „Vor“ springt trotzdem weiter.
        return 0 if self.repeat != "off" else None

    def previous_index(self) -> int | None:
        if not self.tracks:
            return None
        if self.index > 0:
            return self.index - 1
        return len(self.tracks) - 1 if self.repeat == "all" else None

    def jump(self, position: int) -> Track:
        if not 0 <= position < len(self.tracks):
            raise IndexError("Diesen Titel gibt es in der Warteschlange nicht")
        self.index = position
        return self.tracks[position]

    def remove(self, position: int) -> None:
        """Titel entfernen; der laufende Titel lässt sich nicht entfernen."""
        if not 0 <= position < len(self.tracks) or position == self.index:
            raise IndexError("Dieser Titel lässt sich nicht entfernen")
        removed = self.tracks.pop(position)
        if removed in self._original:
            self._original.remove(removed)
        if position < self.index:
            self.index -= 1

    def move(self, source: int, target: int) -> None:
        if not (0 <= source < len(self.tracks) and 0 <= target < len(self.tracks)):
            raise IndexError("Ungültige Position in der Warteschlange")
        track = self.tracks.pop(source)
        self.tracks.insert(target, track)
        if source == self.index:
            self.index = target
        elif source < self.index <= target:
            self.index -= 1
        elif target <= self.index < source:
            self.index += 1

    def set_shuffle(self, enabled: bool) -> None:
        """Mischt nur, was noch kommt; ausschalten stellt die ursprüngliche Reihenfolge wieder her."""
        if enabled == self.shuffle:
            return
        self.shuffle = enabled
        current = self.current
        if enabled:
            self._original = list(self.tracks)
            upcoming = self.tracks[self.index + 1:]
            self._random.shuffle(upcoming)
            self.tracks = self.tracks[: self.index + 1] + upcoming
            return
        self.tracks = list(self._original) or self.tracks
        self.index = next((i for i, track in enumerate(self.tracks) if track is current), self.index)

    def set_repeat(self, mode: str) -> None:
        if mode not in REPEAT_MODES:
            raise ValueError("Wiederholen kennt nur off, all und one")
        self.repeat = mode

    def window(self, before: int = 3, after: int = 40) -> list[dict[str, Any]]:
        """Ausschnitt um den laufenden Titel für die Anzeige, mit Position zum Springen."""
        start = max(0, self.index - before)
        return [{"position": position, **track.as_dict()} for position, track in enumerate(self.tracks[start: self.index + after + 1], start)]
