from __future__ import annotations

import copy

import pytest

from storchcam.config_store import validate_config
from storchcam.defaults import DEFAULT_CONFIG


@pytest.fixture
def config() -> dict:
    # DWD-Warnungen fragen sonst im Hintergrund das Netz an; eigene Tests schalten sie gezielt ein.
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["weather"]["warnings"] = "off"
    return validate_config(raw)


@pytest.fixture(autouse=True)
def fresh_page_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Test beginnt ohne gemerkte YouTube-Music-Seiten."""
    from storchcam import library

    monkeypatch.setattr(library.YouTubeMusicLibrary, "pages", library.PageCache())
