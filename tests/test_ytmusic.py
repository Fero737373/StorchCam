from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from storchcam.config_store import migrate_config, validate_config
from storchcam.providers import music, ytmusic
from tests.test_server import post_json, request, running_server


def song(video: str, token: str, title: str, seconds: int = 200) -> dict[str, Any]:
    return {
        "videoId": video,
        "feedbackToken": token,
        "title": title,
        "artists": [{"name": "Künstler"}],
        "album": {"name": "Album"},
        "thumbnails": [{"url": "https://lh3.googleusercontent.com/abc=w60-h60"}, {"url": "https://lh3.googleusercontent.com/abc=w120-h120"}],
        "duration_seconds": seconds,
        "played": "Today",
    }


COOKIES = [
    {"name": "SID", "value": "allgemein", "domain": ".youtube.com"},
    {"name": "__Secure-3PAPISID", "value": "abc/def", "domain": ".youtube.com"},
    {"name": "PREF", "value": "alt", "domain": ".youtube.com"},
    {"name": "PREF", "value": "music", "domain": "music.youtube.com"},
]


class FakeWindow:
    """Steht für das sichtbare Anmeldefenster."""

    def __init__(self) -> None:
        self.open = True

    def alive(self) -> bool:
        return self.open

    def close(self) -> None:
        self.open = False


