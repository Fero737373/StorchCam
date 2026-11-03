from __future__ import annotations

import json
from pathlib import Path

import pytest

from storchcam.config_store import (
    ConfigError,
    load_config,
    migrate_config,
    platform_app_dir,
    save_config,
    validate_config,
)
from storchcam.defaults import CONFIG_SCHEMA_VERSION


def test_valid_default_and_example(config: dict) -> None:
    assert config["app"]["config_schema_version"] == CONFIG_SCHEMA_VERSION
    example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
    assert validate_config(example)["screen"]["rotation"] == "none"


def test_unknown_and_dangerous_fields_are_rejected(config: dict) -> None:
    config["server"]["shell_command"] = "whoami"
    with pytest.raises(ConfigError, match="Unbekannte Felder"):
        validate_config(config)


def test_external_binding_requires_pin(config: dict) -> None:
    config["server"]["host"] = "0.0.0.0"
    with pytest.raises(ConfigError, match="Admin-PIN"):
        validate_config(config)


def test_firefox_path_is_rejected(config: dict, tmp_path: Path) -> None:
    config["kiosk"]["browser"] = str(tmp_path / "firefox")
    with pytest.raises(ConfigError, match="Chromium, Chrome oder Edge"):
        validate_config(config)


def test_atomic_save_backup_and_restore(tmp_path: Path, config: dict) -> None:
    target = tmp_path / "config.json"
    save_config(config, target)
    changed = json.loads(target.read_text(encoding="utf-8"))
    changed["location"]["label"] = "Hamburg"
    save_config(changed, target)
    assert json.loads(target.with_suffix(".json.bak").read_text(encoding="utf-8"))["location"]["label"] == "Berlin"
    target.write_text("{ kaputt", encoding="utf-8")
    restored = load_config(target)
    assert restored["location"]["label"] == "Berlin"


def test_invalid_config_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("not-json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(target)
    assert target.read_text(encoding="utf-8") == "not-json"


def test_migration_removes_unsafe_hardware_and_placeholder(config: dict) -> None:
    old = {
        **config,
        "app": {**config["app"], "config_schema_version": 2},
        "screen": {"profile": "auto", "output": "DSI-2", "rotation": "right", "touch_matrix": "unsafe"},
        "transit": {**config["transit"], "stops": [{"title": "BEISPIEL", "station_id": "1", "station_name": "Demo"}]},
    }
    migrated, changed = migrate_config(old)
    assert changed
    assert migrated["screen"]["output"] == "auto"
    assert migrated["screen"]["rotation"] == "none"
    assert migrated["transit"]["stops"] == []


def test_platform_path_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platform_app_dir("nt") == tmp_path / "StorchCam"


def test_old_config_with_radar_is_migrated(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    old = {
        "location": {"label": "Musterstadt", "latitude": 52.5200, "longitude": 13.4050},
        "ui": {"theme": "dark", "radar": {"enabled": True, "width": 280, "height": 190, "zoom": 12, "opacity": 0.92}},
        "radar": {"provider": "rainviewer", "refresh_seconds": 300},
    }
    target.write_text(json.dumps(old), encoding="utf-8")
    loaded = load_config(target)
    assert "radar" not in loaded and "radar" not in loaded["ui"]
    assert "radar" not in json.loads(target.read_text(encoding="utf-8"))


@pytest.mark.parametrize("theme", ["y2k", "dagobert"])
def test_picture_themes_are_valid(config: dict, theme: str) -> None:
    validated = validate_config({**config, "ui": {**config["ui"], "theme": theme}})
    assert validated["ui"]["theme"] == theme


def test_sky_mode_defaults_to_auto_and_rejects_unknown(config: dict) -> None:
    assert validate_config(config)["ui"]["sky"] == "auto"
    assert validate_config({**config, "ui": {**config["ui"], "sky": "night"}})["ui"]["sky"] == "night"
    with pytest.raises(ConfigError):
        validate_config({**config, "ui": {**config["ui"], "sky": "dusk"}})
