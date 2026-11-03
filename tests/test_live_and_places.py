from __future__ import annotations

import io
import json
import urllib.error

import pytest

from storchcam.config_store import ConfigError, public_config, validate_config, youtube_api_key, youtube_embed_url
from storchcam.providers import geocoding, youtube


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=hTz03y1ioDo", "https://www.youtube.com/embed/hTz03y1ioDo"),
        ("https://youtu.be/hTz03y1ioDo?t=3", "https://www.youtube.com/embed/hTz03y1ioDo"),
        ("https://m.youtube.com/live/hTz03y1ioDo", "https://www.youtube.com/embed/hTz03y1ioDo"),
        ("https://www.youtube.com/embed/hTz03y1ioDo", "https://www.youtube.com/embed/hTz03y1ioDo"),
        ("https://example.org/watch?v=hTz03y1ioDo", "https://example.org/watch?v=hTz03y1ioDo"),
    ],
)
def test_youtube_links_become_embeddable(url: str, expected: str) -> None:
    assert youtube_embed_url(url) == expected


def test_config_normalizes_stream_and_validates_youtube(config: dict) -> None:
    config["stream"]["url"] = "https://www.youtube.com/watch?v=hTz03y1ioDo"
    config["youtube"]["api_key"] = "AIza_test-KEY123"
    validated = validate_config(config)
    assert validated["stream"]["url"] == "https://www.youtube.com/embed/hTz03y1ioDo"
    assert public_config(validated)["youtube"]["api_key"] == ""
    assert youtube_api_key(validated) == "AIza_test-KEY123"
    config["youtube"]["api_key"] = "bad key!"
    with pytest.raises(ConfigError):
        validate_config(config)


def test_open_mode_allows_external_bind_without_pin(config: dict) -> None:
    config["server"]["host"] = "0.0.0.0"
    with pytest.raises(ConfigError):
        validate_config(config)
    config["admin"]["require_pin"] = False
    assert validate_config(config)["server"]["host"] == "0.0.0.0"


def test_env_key_is_fallback(monkeypatch: pytest.MonkeyPatch, config: dict) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "envkey")
    assert youtube_api_key(config) == "envkey"
    config["youtube"]["api_key"] = "menukey"
    assert youtube_api_key(config) == "menukey"


def test_live_search_maps_results_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    youtube.clear_cache()
    calls: list[str] = []

    def fake(url: str, timeout: int = 12) -> dict:
        calls.append(url)
        return {"items": [
            {"id": {"videoId": "hTz03y1ioDo"}, "snippet": {"title": "Storch &amp; Nest", "channelTitle": "Raddusch", "thumbnails": {"medium": {"url": "https://i.ytimg.com/vi/hTz03y1ioDo/mqdefault_live.jpg"}}}},
            {"id": {}, "snippet": {"title": "kein Video"}},
        ]}

    monkeypatch.setattr(youtube, "_fetch_json", fake)
    results = youtube.search_live("Storch", "key")
    assert results == [{
        "video_id": "hTz03y1ioDo",
        "title": "Storch & Nest",
        "channel": "Raddusch",
        "thumbnail": "https://i.ytimg.com/vi/hTz03y1ioDo/mqdefault_live.jpg",
        "embed_url": "https://www.youtube.com/embed/hTz03y1ioDo",
    }]
    assert "eventType=live" in calls[0] and "videoEmbeddable=true" in calls[0] and "q=Storch" in calls[0]
    youtube.search_live("storch", "key")
    assert len(calls) == 1


def _page(renderers: list[dict]) -> str:
    data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [{"videoRenderer": r} for r in renderers]}}]}}}
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


LIVE = {
    "videoId": "rurvbn9yH-k",
    "title": {"runs": [{"text": "Live aus dem Storchennest"}]},
    "ownerText": {"runs": [{"text": "Heimatverein"}]},
    "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/rurvbn9yH-k/hq720_live.jpg?sqp=abc"}]},
    "badges": [{"metadataBadgeRenderer": {"style": "BADGE_STYLE_TYPE_LIVE_NOW"}}],
}
RECORDED = {"videoId": "AAAAAAAAAAA", "title": {"runs": [{"text": "Aufzeichnung"}]}, "badges": []}


