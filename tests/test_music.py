from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from storchcam.providers import music
from tests.test_server import post_json, request, running_server

SPOTIFY_PLAYING = {
    "is_playing": True,
    "progress_ms": 61000,
    "item": {
        "type": "track",
        "name": "Bohemian Rhapsody",
        "duration_ms": 354000,
        "artists": [{"name": "Queen"}],
        "album": {"name": "A Night at the Opera", "images": [{"url": "https://i.scdn.co/640"}, {"url": "https://i.scdn.co/300"}]},
    },
}
@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORCHCAM_CONFIG", str(tmp_path / "config.json"))
    music.spotify_disconnect()


def music_config(config: dict, source: str) -> dict:
    return {**config, "music": {"source": source, "spotify": {"client_id": "abc"}}}


def fake_requests(monkeypatch: pytest.MonkeyPatch, responses: dict[str, tuple[int, Any]]) -> list[str]:
    calls: list[str] = []

    def fake(url: str, *, form: dict[str, str] | None = None, token: str = "") -> tuple[int, Any]:
        calls.append(url)
        for prefix, response in responses.items():
            if url.startswith(prefix):
                return response
        raise AssertionError(f"unerwartete Anfrage {url}")

    monkeypatch.setattr(music, "_request", fake)
    return calls


def test_spotify_now_playing(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    music._write_token({"client_id": "abc", "refresh_token": "r1"})
    playing: dict[str, tuple[int, Any]] = {
        music.SPOTIFY_TOKEN_URL: (200, {"access_token": "a1", "expires_in": 3600}),
        music.SPOTIFY_PLAYING_URL: (200, SPOTIFY_PLAYING),
    }
    fake_requests(monkeypatch, playing)
    result = music.get_now_playing(music_config(config, "auto"))
    assert result["playing"] and result["source"] == "spotify" and result["cover"] == "https://i.scdn.co/300"
    assert result["progress_ms"] == 61000 and result["duration_ms"] == 354000
    fake_requests(monkeypatch, {**playing, music.SPOTIFY_PLAYING_URL: (204, None)})
    result = music.get_now_playing(music_config(config, "auto"))
    assert result == {"enabled": True, "ok": True, "playing": False}
    assert music.get_now_playing(music_config(config, "off")) == {"enabled": False}


def test_expired_spotify_grant_reports_error(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    music._write_token({"client_id": "abc", "refresh_token": "r1"})
    fake_requests(monkeypatch, {music.SPOTIFY_TOKEN_URL: (400, {"error": "invalid_grant"})})
    result = music.get_now_playing(music_config(config, "spotify"))
    assert result["ok"] is False and "neu verbinden" in result["error"]


def test_spotify_login_flow(monkeypatch: pytest.MonkeyPatch, config: dict, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    with running_server(config, monkeypatch, tmp_path) as address:
        redirect = f"http://127.0.0.1:{address[1]}{music.SPOTIFY_CALLBACK_PATH}"
        status, _headers, body = post_json(address, "/api/music/spotify/authorize", {"client_id": "abc123", "redirect_uri": redirect})
        assert status == 200
        query = parse_qs(urlparse(body["url"]).query)
        assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == [redirect] and query["client_id"] == ["abc123"]
        assert json.loads((tmp_path / "config.json").read_text())["music"]["spotify"]["client_id"] == "abc123"

        status, headers, _ = request(address, "GET", f"{music.SPOTIFY_CALLBACK_PATH}?state=falsch&code=x")
        assert status == 302 and headers["Location"].startswith("/?music=error")

        fake_requests(monkeypatch, {music.SPOTIFY_TOKEN_URL: (200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})})
        status, headers, _ = request(address, "GET", f"{music.SPOTIFY_CALLBACK_PATH}?state={query['state'][0]}&code=ok")
        assert status == 302 and headers["Location"] == "/?music=connected"
        assert music.spotify_connected()
        status, _headers, loaded = request(address, "GET", "/api/config")
        assert loaded["spotify_connected"] is True

        assert post_json(address, "/api/music/spotify/disconnect", {})[0] == 200
        assert not music.spotify_connected()
