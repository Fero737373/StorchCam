from __future__ import annotations

import pytest

from storchcam import system_status


def test_unavailable_windows_values_are_none_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_status.platform, "system", lambda: "Windows")
    monkeypatch.setattr(system_status.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(system_status, "_windows_ram", lambda: None)
    monkeypatch.setattr(system_status, "get_temp", lambda: None)
    result = system_status.get_system_status()
    assert result["cpu"] is None
    assert result["ram"] is None
    assert result["temp"] is None
