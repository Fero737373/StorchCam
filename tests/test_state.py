from __future__ import annotations

import time
from pathlib import Path

import pytest

from storchcam.state import StateManager


def test_central_snapshot_and_config_invalidation(monkeypatch: pytest.MonkeyPatch, config: dict, tmp_path: Path) -> None:
    config["app"]["cache_dir"] = str(tmp_path)
    monkeypatch.setattr("storchcam.state.get_weather", lambda _cfg, force=False: {"ok": True, "text": "sonnig"})
    monkeypatch.setattr("storchcam.state.get_boards", lambda _cfg, force=False: [])
    manager = StateManager(config)
    manager.refresh_all()
    assert manager.snapshot()["weather"]["text"] == "sonnig"
    assert not (tmp_path / "storch_data.json").exists()
    changed = {**config, "location": {**config["location"], "label": "Hamburg"}}
    manager.update_config(changed)
    assert manager.snapshot()["config"]["location"]["label"] == "Hamburg"
    # Nur das Wetter hängt am Ort; die Abfahrten müssen nicht neu geholt werden.
    assert "weather" not in manager._last_run and "boards" in manager._last_run
    assert manager.snapshot()["health"]["weather"]["ok"] is True


def test_provider_failure_does_not_end_worker(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("provider offline")

    monkeypatch.setattr("storchcam.state.get_weather", fail)
    monkeypatch.setattr("storchcam.state.get_boards", fail)
    manager = StateManager(config)
    manager.start()
    assert manager.wait_ready(2)
    time.sleep(0.05)
    assert manager._thread is not None and manager._thread.is_alive()
    manager.stop()


def test_runtime_config_exposes_only_stop_count(config: dict) -> None:
    config["transit"]["stops"] = [{"title": "A", "station_name": "Musterstadt A", "station_id": "1"}]
    runtime = StateManager._public_runtime_config(config)
    assert runtime["transit_stop_count"] == 1
    assert "stops" not in runtime and "transit" not in runtime
