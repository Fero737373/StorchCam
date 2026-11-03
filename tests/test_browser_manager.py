from __future__ import annotations

import os

import pytest

from storchcam import kiosk


def test_session_detection() -> None:
    assert kiosk.session_type({"XDG_SESSION_TYPE": "wayland"}) == "wayland"
    assert kiosk.session_type({"DISPLAY": ":0"}) == "x11"
    assert kiosk.session_type({}) == "unknown"


def test_browser_arguments_are_chromium_family_and_session_specific(tmp_path, config: dict) -> None:
    config["kiosk"]["profile_dir"] = str(tmp_path / "profile")
    wayland = kiosk.browser_arguments("chromium", "/usr/bin/chromium", "http://127.0.0.1:8000", config, {"XDG_SESSION_TYPE": "wayland"})
    x11 = kiosk.browser_arguments("edge", "/usr/bin/edge", "http://127.0.0.1:8000", config, {"DISPLAY": ":0"})
    if os.name == "nt":
        assert not any(argument.startswith("--ozone-platform=") for argument in wayland)
        assert not any(argument.startswith("--ozone-platform=") for argument in x11)
    else:
        assert "--ozone-platform=wayland" in wayland
        assert "--ozone-platform=x11" in x11
    with pytest.raises(ValueError):
        kiosk.browser_arguments("firefox", "/usr/bin/firefox", "http://localhost", config, {})


def test_watchdog_has_bounded_backoff(config: dict) -> None:
    config["kiosk"]["browser_restart_seconds"] = 2
    config["kiosk"]["browser_restart_max_seconds"] = 8
    config["kiosk"]["browser_max_failures"] = 3
    manager = kiosk.BrowserManager(config, "http://127.0.0.1:8000")
    manager._schedule_failure(10)
    assert manager.next_start == 12 and manager.delay == 4
    manager._schedule_failure(12)
    assert manager.delay == 8 and not manager.exhausted
    manager._schedule_failure(16)
    assert manager.exhausted


def test_missing_browser_exhausts_without_webbrowser_fallback(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    monkeypatch.setattr(kiosk, "find_browser", lambda _configured="auto": None)
    manager = kiosk.BrowserManager(config, "http://127.0.0.1:8000")
    assert not manager.start() and manager.exhausted and manager.process is None


def test_browser_log_rotation_is_bounded(tmp_path) -> None:
    target = tmp_path / "chromium.log"
    target.write_bytes(b"x" * 100)
    kiosk._rotate_file(target, 50, 2)
    assert not target.exists()
    assert (tmp_path / "chromium.log.1").exists()
    target.write_bytes(b"y" * 100)
    kiosk._rotate_file(target, 50, 2)
    assert (tmp_path / "chromium.log.1").read_bytes().startswith(b"y")
    assert (tmp_path / "chromium.log.2").read_bytes().startswith(b"x")


def test_generic_profile_does_not_change_display(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    if os.name == "nt":
        pytest.skip("X11-only assertion")
    config["screen"]["hardware_profile"] = "generic"
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    called = []
    monkeypatch.setattr(kiosk, "_run_checked", lambda command, env: called.append(command))
    kiosk.prepare_display(config)
    assert called == []
