from __future__ import annotations

from datetime import datetime

import pytest

from storchcam.providers import transit_vrr_smart as transit


def stop(station_id: str = "20000001") -> dict:
    return {
        "title": "Marktplatz",
        "station_name": "Musterstadt Marktplatz",
        "station_id": station_id,
        "line_filter": ["31", "N"],
        "nightbus_only": False,
        "hide_if_empty": False,
        "max_rows": 3,
    }


def departures() -> dict:
    return {
        "departureData": [
            {"lineNumber": "31", "direction": "Musterstadt, Nordpark", "countdown": 2},
            {"lineNumber": "4", "direction": "Universität", "countdown": 3},
            {"lineNumber": "N2", "direction": "Westpark", "countdown": 5},
        ]
    }


def test_cache_key_includes_complete_stop_configuration(config: dict) -> None:
    config["transit"]["stops"] = [stop()]
    before = transit.cache_key(config)
    config["transit"]["stops"][0]["station_id"] = "other"
    assert transit.cache_key(config) != before


def test_line_and_nightbus_filters(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    transit.clear_cache()
    config["transit"]["stops"] = [stop()]
    monkeypatch.setattr(transit, "_fetch_json", lambda _url, _data=None, timeout=12: departures())
    board = transit.get_boards(config, force=True)[0]
    assert [row["line"] for row in board["rows"]] == ["31", "N2"]
    config["transit"]["stops"][0]["nightbus_only"] = True
    board = transit.get_boards(config, force=True)[0]
    assert [row["line"] for row in board["rows"]] == ["N2"]


def test_multiple_stops_and_error_do_not_mutate_config(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    transit.clear_cache()
    config["transit"]["stops"] = [stop("one"), {**stop("two"), "title": "Zwei"}]
    calls = 0

    def fake(_url: str, data=None, timeout=12):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("VRR offline")
        return departures()

    monkeypatch.setattr(transit, "_fetch_json", fake)
    boards = transit.get_boards(config, force=True)
    assert len(boards) == 2 and boards[1]["ok"] is False
    assert "last_error" not in config["transit"]["stops"][1]


def test_rows_expose_raw_minutes_and_boards_checked_at(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    transit.clear_cache()
    config["transit"]["stops"] = [{**stop(), "line_filter": []}, {**stop("two"), "title": "Zwei"}]
    data = {
        "departureData": [
            {"lineNumber": "31", "direction": "Nordpark", "countdown": "0"},
            {"lineNumber": "4", "direction": "Universität", "countdown": 65},
            {"lineNumber": "N2", "direction": "Westpark", "countdown": None},
        ]
    }
    calls = 0

    def fake(_url: str, _data=None, timeout=12):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("VRR offline")
        return data

    monkeypatch.setattr(transit, "_fetch_json", fake)
    boards = transit.get_boards(config, force=True)
    assert [(row["mins"], row["minutes"]) for row in boards[0]["rows"]] == [("jetzt", 0), ("1h 05", 65), ("–", None)]
    for board in boards:
        assert datetime.fromisoformat(board["checked_at"]).tzinfo is not None
    assert boards[1]["ok"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Altstadt, Lindenstraße", "Lindenstraße"),
        ("Musterstadt, Lindenallee/Ringstr", "Lindenallee/Ringstr"),
        ("Musterstadt Marktplatz", "Marktplatz"),
        ("Musterstadt", "Musterstadt"),
        ("Nachbarort Bahnhof", "Nachbarort Bahnhof"),
        ("Universität", "Universität"),
    ],
)
def test_short_name_drops_city_and_district(raw: str, expected: str) -> None:
    assert transit.short_name(raw, "Musterstadt") == expected
    assert transit.city_of("Musterstadt, Marktplatz") == "Musterstadt" and transit.city_of("Marktplatz") == ""


def test_board_title_uses_vrr_station_name_unless_customised(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    transit.clear_cache()
    auto = {**stop(), "title": "Gellershagen Lindenstraße", "station_name": "Gellershagen Lindenstraße"}
    config["transit"]["stops"] = [auto, {**stop("two"), "title": ""}, {**stop("three"), "title": "Zuhause"}]
    data = {**departures(), "stationName": "Altstadt, Lindenstraße"}
    monkeypatch.setattr(transit, "_fetch_json", lambda _url, _data=None, timeout=12: data)
    boards = transit.get_boards(config, force=True)
    assert [board["title"] for board in boards] == ["Lindenstraße", "Lindenstraße", "Zuhause"]
    assert boards[0]["rows"][0]["target"] == "Nordpark"
