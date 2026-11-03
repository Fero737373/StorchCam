"""Resolve the configured live stream for the kiosk's own video player.

YouTube live streams become an HLS playlist through yt-dlp, ordinary YouTube videos a single video file
that loops, direct ``.m3u8`` addresses are used as they are, and everything else stays an embedded web
page as before. Resolved YouTube addresses expire after a few hours; the cache drops them ten minutes
early so the player never asks for a dead address.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger(__name__)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
EXPIRE_IN_PATH = re.compile(r"/expire/(\d+)")
DEFAULT_LIFETIME = 3 * 3600
REFRESH_MARGIN = 600
QUALITIES = {"auto": 1080, "1080": 1080, "720": 720, "480": 480}
YOUTUBE_HOSTS = {"youtube.com", "youtube-nocookie.com", "music.youtube.com"}
# H.264 zuerst (überall flott), VP9 als Ausweg; AV1 ist für den Pi in Software zu schwer.
FILE_CODECS = {"avc1": 2, "vp09": 1, "vp9": 1}
FPS_LIMITS = {30, 60}

_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()
# yt-dlp braucht auf dem Pi einige Sekunden; gleichzeitige Anfragen warten auf dasselbe Ergebnis.
_resolve_lock = threading.Lock()


class StreamError(RuntimeError):
    """User-facing reason why a stream cannot play in the own player."""


def youtube_id(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host == "youtu.be" and parts:
        candidate = parts[0]
    elif host in YOUTUBE_HOSTS and parts:
        if parts[0] == "watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parts[0] in {"embed", "live", "shorts"} and len(parts) > 1:
            candidate = parts[1]
    return candidate if VIDEO_ID.fullmatch(candidate) else ""


def is_hls(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and parsed.path.lower().endswith(".m3u8")


def max_height(quality: Any) -> int:
    return QUALITIES.get(str(quality), QUALITIES["auto"])


def expires_at(url: str, now: float) -> float:
    """googlevideo-Adressen tragen ihr Ablaufdatum im Pfad (``/expire/<unix>/``) oder als Parameter."""
    parsed = urlparse(url)
    match = EXPIRE_IN_PATH.search(parsed.path)
    raw = match.group(1) if match else parse_qs(parsed.query).get("expire", [""])[0]
    try:
        return float(raw)
    except ValueError:
        return now + DEFAULT_LIFETIME


def _usable(fmt: Any) -> bool:
    return isinstance(fmt, dict) and bool(fmt.get("url")) and "MISSING POT" not in str(fmt.get("format_note") or "")


def _has_video(fmt: dict[str, Any]) -> bool:
    return str(fmt.get("vcodec") or "none") != "none"


def _hls_address(formats: list[dict[str, Any]]) -> str:
    """Master-Playlist der besten HLS-Variante; hls.js wählt daraus passend zur Leitung."""
    hls = [fmt for fmt in formats if str(fmt.get("protocol", "")).startswith("m3u8")]
    hls = [fmt for fmt in hls if _has_video(fmt)] or hls
    if not hls:
        return ""
    best = hls[-1]  # yt-dlp sortiert aufsteigend nach Qualität
    return str(best.get("manifest_url") or best.get("url") or "")


def _file_format(formats: list[dict[str, Any]], height_limit: int, fps_limit: int = 30) -> dict[str, Any] | None:
    """Für Videos ohne HLS: die beste einzelne Videodatei, die der Pi flüssig abspielt (stumm, Ton ist egal)."""
    candidates = []
    for fmt in formats:
        codec = str(fmt.get("vcodec") or "")
        rank = next((score for prefix, score in FILE_CODECS.items() if codec.startswith(prefix)), 0)
        height = int(fmt.get("height") or 0)
        fps = float(fmt.get("fps") or 0)
        if (
            rank
            and fmt.get("protocol") in {"https", "http"}
            and fmt.get("ext") in {"mp4", "webm"}
            and 0 < height <= height_limit
            and fps <= fps_limit + 0.5
        ):
            candidates.append((rank, height, fps, float(fmt.get("tbr") or 0), fmt))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[:4])[4]


def _describe(live_status: str) -> str:
    return {
        "is_upcoming": "Der Livestream hat noch nicht begonnen",
        "is_live": "YouTube liefert für diesen Livestream gerade keine HLS-Adresse",
        "post_live": "Der Livestream ist gerade zu Ende gegangen",
    }.get(live_status, "YouTube liefert für dieses Video keine abspielbare Datei")


def _youtube_source(video_id: str, height_limit: int, fps_limit: int) -> dict[str, Any]:
    import yt_dlp  # type: ignore[import-untyped]  # erst hier laden: groß und nur für YouTube nötig

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "best[protocol^=m3u8]/best",
        # Ohne passendes Kombi-Format (Video ohne Ton reicht dem stummen Kiosk) nicht abbrechen,
        # sondern die Formatliste selbst durchsehen.
        "ignore_no_formats_error": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as exc:  # yt-dlp wirft je nach Fehler sehr unterschiedliche Typen
        raise StreamError(f"YouTube liefert keinen Stream: {str(exc)[:160]}") from exc
    if not isinstance(info, dict):
        raise StreamError("YouTube liefert keine Streamdaten")
    formats = [fmt for fmt in info.get("formats") or [] if _usable(fmt)]
    live_status = str(info.get("live_status") or ("is_live" if info.get("is_live") else "not_live"))
    details = {
        "title": str(info.get("title") or ""),
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "live": live_status == "is_live",
    }
    playlist = _hls_address(formats)
    if playlist:
        return {"kind": "hls", "upstream": playlist, **details}
    if live_status not in {"is_live", "is_upcoming"}:
        chosen = _file_format(formats, height_limit, fps_limit)
        if chosen:
            return {"kind": "file", "upstream": str(chosen["url"]), **details}
    raise StreamError(f"{_describe(live_status)} ({len(formats)} Formate gefunden)")


def resolve(url: str, *, refresh: bool = False, now: float | None = None, height_limit: int = 1080, fps_limit: int = 30) -> dict[str, Any]:
    """``{"kind": "hls"|"file", "upstream", "expires", ...}``, ``{"kind": "embed", "reason"}`` oder ``{"kind": "none"}``.

    ``file`` ist ein normales Video ohne HLS (kein Livestream); der Kiosk spielt es als Schleife.
    """
    clean = url.strip()
    if not clean:
        return {"kind": "none"}
    current = time.time() if now is None else now
    if is_hls(clean):
        return {"kind": "hls", "upstream": clean, "expires": current + DEFAULT_LIFETIME, "title": "", "channel": "", "live": True}
    video_id = youtube_id(clean)
    if not video_id:
        return {"kind": "embed", "reason": "Diese Adresse ist weder YouTube noch HLS und wird eingebettet"}
    key = f"{video_id}:{height_limit}:{fps_limit}"
    with _resolve_lock:
        with _cache_lock:
            cached = _cache.get(key)
        if cached and not refresh and cached["expires"] - REFRESH_MARGIN > current:
            return dict(cached)
        try:
            result: dict[str, Any] = _youtube_source(video_id, height_limit, fps_limit if fps_limit in FPS_LIMITS else 30)
        except StreamError as exc:
            LOGGER.warning("Stream %s läuft eingebettet: %s", video_id, exc)
            return {"kind": "embed", "reason": str(exc)}
        result["expires"] = expires_at(result["upstream"], current)
        with _cache_lock:
            _cache[key] = dict(result)
    LOGGER.info(
        "Stream %s im eigenen Player (%s), gültig bis %s",
        video_id,
        "Livestream" if result["kind"] == "hls" else "Video in Schleife",
        time.strftime("%H:%M", time.localtime(result["expires"])),
    )
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
