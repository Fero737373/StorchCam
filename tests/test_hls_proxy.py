from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from storchcam import hls_proxy

MASTER = """#EXTM3U
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="de",URI="audio/de.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=7000000,RESOLUTION=1920x1080,FRAME-RATE=60,CODECS="avc1.64002a,mp4a.40.2"
hd60/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,FRAME-RATE=30,CODECS="avc1.640028,mp4a.40.2"
https://cdn.example.org/hd30/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,FRAME-RATE=30
hd720/index.m3u8
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=200000,URI="iframes.m3u8"
"""

MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:812
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.org/k1"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:5.0,
seg812.ts
#EXTINF:5.0,
https://other.example.org/seg813.ts
"""


def decoded(local: str) -> tuple[str, str]:
    parsed = urlparse(local)
    query = parse_qs(parsed.query)
    return parsed.path, hls_proxy.verified_url(query["u"][0], query["s"][0])


def uris(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_signed_addresses_round_trip_and_reject_tampering() -> None:
    local = hls_proxy.local_address("https://cdn.example.org/a.m3u8", playlist=True)
    assert decoded(local) == (hls_proxy.PLAYLIST_PATH, "https://cdn.example.org/a.m3u8")
    query = parse_qs(urlparse(local).query)
    other = hls_proxy.local_address("https://evil.example.org/x", playlist=False)
    with pytest.raises(hls_proxy.ProxyError) as error:
        hls_proxy.verified_url(parse_qs(urlparse(other).query)["u"][0], query["s"][0])
    assert error.value.status == 403
    with pytest.raises(hls_proxy.ProxyError):
        hls_proxy.verified_url("%%%", "x")


def test_signed_non_http_sources_are_refused() -> None:
    local = hls_proxy.local_address("file:///etc/passwd", playlist=False)
    query = parse_qs(urlparse(local).query)
    with pytest.raises(hls_proxy.ProxyError) as error:
        hls_proxy.verified_url(query["u"][0], query["s"][0])
    assert error.value.status == 400


def test_master_playlist_keeps_pi_friendly_variants_and_rewrites_all_addresses() -> None:
    text = hls_proxy.rewrite_playlist(MASTER, "https://manifest.example.org/live/master.m3u8", max_height=1080)
    variants = [decoded(uri) for uri in uris(text)]
    assert variants == [
        (hls_proxy.PLAYLIST_PATH, "https://cdn.example.org/hd30/index.m3u8"),
        (hls_proxy.PLAYLIST_PATH, "https://manifest.example.org/live/hd720/index.m3u8"),
    ]
    assert "FRAME-RATE=60" not in text and "I-FRAME" not in text
    media_line = next(line for line in text.splitlines() if line.startswith("#EXT-X-MEDIA:"))
    audio = media_line.split('URI="', 1)[1].split('"', 1)[0]
    assert decoded(audio) == (hls_proxy.PLAYLIST_PATH, "https://manifest.example.org/live/audio/de.m3u8")
    assert "googlevideo" not in text and "example.org" not in "".join(uris(text))


def test_sixty_fps_only_when_allowed() -> None:
    text = hls_proxy.rewrite_playlist(MASTER, "https://m.example.org/master.m3u8", max_height=1080, max_fps=60)
    assert "FRAME-RATE=60" in text and len(uris(text)) == 3


def test_lower_quality_setting_drops_bigger_variants() -> None:
    text = hls_proxy.rewrite_playlist(MASTER, "https://m.example.org/master.m3u8", max_height=720)
    assert [decoded(uri)[1] for uri in uris(text)] == ["https://m.example.org/hd720/index.m3u8"]


def test_all_variants_stay_when_none_fits() -> None:
    only_big = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=9000000,RESOLUTION=2560x1440,FRAME-RATE=60\nqhd.m3u8\n"
    assert len(uris(hls_proxy.rewrite_playlist(only_big, "https://m.example.org/", max_height=720))) == 1


def test_media_playlist_segments_keys_and_init_are_segments() -> None:
    text = hls_proxy.rewrite_playlist(MEDIA, "https://cdn.example.org/hd30/index.m3u8")
    assert [decoded(uri) for uri in uris(text)] == [
        (hls_proxy.SEGMENT_PATH, "https://cdn.example.org/hd30/seg812.ts"),
        (hls_proxy.SEGMENT_PATH, "https://other.example.org/seg813.ts"),
    ]
    key = next(line for line in text.splitlines() if line.startswith("#EXT-X-KEY"))
    init = next(line for line in text.splitlines() if line.startswith("#EXT-X-MAP"))
    assert decoded(key.split('URI="')[1].split('"')[0]) == (hls_proxy.SEGMENT_PATH, "https://keys.example.org/k1")
    assert decoded(init.split('URI="')[1].split('"')[0]) == (hls_proxy.SEGMENT_PATH, "https://cdn.example.org/hd30/init.mp4")
    assert "#EXT-X-MEDIA-SEQUENCE:812" in text and text.count("#EXTINF:5.0,") == 2


def test_open_ranges_are_cut_into_pieces() -> None:
    assert hls_proxy.bounded_range("bytes=0-") == f"bytes=0-{hls_proxy.RANGE_BYTES - 1}"
    assert hls_proxy.bounded_range("bytes=500-999") == "bytes=500-999"
    assert hls_proxy.bounded_range("bytes=5-") == f"bytes=5-{hls_proxy.RANGE_BYTES + 4}"
    assert hls_proxy.bounded_range("") == "" and hls_proxy.bounded_range("bytes=-500") == "" and hls_proxy.bounded_range("items=1-2") == ""
