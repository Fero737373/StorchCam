"""Search for videos that are live on YouTube right now.

Works without any key by reading the public search page with the "Live" filter.
An optional YouTube Data API v3 key switches to the official API; if that fails
(e.g. quota exhausted) the public search page is used as a fallback.
"""

from __future__ import annotations

import copy
import html
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .http_json import USER_AGENT, fetch_json_object

LOGGER = logging.getLogger(__name__)
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
RESULTS_URL = "https://www.youtube.com/results"
LIVE_FILTER = "EgJAAQ=="  # Suchfilter „Live“ der YouTube-Oberfläche
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
    # Überspringt die EU-Einwilligungsseite, die sonst statt der Suchergebnisse käme.
    "Cookie": "SOCS=CAI; CONSENT=YES+cb",
}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CACHE_SECONDS = 300
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_cache_lock = threading.Lock()


class YouTubeError(RuntimeError):
    """A user-facing error from the YouTube search."""


def _fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    return fetch_json_object(url, "YouTube", headers=headers, timeout=timeout)


def _api_error(exc: urllib.error.HTTPError) -> YouTubeError:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        reason = payload["error"]["errors"][0]["reason"]
    except Exception:
        reason = ""
    messages = {
        "quotaExceeded": "Das tägliche YouTube-Kontingent ist aufgebraucht. Morgen geht es wieder.",
        "dailyLimitExceeded": "Das tägliche YouTube-Kontingent ist aufgebraucht. Morgen geht es wieder.",
        "keyInvalid": "Der YouTube-API-Schlüssel ist ungültig.",
        "accessNotConfigured": "Für diesen Schlüssel ist die YouTube Data API v3 nicht aktiviert.",
        "forbidden": "YouTube verweigert den Zugriff mit diesem Schlüssel.",
    }
    return YouTubeError(messages.get(reason, f"YouTube-Suche fehlgeschlagen (HTTP {exc.code})"))


def _entry(video_id: str, title: str, channel: str, thumbnail: str = "") -> dict[str, str]:
    if not thumbnail.startswith("https://i.ytimg.com/"):
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "thumbnail": thumbnail,
        "embed_url": f"https://www.youtube.com/embed/{video_id}",
    }


def _fetch_page(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return str(response.read().decode("utf-8", "replace"))


def _text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if "simpleText" in value:
        return str(value["simpleText"])
    return "".join(str(run.get("text", "")) for run in value.get("runs", []) if isinstance(run, dict))


def _video_renderers(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            renderer = current.get("videoRenderer")
            if isinstance(renderer, dict):
                found.append(renderer)
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return found


def _is_live(renderer: dict[str, Any]) -> bool:
    for badge in renderer.get("badges", []) or []:
        if isinstance(badge, dict) and "LIVE" in str(badge.get("metadataBadgeRenderer", {}).get("style", "")):
            return True
    for overlay in renderer.get("thumbnailOverlays", []) or []:
        if isinstance(overlay, dict) and overlay.get("thumbnailOverlayTimeStatusRenderer", {}).get("style") == "LIVE":
            return True
    return False


def parse_results_page(page: str, max_results: int = 24) -> list[dict[str, str]]:
    match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", page, re.S)
    if not match:
        raise ValueError("YouTube-Suchseite enthält keine Ergebnisdaten")
    data = json.loads(match.group(1))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for renderer in _video_renderers(data):
        video_id = str(renderer.get("videoId", ""))
        if not VIDEO_ID.fullmatch(video_id) or video_id in seen or not _is_live(renderer):
            continue
        seen.add(video_id)
        thumbnails = renderer.get("thumbnail", {}).get("thumbnails", []) if isinstance(renderer.get("thumbnail"), dict) else []
        thumbnail = str(thumbnails[-1].get("url", "")).split("?")[0] if thumbnails and isinstance(thumbnails[-1], dict) else ""
        channel = _text(renderer.get("ownerText")) or _text(renderer.get("longBylineText"))
        results.append(_entry(video_id, _text(renderer.get("title")), channel, thumbnail))
        if len(results) >= max_results:
            break
    return results


def _search_page(query: str, max_results: int) -> list[dict[str, str]]:
    params = {"search_query": query or "live", "sp": LIVE_FILTER}
    try:
        page = _fetch_page(RESULTS_URL + "?" + urllib.parse.urlencode(params))
        return parse_results_page(page, max_results)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        LOGGER.warning("YouTube-Suchseite fehlgeschlagen: %s", exc)
        raise YouTubeError("YouTube ist gerade nicht erreichbar.") from exc


def search_live(query: str, api_key: str = "", *, max_results: int = 24) -> list[dict[str, str]]:
    clean = " ".join(query.split())[:100]
    key = f"{clean.casefold()}|{max_results}|{bool(api_key)}"
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return copy.deepcopy(cached[1])
    if api_key:
        try:
            results = _search_api(clean, api_key, max_results)
        except YouTubeError as exc:
            LOGGER.warning("YouTube-API fehlgeschlagen (%s), nutze öffentliche Suche", exc)
            results = _search_page(clean, max_results)
    else:
        results = _search_page(clean, max_results)
    with _cache_lock:
        _cache[key] = (now, copy.deepcopy(results))
    return results


def _search_api(clean: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    params = {
        "part": "snippet",
        "type": "video",
        "eventType": "live",
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
        "relevanceLanguage": "de",
        "maxResults": str(max_results),
        "key": api_key,
    }
    if clean:
        params["q"] = clean
    else:
        params["order"] = "viewCount"
    try:
        data = _fetch_json(SEARCH_URL + "?" + urllib.parse.urlencode(params))
    except urllib.error.HTTPError as exc:
        raise _api_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        LOGGER.warning("YouTube-Suche fehlgeschlagen: %s", exc)
        raise YouTubeError("YouTube ist gerade nicht erreichbar.") from exc

    results: list[dict[str, str]] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("id", {}).get("videoId", ""))
        snippet = item.get("snippet", {}) if isinstance(item.get("snippet"), dict) else {}
        if not video_id:
            continue
        thumbnails = snippet.get("thumbnails", {}) if isinstance(snippet.get("thumbnails"), dict) else {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get("high") or thumbnails.get("default") or {}).get("url", "")
        results.append(_entry(
            video_id,
            html.unescape(str(snippet.get("title", ""))),
            html.unescape(str(snippet.get("channelTitle", ""))),
            str(thumbnail),
        ))
    return results


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