def test_keyless_search_reads_live_filter_page(monkeypatch: pytest.MonkeyPatch) -> None:
    youtube.clear_cache()
    urls: list[str] = []

    def fake(url: str, timeout: int = 12) -> str:
        urls.append(url)
        return _page([LIVE, RECORDED, LIVE])

    monkeypatch.setattr(youtube, "_fetch_page", fake)
    assert youtube.search_live("Storch") == [{
        "video_id": "rurvbn9yH-k",
        "title": "Live aus dem Storchennest",
        "channel": "Heimatverein",
        "thumbnail": "https://i.ytimg.com/vi/rurvbn9yH-k/hq720_live.jpg",
        "embed_url": "https://www.youtube.com/embed/rurvbn9yH-k",
    }]
    assert "search_query=Storch" in urls[0] and "sp=EgJAAQ%3D%3D" in urls[0]


def test_api_errors_fall_back_to_public_search(monkeypatch: pytest.MonkeyPatch) -> None:
    youtube.clear_cache()
    body = io.BytesIO(b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')

    def fail(url: str, timeout: int = 12) -> dict:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(youtube, "_fetch_json", fail)
    monkeypatch.setattr(youtube, "_fetch_page", lambda url, timeout=12: _page([LIVE]))
    assert [item["video_id"] for item in youtube.search_live("Storch", "key")] == ["rurvbn9yH-k"]


def test_broken_page_gives_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    youtube.clear_cache()
    monkeypatch.setattr(youtube, "_fetch_page", lambda url, timeout=12: "<html>Einwilligung</html>")
    with pytest.raises(youtube.YouTubeError, match="nicht erreichbar"):
        youtube.search_live("Storch")


def test_place_search_finds_streets_via_nominatim(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake(url: str, timeout: int = 10):
        urls.append(url)
        return [
            {"lat": "52.5123456", "lon": "13.4123456", "name": "Musterstraße", "address": {"road": "Musterstraße", "house_number": "1", "postcode": "10115", "city": "Berlin", "state": "Berlin", "country": "Deutschland"}},
            {"lat": "35.6895", "lon": "139.6917", "name": "Tokio", "address": {"city": "Tokio", "country": "Japan"}},
            {"name": "ohne Koordinaten"},
        ]

    monkeypatch.setattr(geocoding, "_fetch_json", fake)
    assert geocoding.search_places("Musterstraße 1 Berlin") == [
        {"name": "Musterstraße 1, 10115 Berlin", "region": "Berlin, Deutschland", "latitude": 52.51235, "longitude": 13.41235, "timezone": ""},
        {"name": "Tokio", "region": "Japan", "latitude": 35.6895, "longitude": 139.6917, "timezone": ""},
    ]
    assert urls[0].startswith(geocoding.NOMINATIM_URL)
    assert geocoding.search_places("T") == []


def test_place_search_falls_back_to_open_meteo(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(url: str, timeout: int = 10):
        if url.startswith(geocoding.NOMINATIM_URL):
            raise OSError("Nominatim offline")
        return {"results": [{"name": "Tokio", "admin1": "Tokio", "country": "Japan", "latitude": 35.6895, "longitude": 139.69171, "timezone": "Asia/Tokyo"}]}

    monkeypatch.setattr(geocoding, "_fetch_json", fake)
    assert geocoding.search_places("Tokio") == [
        {"name": "Tokio", "region": "Japan", "latitude": 35.6895, "longitude": 139.6917, "timezone": "Asia/Tokyo"},
    ]


def test_timezone_for_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geocoding, "_fetch_json", lambda url, timeout=10: {"timezone": "Europe/Berlin"} if "timezone=auto" in url else {})
    assert geocoding.timezone_for(52.52, 13.405) == "Europe/Berlin"
