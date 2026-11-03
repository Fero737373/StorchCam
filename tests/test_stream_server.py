from __future__ import annotations

import functools
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from storchcam.providers import stream_source
from tests.test_server import request, running_server

SEGMENT = bytes(range(256)) * 300


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return None


class RangeHandler(BaseHTTPRequestHandler):
    """Wie googlevideo: liefert Teilbereiche einer Videodatei mit 206."""

    def do_GET(self) -> None:  # noqa: N802
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        start = int(match.group(1)) if match else 0
        end = int(match.group(2)) if match and match.group(2) else len(SEGMENT) - 1
        body = SEGMENT[start : end + 1]
        self.send_response(206 if match else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if match:
            self.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{len(SEGMENT)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return None


@contextmanager
def upstream(tmp_path: Path) -> Iterator[str]:
    """Kleine HLS-Quelle wie eine Kamera: Master, eine Variante, ein Segment."""
    root = tmp_path / "cam"
    (root / "hd").mkdir(parents=True)
    (root / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720,FRAME-RATE=25\nhd/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=9000000,RESOLUTION=3840x2160,FRAME-RATE=25\nuhd/index.m3u8\n",
        encoding="utf-8",
    )
    (root / "hd" / "index.m3u8").write_text("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nseg0.ts\n", encoding="utf-8")
    (root / "hd" / "seg0.ts").write_bytes(SEGMENT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def only_uri(body: bytes) -> str:
    lines = [line for line in body.decode().splitlines() if line and not line.startswith("#")]
    assert len(lines) == 1
    return lines[0]


def test_hls_source_plays_through_the_signed_local_proxy(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with upstream(tmp_path) as base:
        config["stream"]["url"] = f"{base}/master.m3u8"
        with running_server(config, monkeypatch, tmp_path) as address:
            status, headers, source = request(address, "GET", "/api/stream/source")
            assert status == 200 and source["kind"] == "hls" and source["url"].startswith("/api/stream/hls/playlist?")
            assert "media-src 'self' blob:" in headers["Content-Security-Policy"]

            status, headers, master = request(address, "GET", source["url"])
            assert status == 200 and headers["Content-Type"] == "application/vnd.apple.mpegurl"
            assert b"3840x2160" not in master  # 4K überfordert den Pi und fällt weg
            variant = only_uri(master)
            assert variant.startswith("/api/stream/hls/playlist?")

            status, _headers, media = request(address, "GET", variant)
            segment = only_uri(media)
            assert status == 200 and segment.startswith("/api/stream/hls/segment?")

            status, headers, body = request(address, "GET", segment)
            assert status == 200 and body == SEGMENT and headers["Content-Length"] == str(len(SEGMENT))

            query = parse_qs(urlparse(segment).query)
            forged = f"/api/stream/hls/segment?u={query['u'][0]}&s=AAAAAAAAAAAAAAAAAAAAAAAA"
            assert request(address, "GET", forged)[0] == 403


def test_unreachable_source_reports_gateway_error(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["stream"]["url"] = "http://127.0.0.1:9/gone/master.m3u8"
    with running_server(config, monkeypatch, tmp_path) as address:
        _status, _headers, source = request(address, "GET", "/api/stream/source")
        status, _headers, body = request(address, "GET", source["url"])
        assert status == 504 and body["ok"] is False


def test_other_pages_stay_embedded_and_unsaved_urls_need_the_menu(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["stream"]["url"] = "https://example.org/webcam"
    config["admin"].update({"require_pin": True, "pin_hash": "pbkdf2_sha256$1$aa$bb"})
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, source = request(address, "GET", "/api/stream/source")
        assert status == 200 and source["kind"] == "embed"
        status, _headers, _body = request(address, "GET", "/api/stream/source?url=https%3A%2F%2Fcam.example.org%2Fx.m3u8")
        assert status == 401


def test_youtube_source_is_resolved_on_the_server(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resolved = {"kind": "hls", "upstream": "https://manifest.googlevideo.com/api/manifest/hls_variant/expire/4102444800/x/index.m3u8", "title": "Hafen", "channel": "Cam", "live": True, "expires": 4_102_444_800.0}
    monkeypatch.setattr(stream_source, "resolve", lambda url, **_options: dict(resolved))
    config["stream"]["url"] = "https://www.youtube.com/embed/W2CicgFq5oo"
    with running_server(config, monkeypatch, tmp_path) as address:
        _status, _headers, source = request(address, "GET", "/api/stream/source")
        assert source["kind"] == "hls" and source["title"] == "Hafen" and source["expires_in"] > 3600
        assert "googlevideo" not in source["url"]


def test_ordinary_video_streams_as_file_with_ranges(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    upstream_url = f"http://127.0.0.1:{server.server_address[1]}/videoplayback?expire=4102444800"
    seen: dict = {}

    def fake_resolve(url: str, **options: object) -> dict:
        seen.update(options)
        return {"kind": "file", "upstream": upstream_url, "title": "Nestbau", "channel": "", "live": False, "expires": 4_102_444_800.0}

    monkeypatch.setattr(stream_source, "resolve", fake_resolve)
    config["stream"].update({"url": "https://youtu.be/W2CicgFq5oo", "quality": "720"})
    try:
        with running_server(config, monkeypatch, tmp_path) as address:
            _status, _headers, source = request(address, "GET", "/api/stream/source")
            assert source["kind"] == "file" and source["url"].startswith("/api/stream/hls/segment?") and seen["height_limit"] == 720
            status, headers, body = request(address, "GET", source["url"], headers={"Range": "bytes=100-199"})
            assert status == 206 and body == SEGMENT[100:200]
            assert headers["Content-Range"] == f"bytes 100-199/{len(SEGMENT)}" and headers["Accept-Ranges"] == "bytes"
    finally:
        server.shutdown()
        server.server_close()
