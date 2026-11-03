"""Radio, DWD warnings, pairing, listening history, config schema 5 and the new endpoints."""

from __future__ import annotations

import copy
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from storchcam import listening, pairing, player
from storchcam.config_store import ConfigError, migrate_config, validate_config
from storchcam.defaults import DEFAULT_CONFIG
from storchcam.equalizer import Equalizer
from storchcam.music_queue import Track
from storchcam.providers import radio, transit_vrr_smart, warnings_dwd, weather_smart
from storchcam.server import StorchHandler
from tests.test_server import post_json, request, running_server

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
STATION = {"uuid": "11111111-aaaa", "name": "WDR 2", "url": "https://wdr.example.org/wdr2.mp3", "country": "Germany", "codec": "MP3", "bitrate": 128, "tags": ["pop"]}


# ---------- Radio Browser ----------

def test_station_parsing_skips_broken_entries() -> None:
    raw = {"stationuuid": "abc-123-def", "name": "  WDR   2 ", "url": "http://x/a.pls", "url_resolved": "https://wdr.example.org/live.mp3", "tags": "pop,news,,", "bitrate": "128", "votes": 5}
    station = radio.station(raw)
    assert station is not None and station["name"] == "WDR 2" and station["url"] == "https://wdr.example.org/live.mp3"
    assert station["tags"] == ["pop", "news"] and station["bitrate"] == 128
    assert radio.station({"stationuuid": "x", "url": "ftp://nope"}) is None
    assert radio.station({"url": "https://ok"}) is None


def test_search_merges_name_and_tag_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(path: str, params: dict[str, str]) -> Any:
        calls.append(dict(params))
        if "tag" in params:
            return [{"stationuuid": "b", "name": "Jazz FM", "url": "https://j/1", "votes": 50}, {"stationuuid": "a", "name": "Jazzradio", "url": "https://j/2", "votes": 10}]
        return [{"stationuuid": "a", "name": "Jazzradio", "url": "https://j/2", "votes": 10}]

    monkeypatch.setattr(radio, "_get", fake_get)
    found = radio.search("Jazz")
    assert [item["uuid"] for item in found] == ["b", "a"]
    assert calls[0]["name"] == "Jazz" and calls[1]["tag"] == "jazz"
    calls.clear()
    radio.search("", "news")
    assert calls == [{"hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": "30", "tag": "news"}]


