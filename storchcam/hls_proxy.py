"""Signed pass-through for HLS playlists and segments.

The kiosk only talks to the local server, so the CSP can stay at ``'self'`` and CORS rules of the
stream host do not matter. Every upstream address inside a playlist is replaced by a local address
with an HMAC signature; the endpoints therefore only fetch what a resolved playlist pointed to.
Master playlists are thinned out to variants the Pi decodes comfortably (height and frame rate).
Single video files (ordinary YouTube videos) go through the segment endpoint with ``Range`` support.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

PLAYLIST_PATH = "/api/stream/hls/playlist"
SEGMENT_PATH = "/api/stream/hls/segment"
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
MAX_FPS = 30
CHUNK_BYTES = 64 * 1024
# Wie yt-dlp: große Videodateien in 10-MB-Stücken holen, sonst bremst googlevideo die Verbindung.
RANGE_BYTES = 10 * 1024 * 1024
RANGE = re.compile(r"bytes=(\d+)-(\d*)")
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')
ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
# Neu bei jedem Start: alte Adressen aus einem früheren Lauf sind damit ungültig.
_KEY = secrets.token_bytes(32)


class ProxyError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signature(url: str) -> str:
    return _encode(hmac.new(_KEY, url.encode("utf-8"), hashlib.sha256).digest()[:18])


def local_address(url: str, playlist: bool) -> str:
    path = PLAYLIST_PATH if playlist else SEGMENT_PATH
    return f"{path}?{urlencode({'u': _encode(url.encode('utf-8')), 's': signature(url)})}"


def verified_url(encoded: str, sig: str) -> str:
    try:
        url = _decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProxyError(400, "Ungültige Streamadresse") from exc
    if not sig or not hmac.compare_digest(signature(url), sig):
        raise ProxyError(403, "Streamadresse nicht freigegeben")
    if urlparse(url).scheme not in {"http", "https"}:
        raise ProxyError(400, "Nur HTTP- und HTTPS-Quellen")
    return url


def _attributes(tag_line: str) -> dict[str, str]:
    _name, _, values = tag_line.partition(":")
    return {key: value.strip('"') for key, value in ATTRIBUTE.findall(values)}


def _variant_allowed(attributes: dict[str, str], max_height: int, max_fps: int = MAX_FPS) -> bool:
    resolution = attributes.get("RESOLUTION", "")
    try:
        height = int(resolution.lower().split("x")[1]) if "x" in resolution.lower() else 0
        fps = float(attributes.get("FRAME-RATE") or 0)
    except ValueError:
        return True
    return (not height or height <= max_height) and (not fps or fps <= max_fps + 0.5)


def _rewrite_uri_attribute(line: str, base_url: str, *, playlist: bool) -> str:
    def replace(match: re.Match[str]) -> str:
        return f'URI="{local_address(urljoin(base_url, match.group(1)), playlist)}"'

    return URI_ATTRIBUTE.sub(replace, line)


def rewrite_playlist(text: str, base_url: str, max_height: int = 1080, max_fps: int = MAX_FPS) -> str:
    """Upstream-Adressen durch signierte lokale ersetzen; zu große oder zu schnelle Varianten weglassen."""
    lines = [line.strip() for line in text.splitlines()]
    variants = [line for line in lines if line.startswith("#EXT-X-STREAM-INF")]
    # Bleibt nach dem Filtern keine Variante übrig, lieber alle behalten als gar kein Bild.
    keep_all = bool(variants) and not any(_variant_allowed(_attributes(line), max_height, max_fps) for line in variants)
    output: list[str] = []
    next_is_playlist = False
    skip_next_uri = False
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            if not keep_all and not _variant_allowed(_attributes(line), max_height, max_fps):
                skip_next_uri = True
                continue
            next_is_playlist = True
            output.append(line)
        elif line.startswith("#EXT-X-I-FRAME-STREAM-INF"):
            continue  # Vorschaubilder zum Spulen braucht der Kiosk nicht
        elif line.startswith("#"):
            output.append(_rewrite_uri_attribute(line, base_url, playlist=line.startswith("#EXT-X-MEDIA:")))
        elif not line:
            output.append("")
        elif skip_next_uri:
            skip_next_uri = False
        else:
            output.append(local_address(urljoin(base_url, line), next_is_playlist))
            next_is_playlist = False
    return "\n".join(output).rstrip("\n") + "\n"


def bounded_range(header: str) -> str:
    """``Range`` des Players weiterreichen, offene Bereiche aber auf ein Stück begrenzen."""
    match = RANGE.fullmatch(header.strip())
    if not match:
        return ""
    start = int(match.group(1))
    end = start + RANGE_BYTES - 1
    if match.group(2):
        end = min(end, int(match.group(2)))
    return f"bytes={start}-{end}" if end >= start else ""


def open_upstream(url: str, timeout: float = 15, *, byte_range: str = "") -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if byte_range:
        headers["Range"] = byte_range
    request = urllib.request.Request(url, headers=headers)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise ProxyError(502, f"Streamquelle antwortet mit {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProxyError(504, "Streamquelle nicht erreichbar") from exc


def fetch_playlist(url: str, max_height: int, max_fps: int = MAX_FPS) -> bytes:
    with open_upstream(url) as response:
        raw = response.read(MAX_PLAYLIST_BYTES + 1)
        final_url = response.geturl() or url
    if len(raw) > MAX_PLAYLIST_BYTES:
        raise ProxyError(502, "Playlist ist zu groß")
    text = raw.decode("utf-8", "replace")
    if not text.lstrip("﻿ \r\n").startswith("#EXTM3U"):
        raise ProxyError(502, "Quelle liefert keine HLS-Playlist")
    return rewrite_playlist(text, final_url, max_height, max_fps).encode("utf-8")