class FakeHeadless:
    """Steht für das unsichtbare Chromium, das nach dem Schließen die Cookies ausliest."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies = cookies
        self.closed = False

    def call(self, method: str, timeout: float = 10) -> dict[str, Any]:
        assert method == "Browser.getVersion"
        return {"userAgent": "Mozilla/5.0 HeadlessChrome/156"}

    def youtube_cookies(self) -> list[dict[str, Any]]:
        return self.cookies

    def close(self) -> None:
        self.closed = True


def fake_login(monkeypatch: pytest.MonkeyPatch, window: FakeWindow, session_after: int) -> FakeHeadless:
    polls = {"count": 0}

    def has_session() -> bool:
        polls["count"] += 1
        return polls["count"] > session_after

    headless = FakeHeadless(COOKIES)
    monkeypatch.setattr(ytmusic, "_browser_arguments", lambda _config, **_kwargs: ["chromium"])
    monkeypatch.setattr(ytmusic, "LoginWindow", lambda _arguments, _env, _log: window)
    monkeypatch.setattr(ytmusic, "DevToolsBrowser", lambda _arguments, _env, _log: headless)
    monkeypatch.setattr(ytmusic, "profile_has_session", has_session)
    return headless


def wait_until_done() -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while ytmusic.login_status()["status"] == "pending":
        assert time.monotonic() < deadline, "Anmeldung wurde nicht beendet"
    return ytmusic.login_status()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORCHCAM_CONFIG", str(tmp_path / "config.json"))
    ytmusic.disconnect()
    monkeypatch.setattr(ytmusic.time, "sleep", lambda _seconds: None)


def fake_history(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(ytmusic, "_history", lambda: items)


def test_new_history_entry_counts_as_playing_for_its_length(monkeypatch: pytest.MonkeyPatch) -> None:
    history = [song("a", "t1", "Alt")]
    fake_history(monkeypatch, history)
    # Beim ersten Blick ist unklar, wann der Song lief: nichts anzeigen.
    assert ytmusic.now_playing(now=1000) is None
    history.insert(0, song("b", "t2", "Neu", seconds=180))
    track = ytmusic.now_playing(now=1010)
    assert track and track["title"] == "Neu" and track["artist"] == "Künstler"
    assert track["cover"] == "https://lh3.googleusercontent.com/abc=w300-h300"
    # Google erneuert die feedbackTokens bei jeder Abfrage; das ist kein neuer Song.
    history[0] = song("b", "t9", "Neu", seconds=180)
    assert ytmusic.now_playing(now=1010 + 180 + ytmusic.GRACE_SECONDS - 1)
    assert ytmusic.now_playing(now=1010 + 180 + ytmusic.GRACE_SECONDS + 1) is None


def test_stale_history_does_not_jump_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # Abfolge wie im echten Verlauf beobachtet: neu, kurz alter Stand, dann ein älterer Song erneut gespielt.
    responses = [
        [song("r", "1", "Revenge"), song("b", "1", "Buck"), song("i", "1", "Hard")],
        [song("d", "2", "Believin"), song("r", "2", "Revenge"), song("b", "2", "Buck")],
        [song("r", "3", "Revenge"), song("b", "3", "Buck"), song("i", "3", "Hard")],
        [song("i", "4", "Hard"), song("d", "4", "Believin"), song("r", "4", "Revenge")],
    ]
    current: list[list[dict[str, Any]]] = [responses[0]]
    monkeypatch.setattr(ytmusic, "_history", lambda: current[0])
    titles = []
    for when, response in enumerate(responses):
        current[0] = response
        track = ytmusic.now_playing(now=1000 + when * 10)
        titles.append(track["title"] if track else None)
    assert titles == [None, "Believin", "Believin", "Hard"]


def test_unknown_history_resets_after_a_few_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    current = [[song("a", "1", "A"), song("b", "1", "B")]]
    monkeypatch.setattr(ytmusic, "_history", lambda: current[0])
    ytmusic.now_playing(now=1000)
    current[0] = [song("x", "2", "X"), song("y", "2", "Y")]
    for when in range(ytmusic.STALE_LIMIT + 1):
        assert ytmusic.now_playing(now=1010 + when) is None
    current[0] = [song("z", "3", "Z"), song("x", "3", "X")]
    assert ytmusic.now_playing(now=1100)["title"] == "Z"  # type: ignore[index]


def test_auth_headers_prefer_specific_cookie_domain() -> None:
    headers = ytmusic.auth_headers(COOKIES, "UA")
    assert headers["cookie"] == "SID=allgemein; __Secure-3PAPISID=abc/def; PREF=music"
    assert headers["authorization"].startswith("SAPISIDHASH") and headers["x-goog-authuser"] == "0"
    assert headers["user-agent"] == "UA" and headers["origin"] == "https://music.youtube.com"


def test_visible_window_has_no_remote_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ytmusic, "find_browser", lambda _name: ("chromium", "/usr/bin/chromium"))
    monkeypatch.setattr(ytmusic, "session_type", lambda: "wayland")
    visible = ytmusic._browser_arguments({}, headless=False)
    # Google verweigert die Anmeldung in ferngesteuerten Browsern.
    assert not any("remote-debugging" in argument or "automation" in argument for argument in visible)
    assert visible[-1] == ytmusic.LOGIN_URL and "--enable-wayland-ime" in visible
    assert "--headless=new" in ytmusic._browser_arguments({}, headless=True)


def test_profile_session_detection_reads_cookie_names() -> None:
    assert not ytmusic.profile_has_session()
    database = ytmusic.profile_dir() / "Default" / "Cookies"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
        connection.execute("INSERT INTO cookies VALUES ('.google.com', '__Secure-3PAPISID', x'00')")
    assert not ytmusic.profile_has_session()
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO cookies VALUES ('.youtube.com', '__Secure-3PAPISID', x'00')")
    assert ytmusic.profile_has_session()


def test_login_closes_window_then_reads_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()
    headless = fake_login(monkeypatch, window, session_after=2)
    monkeypatch.setattr(ytmusic, "describe_latest", lambda: "Lied · Künstler")
    assert ytmusic.start_login({})["status"] == "pending"
    assert wait_until_done() == {"status": "connected", "latest": "Lied · Künstler", "connected": True}
    assert not window.open and headless.closed
    stored = json.loads(ytmusic.auth_path().read_text())
    assert "__Secure-3PAPISID=abc/def" in stored["cookie"] and stored["user-agent"] == "Mozilla/5.0 Chrome/156"
    assert ytmusic.auth_path().stat().st_mode & 0o777 == 0o600


def test_window_closed_by_user_after_login_still_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()
    fake_login(monkeypatch, window, session_after=0)
    monkeypatch.setattr(ytmusic, "describe_latest", lambda: "Lied · Künstler")
    ytmusic.start_login({})
    window.open = False
    assert wait_until_done()["status"] == "connected"


def test_closed_window_without_login_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()
    fake_login(monkeypatch, window, session_after=10**6)
    ytmusic.start_login({})
    window.open = False
    status = wait_until_done()
    assert status["status"] == "error" and "geschlossen" in status["error"] and not status["connected"]


def test_music_source_uses_ytmusic_when_connected(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    monkeypatch.setattr(ytmusic, "connected", lambda: True)
    monkeypatch.setattr(ytmusic, "now_playing", lambda: {"source": "ytmusic", "title": "Lied", "artist": "X"})
    result = music.get_now_playing({**config, "music": {**config["music"], "source": "ytmusic"}})
    assert result["playing"] and result["source"] == "ytmusic"

    def broken() -> None:
        raise ytmusic.YTMusicError("YouTube Music: HTTP 400")

    monkeypatch.setattr(ytmusic, "now_playing", broken)
    result = music.get_now_playing({**config, "music": {**config["music"], "source": "ytmusic"}})
    assert result["ok"] is False and "HTTP 400" in result["error"]


def test_old_google_client_settings_are_dropped(config: dict) -> None:
    old = {**config, "music": {**config["music"], "ytmusic": {"client_id": "x.apps.googleusercontent.com", "client_secret": "GOCSPX-x"}}}
    migrated, _changed = migrate_config(old)
    assert "ytmusic" not in validate_config(migrated)["music"]


def test_login_endpoints(monkeypatch: pytest.MonkeyPatch, config: dict, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    calls: list[str] = []
    monkeypatch.setattr(ytmusic, "start_login", lambda _config: calls.append("start") or {"status": "pending", "connected": False})
    monkeypatch.setattr(ytmusic, "cancel_login", lambda: calls.append("cancel"))
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, result = post_json(address, "/api/music/ytmusic/login", {})
        assert status == 200 and result["status"] == "pending"
        assert post_json(address, "/api/music/ytmusic/cancel", {})[0] == 200
        status, _headers, loaded = request(address, "GET", "/api/config")
        assert loaded["ytmusic_connected"] is False and "ytmusic" not in loaded["config"]["music"]
    assert calls == ["start", "cancel"]


def test_lastfm_settings_are_migrated(config: dict) -> None:
    old = {**config, "music": {**config["music"], "source": "lastfm", "lastfm": {"user": "x", "api_key": "y"}}}
    migrated, changed = migrate_config(old)
    assert changed and validate_config(migrated)["music"]["source"] == "auto" and "lastfm" not in migrated["music"]