def test_unreachable_mirrors_raise_radio_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(radio, "servers", lambda: ["one.example", "two.example"])
    monkeypatch.setattr(radio, "fetch_json", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(radio.RadioError):
        radio.search("WDR")


# ---------- DWD-Warnungen ----------

def alert(severity: str, onset: datetime, expires: datetime, headline: str = "Amtliche WARNUNG vor STURMBÖEN") -> dict[str, Any]:
    return {"id": 1, "alert_id": severity, "status": "actual", "severity": severity, "onset": onset.isoformat(), "expires": expires.isoformat(), "headline_de": headline, "event_de": "STURMBÖEN"}


def test_warnings_filter_by_level_time_and_order() -> None:
    alerts = [
        alert("minor", NOW - timedelta(hours=1), NOW + timedelta(hours=2), "Amtliche WARNUNG vor FROST"),
        alert("severe", NOW + timedelta(hours=1), NOW + timedelta(hours=5)),
        alert("moderate", NOW - timedelta(hours=3), NOW - timedelta(minutes=1)),  # schon vorbei
        alert("extreme", NOW + timedelta(hours=12), NOW + timedelta(hours=14)),  # noch zu weit weg
    ]
    found = warnings_dwd.relevant(alerts, "moderate", NOW)
    assert [item["severity"] for item in found] == ["severe"]
    assert found[0]["headline"] == "Amtliche Warnung vor Sturmböen"
    assert [item["severity"] for item in warnings_dwd.relevant(alerts, "minor", NOW)] == ["severe", "minor"]


def test_warnings_off_and_outside_germany(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    assert warnings_dwd.get_warnings(config) == {"ok": True, "enabled": False, "alerts": []}
    config["weather"]["warnings"] = "minor"

    def not_found(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise urllib.error.HTTPError("https://api.brightsky.dev/alerts", 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(warnings_dwd, "fetch_json_object", not_found)
    assert warnings_dwd.get_warnings(config) == {"ok": True, "enabled": True, "covered": False, "alerts": []}
    monkeypatch.setattr(warnings_dwd, "fetch_json_object", lambda *_a, **_k: {"alerts": [alert("moderate", NOW - timedelta(hours=1), NOW + timedelta(hours=3))], "location": {"name": "Stadt Musterstadt"}})
    result = warnings_dwd.get_warnings(config, now=NOW)
    assert result["cell"] == "Stadt Musterstadt" and len(result["alerts"]) == 1


# ---------- Wetter und Abfahrten ----------

def test_hourly_series_starts_with_the_running_hour_and_sun_times() -> None:
    hourly = {
        "time": ["2026-11-02T10:00", "2026-11-02T11:00", "2026-11-02T12:00", "2026-11-02T13:00"],
        "temperature_2m": [7.4, 8.6, 9.5, None],
        "precipitation_probability": [10, 55, 60, 5],
        "precipitation": [0, 0.4, 1.2, 0],
        "wind_speed_10m": [12.3, 14, 15, 9],
        "weather_code": [3, 61, 63, 2],
        "is_day": [1, 1, 1, 1],
    }
    now = datetime(2026, 11, 2, 11, 40, tzinfo=timezone(timedelta(hours=1)))
    series = weather_smart.hourly_series(hourly, "Europe/Berlin", 25, now)
    assert [item["hour"] for item in series] == [11, 12, 13]
    assert series[0] == {**series[0], "temp": 9, "probability": 55, "wind": 14, "code": 61, "is_day": True}
    assert series[2]["temp"] is None
    sun = weather_smart.sun_times({"sunrise": ["2026-11-02T07:14"], "sunset": ["2026-11-02T16:52"]}, "Europe/Berlin")
    assert sun == [{"rise": "2026-11-02T07:14:00+01:00", "set": "2026-11-02T16:52:00+01:00"}]


def test_departures_carry_delay_type_times_and_walk(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    transit_vrr_smart.clear_cache()
    config["transit"]["stops"] = [{"title": "", "station_name": "Musterstadt, Marktplatz", "station_id": "1", "line_filter": ["4"], "nightbus_only": False, "hide_if_empty": False, "max_rows": 3, "walk_minutes": 4}]
    data = {"stationName": "Musterstadt, Marktplatz", "departureData": [
        {"lineNumber": "4", "type": 4, "direction": "Südring", "countdown": "7", "delay": "3", "isRealTime": True, "fullTime": "1762081380", "orgFullTime": "1762081200"},
        {"lineNumber": "N2", "type": 5, "direction": "Westpark", "countdown": "40"},
        {"lineNumber": "RE6", "type": 1, "direction": "Köln", "countdown": "12"},
    ]}
    monkeypatch.setattr(transit_vrr_smart, "_fetch_json", lambda _url, _data=None, timeout=12: data)
    board = transit_vrr_smart.get_boards(config, force=True)[0]
    assert board["walk_minutes"] == 4 and [row["line"] for row in board["rows"]] == ["4"]
    row = board["rows"][0]
    assert (row["type"], row["delay"], row["realtime"], row["at"], row["planned"]) == ("tram", 3, True, 1762081380, 1762081200)
    assert {item["line"]: item["type"] for item in board["lines"]} == {"4": "tram", "N2": "night", "RE6": "train"}


# ---------- Kopplung ----------

def test_pairing_code_works_once_and_dies_after_wrong_tries(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    config["server"]["public_url"] = "http://raspberrypi.local:8080"
    status = pairing.start(config)
    assert status["active"] and len(status["code"]) == 6 and status["link"].startswith("http://raspberrypi.local:8080/?koppeln=")
    assert status["qr"].startswith("data:image/svg+xml")
    code = status["code"]
    assert pairing.claim(f"{code[:3]} {code[3:]}") is True
    assert pairing.claim(code) is False
    code = pairing.start(config)["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(pairing.MAX_ATTEMPTS):
        assert pairing.claim(wrong) is False
    assert pairing.claim(code) is False and pairing.status(config)["active"] is False


def test_public_url_from_environment(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    monkeypatch.setenv("STORCHCAM_PUBLIC_URL", "http://pi.local:8080/")
    assert pairing.public_url(config) == "http://pi.local:8080"


# ---------- Verlauf und Ausschalt-Timer ----------

def test_history_moves_repeats_to_the_front(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORCHCAM_CONFIG", str(tmp_path / "config.json"))
    listening.remember({"source": "radio", "kind": "station", "id": "a", "title": "WDR 2", "station": STATION})
    listening.remember({"source": "ytmusic", "kind": "song", "id": "b", "title": "Song"})
    listening.remember({"source": "radio", "kind": "station", "id": "a", "title": "WDR 2"})
    listening.remember({"source": "nope", "id": "c"})
    assert [item["id"] for item in listening.recent()] == ["a", "b"]
    for index in range(20):
        listening.remember({"source": "spotify", "kind": "track", "id": f"t{index}", "title": "x"})
    assert len(listening.recent()) == listening.HISTORY_SIZE


def test_sleep_timer_switches_off_and_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[str] = []

    class InstantTimer:
        daemon = False

        def __init__(self, _seconds: float, function: Any) -> None:
            self.function = function

        def start(self) -> None:
            self.function()

        def cancel(self) -> None:
            return None

    assert listening.set_sleep(0, "", lambda: fired.append("x")) == 0.0
    assert listening.sleep_state() == {"sleep_end": 0, "sleep_label": ""}
    monkeypatch.setattr(listening.threading, "Timer", InstantTimer)
    listening.set_sleep(15, "15", lambda: fired.append("aus"))
    assert fired == ["aus"]


# ---------- Radio im Player ----------

class FakeMpv:
    """Steht für mpv: merkt sich Befehle und liefert feste Eigenschaften."""

    def __init__(self, _on_event: Any = None, _volume: int = 100, properties: dict[str, Any] | None = None) -> None:
        self.commands: list[tuple[Any, ...]] = []
        self.properties = {"pause": False, "metadata": {"icy-title": "Coldplay - Viva la Vida"}, "idle-active": False, "time-pos": 12.0, "duration": 200.0}
        self.properties.update(properties or {})

    def alive(self) -> bool:
        return True

    def command(self, *arguments: Any, timeout: float = 5) -> Any:
        self.commands.append(arguments)

    def get(self, name: str) -> Any:
        return self.properties.get(name)

    def close(self) -> None:
        return None


class InlineThread:
    def __init__(self, target: Any, args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None, **_options: Any) -> None:
        self.run = lambda: target(*args, **(kwargs or {}))

    def start(self) -> None:
        self.run()


def make_player(monkeypatch: pytest.MonkeyPatch) -> tuple[player.PiPlayer, FakeMpv]:
    mpv = FakeMpv()
    covers = player.RadioCovers(fetch=lambda *_a, **_k: {"results": [{"artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg"}]})
    monkeypatch.setattr(player.threading, "Thread", InlineThread)
    resolver = type("Resolver", (), {"url": lambda self, video_id: f"https://audio/{video_id}", "prefetch": lambda self, video_id: None})()
    return player.PiPlayer(client_factory=lambda *_a: mpv, resolver=resolver, covers=covers), mpv


def test_radio_playback_reports_station_song_cover_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    pi, mpv = make_player(monkeypatch)
    pi.play_radio(STATION)
    assert ("loadfile", STATION["url"], "replace") in mpv.commands
    assert ("set_property", "af", "@lv:lavfi=[astats=metadata=1:reset=1]") in mpv.commands
    first = pi.now_playing()
    assert first is not None and first["source"] == "radio" and first["artist"] == "Coldplay – Viva la Vida"
    second = pi.now_playing()
    assert second is not None and second["cover"] == "https://is1.mzstatic.com/a/300x300bb.jpg"
    assert [item["title"] for item in second["recent_titles"]] == ["Coldplay – Viva la Vida"]
    assert pi.current_station()["uuid"] == STATION["uuid"]
    with pytest.raises(player.PlayerError):
        pi.control("next")
    with pytest.raises(player.PlayerError):
        pi.play_radio({"url": "file:///etc/passwd"})


def test_queue_plays_from_a_position_and_follows_repeat_and_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    pi, mpv = make_player(monkeypatch)
    songs = [Track(id=f"v{index}", title=f"Song {index}") for index in range(4)]
    pi.play_tracks(songs, start=1)
    assert mpv.commands[-2] == ("loadfile", "https://audio/v1", "replace")
    pi.control("repeat", "one")
    pi.advance(1, natural=True)
    assert pi.queue.index == 1
    pi.control("repeat", "all")
    pi.control("jump", 3)
    pi.advance(1, natural=True)
    assert pi.queue.current.id == "v0"
    pi.control("move", {"from": 3, "to": 1})
    assert [track.id for track in pi.queue.tracks] == ["v0", "v3", "v1", "v2"]
    pi.control("remove", 2)
    now = pi.now_playing()
    assert now is not None and [item["id"] for item in now["queue"]] == ["v0", "v3", "v2"] and now["repeat"] == "all"
    with pytest.raises(player.PlayerError):
        pi.control("remove", 0)  # der laufende Titel
    pi.control("seek", 42)
    assert ("seek", 42.0, "absolute") in mpv.commands


def test_equalizer_is_sent_to_mpv_and_level_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    pi, mpv = make_player(monkeypatch)
    pi.play_radio(STATION)
    pi.set_equalizer(Equalizer.preset("bass"))
    chain = mpv.commands[-1][2]
    assert chain.startswith("@eq:lavfi=[equalizer=f=60:t=q:w=1.1:g=6,") and "volume=-6dB" in chain and chain.endswith("@lv:lavfi=[astats=metadata=1:reset=1]")
    mpv.properties["af-metadata/lv"] = {"lavfi.astats.Overall.RMS_level": "-15.0"}
    assert pi.level() == 0.75
    mpv.properties["af-metadata/lv"] = {"lavfi.astats.Overall.RMS_level": "-inf"}
    assert pi.level() == 0.0


# ---------- Konfiguration Version 5 ----------

def test_v4_config_migrates_audio_distance_and_setup() -> None:
    old = {"app": {"config_schema_version": 4}, "stream": {"url": "https://www.youtube.com/embed/W2CicgFq5oo", "muted": False}, "ui": {"layout_profile": "minimal"}}
    migrated, changed = migrate_config(old)
    assert changed
    config = validate_config(migrated)
    assert config["stream"]["audio"] == "on" and config["stream"]["muted"] is False
    assert config["ui"]["distance"] == 1 and config["ui"]["setup_done"] is True
    fresh = validate_config(migrate_config({"app": {"config_schema_version": 4}})[0])
    assert fresh["ui"]["setup_done"] is False and fresh["ui"]["distance"] == 2


def test_v5_validation_cleans_and_rejects(config: dict) -> None:
    candidate = copy.deepcopy(config)
    candidate["stream"]["favorites"] = [{"url": "https://youtu.be/W2CicgFq5oo", "title": "Nest"}, {"url": "https://www.youtube.com/embed/W2CicgFq5oo", "title": "doppelt"}]
    candidate["music"]["radio"]["favorites"] = [STATION, STATION]
    candidate["transit"]["stops"] = [{"station_name": "Marktplatz", "station_id": "1", "walk_minutes": 7}]
    valid = validate_config(candidate)
    assert valid["stream"]["favorites"] == [{"url": "https://www.youtube.com/embed/W2CicgFq5oo", "title": "Nest", "channel": ""}]
    assert "notes" not in valid["stream"]
    assert len(valid["music"]["radio"]["favorites"]) == 1 and valid["transit"]["stops"][0]["walk_minutes"] == 7
    for path, value, message in [
        (("ui", "night", "start"), "25:00", "Uhrzeit"),
        (("ui", "distance"), 4, "ui.distance"),
        (("stream", "audio"), "loud", "stream.audio"),
        (("weather", "warnings"), "all", "weather.warnings"),
        (("server", "lan_access"), True, "PIN"),
        (("server", "public_url"), "raspberrypi.local", "public_url"),
        (("music", "equalizer"), {"preset": "flat", "gains": [0, 0, 0]}, "Bänder"),
        (("music", "equalizer"), {"preset": "laut", "gains": [0] * 6}, "Voreinstellung"),
    ]:
        broken = copy.deepcopy(DEFAULT_CONFIG)
        target: Any = broken
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ConfigError, match=message):
            validate_config(broken)


# ---------- Endpunkte ----------

def test_remote_devices_need_a_pairing_code(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    monkeypatch.setattr(StorchHandler, "_loopback_client", lambda self: False)
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, admin = request(address, "GET", "/api/admin/status")
        assert admin["authenticated"] is False and admin["remote"] is True and admin["pin_login"] is False
        assert request(address, "GET", "/api/config")[0] == 401
        assert post_json(address, "/api/admin/login", {"pin": "1234"})[0] == 403
        assert post_json(address, "/api/display/power", {"on": False})[0] == 403
        assert post_json(address, "/api/pair/claim", {"code": "000000"})[0] == 403
        code = pairing.start(config)["code"]
        status, _headers, session = post_json(address, "/api/pair/claim", {"code": code})
        assert status == 200 and session["token"]
        status, _headers, current = request(address, "GET", "/api/config", headers={"Authorization": f"Bearer {session['token']}"})
        assert status == 200 and current["config"]["location"]


def test_radio_endpoints_play_switch_and_remember(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    second = {**STATION, "uuid": "22222222-bbbb", "name": "DLF", "url": "https://dlf.example.org/live.mp3"}
    config["music"]["radio"]["favorites"] = [STATION, second]
    played: list[str] = []
    current: dict[str, Any] = {}

    def fake_play(station: dict[str, Any]) -> None:
        played.append(station["name"])
        current.update(station)

    monkeypatch.setattr(player, "play_radio", fake_play)
    monkeypatch.setattr(player, "current_station", lambda: dict(current) if current else None)
    monkeypatch.setattr(player, "full_output_volume", lambda *_a, **_k: None)
    monkeypatch.setattr(radio, "count_click", lambda _uuid: None)
    monkeypatch.setattr(radio, "search", lambda query, filter_key="": [STATION] if query == "WDR" else [])
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, found = post_json(address, "/api/radio/search", {"query": "WDR"})
        assert status == 200 and found["results"][0]["name"] == "WDR 2"
        assert post_json(address, "/api/radio/play", {"station": {"uuid": "x", "url": "javascript:alert(1)"}})[0] == 400
        status, _headers, _body = post_json(address, "/api/radio/play", {"station": STATION})
        assert status == 200 and played == ["WDR 2"]
        assert post_json(address, "/api/player/control", {"target": "pi", "action": "next"})[0] == 200
        assert played[-1] == "DLF"
        assert post_json(address, "/api/player/control", {"target": "pi", "action": "next"})[0] == 200
        assert played[-1] == "WDR 2"
        status, _headers, recent = request(address, "GET", "/api/music/recent")
        assert [item["title"] for item in recent["results"]][:2] == ["WDR 2", "DLF"]
        status, _headers, sleep = post_json(address, "/api/player/sleep", {"minutes": 30, "label": "30"})
        assert status == 200 and sleep["sleep_end"] > 0
        cleared = post_json(address, "/api/player/sleep", {"minutes": 0})
        assert cleared[2]["sleep_end"] == 0


def test_favourite_cameras_resolve_without_menu_login(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["admin"].update({"require_pin": True, "pin_hash": "pbkdf2_sha256$1$aa$bb"})
    config["stream"]["url"] = "https://example.org/webcam"
    config["stream"]["favorites"] = [{"url": "https://cam.example.org/live.m3u8", "title": "Ersatz", "channel": ""}]
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, source = request(address, "GET", "/api/stream/source?url=https%3A%2F%2Fcam.example.org%2Flive.m3u8")
        assert status == 200 and source["kind"] == "hls"
        assert request(address, "GET", "/api/stream/source?url=https%3A%2F%2Fother.example.org%2Fx.m3u8")[0] == 401
