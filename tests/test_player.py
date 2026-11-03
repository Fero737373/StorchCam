from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import pytest

from storchcam import bluetooth, player, spotify_connect
from storchcam.library import SpotifyLibrary, YouTubeMusicLibrary
from storchcam.providers import music
from tests.test_server import post_json, request, running_server


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORCHCAM_CONFIG", str(tmp_path / "config.json"))
    bluetooth._job.update({"busy": False, "action": "", "mac": "", "message": "", "ok": True})


def wait_for_job() -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while bluetooth._job["busy"]:
        assert time.monotonic() < deadline, "Bluetooth-Aufgabe endet nicht"
        time.sleep(0.01)
    return dict(bluetooth._job)


SPEAKER = "AA:BB:CC:DD:EE:01"
PHONE = "AA:BB:CC:DD:EE:02"
PAD = "AA:BB:CC:DD:EE:04"


class FakeBluez:
    """Antwortet wie bluetoothctl mit einem Adapter, einem Lautsprecher und einem Handy."""

    def __init__(self, adapter: bool = True) -> None:
        self.adapter = adapter
        self.paired = False
        self.connected = False
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *arguments: str, timeout: float = 8, duration: int = 0) -> str:
        self.calls.append(arguments)
        command = arguments[2:] if arguments[0] == "--agent" else arguments
        if command[0] == "list":
            return "Controller 2C:CF:67:56:81:62 storchcam [default]\n" if self.adapter else ""
        if command[0] == "show":
            return "Controller 2C:CF:67:56:81:62\n\tPowered: yes\n"
        if command[0] == "devices":
            return f"Device {SPEAKER} Box\nDevice {PHONE} Handy\nDevice AA:BB:CC:DD:EE:03 AA-BB-CC-DD-EE-03\nDevice {PAD} Wireless Controller\n"
        if command[0] == "info":
            if command[1] == SPEAKER:
                return (
                    f"Device {SPEAKER}\n\tName: Box\n\tAlias: Box\n\tIcon: audio-card\n"
                    f"\tPaired: {'yes' if self.paired else 'no'}\n\tConnected: {'yes' if self.connected else 'no'}\n"
                )
            if command[1] == PAD:
                return (f"Device {PAD}\n\tName: Wireless Controller\n\tIcon: input-gaming\n\tPaired: no\n"
                        "\tUUID: Human Interface Device... (00001124-0000-1000-8000-00805f9b34fb)\n")
            if command[1] == PHONE:
                return f"Device {PHONE}\n\tName: Handy\n\tIcon: phone\n\tPaired: no\n\tUUID: Handsfree (0000111e)\n"
            return "Device AA:BB:CC:DD:EE:03\n\tPaired: no\n"
        if command[0] == "pair":
            self.paired = True
            return "Attempting to pair\nPairing successful\n"
        if command[0] == "connect":
            self.connected = True
            return "Connection successful\n"
        return ""


def test_bluetooth_without_adapter_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bluetooth, "_run", FakeBluez(adapter=False))
    status = bluetooth.status()
    assert status["available"] is False and "kein Bluetooth" in status["error"]
    with pytest.raises(bluetooth.BluetoothError, match="kein Bluetooth"):
        bluetooth.scan()


def test_bluetooth_lists_speakers_and_controllers_and_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    bluez = FakeBluez()
    monkeypatch.setattr(bluetooth, "_run", bluez)
    status = bluetooth.status()
    assert status["available"] and [(device["name"], device["kind"]) for device in status["devices"]] == [("Box", "audio"), ("Wireless Controller", "gamepad")]
    bluetooth.connect(SPEAKER.lower())
    assert wait_for_job() == {"busy": False, "action": "connect", "mac": SPEAKER, "message": "Verbunden", "ok": True}
    assert ("--agent", "NoInputNoOutput", "pair", SPEAKER) in bluez.calls and ("trust", SPEAKER) in bluez.calls
    assert bluetooth.status()["devices"][0]["connected"]
    with pytest.raises(bluetooth.BluetoothError, match="Ungültige"):
        bluetooth.disconnect("box")


def test_bluetooth_pairs_a_controller_without_touching_the_sound(monkeypatch: pytest.MonkeyPatch) -> None:
    bluez = FakeBluez()
    monkeypatch.setattr(bluetooth, "_run", bluez)
    volume: list[int] = []
    monkeypatch.setattr(bluetooth.player, "full_output_volume", lambda **_kwargs: volume.append(1))
    bluetooth.connect(PAD)
    assert wait_for_job()["message"] == "Verbunden" and ("trust", PAD) in bluez.calls and not volume


def test_bluetooth_failed_pairing_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    bluez = FakeBluez()
    original = bluez.__call__

    def refuse(*arguments: str, **kwargs: Any) -> str:
        if "pair" in arguments:
            return "Failed to pair: org.bluez.Error.AuthenticationFailed\n"
        return original(*arguments, **kwargs)

    monkeypatch.setattr(bluetooth, "_run", refuse)
    bluetooth.connect(SPEAKER)
    job = wait_for_job()
    assert not job["ok"] and "Kopplungsmodus" in job["message"]


def fake_spotify(monkeypatch: pytest.MonkeyPatch, responses: dict[tuple[str, str], Any]) -> list[tuple[str, str, Any]]:
    calls: list[tuple[str, str, Any]] = []

    def fake(method: str, path: str, payload: Any = None) -> Any:
        calls.append((method, path, payload))
        for (want_method, prefix), response in responses.items():
            if method == want_method and path.startswith(prefix):
                return response
        return None

    monkeypatch.setattr(music, "_spotify_api", fake)
    return calls


def test_spotify_search_and_play_on_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_spotify(monkeypatch, {
        ("GET", "/search"): {
            "tracks": {"items": [{"uri": "spotify:track:abc", "name": "Song", "artists": [{"name": "Band"}], "album": {"images": [{"url": "big"}, {"url": "small"}]}}]},
            "albums": {"items": [{"uri": "spotify:album:def", "name": "Album", "artists": [{"name": "Band"}], "images": []}]},
            "playlists": {"items": [None, {"uri": "spotify:playlist:ghi", "name": "Liste", "owner": {"display_name": "Ich"}}]},
        },
        ("GET", "/me/player/devices"): {"devices": [{"name": "Handy", "id": "h"}, {"name": spotify_connect.DEVICE_NAME, "id": "pi 1"}]},
    })
    results = SpotifyLibrary(music._spotify_api).search("song")
    assert [(item["kind"], item["id"], item["subtitle"]) for item in results] == [
        ("song", "spotify:track:abc", "Band"), ("album", "spotify:album:def", "Band"), ("playlist", "spotify:playlist:ghi", "Ich"),
    ]
    assert results[0]["cover"] == "small"
    music.spotify_play("song", "spotify:track:abc")
    music.spotify_play("album", "spotify:album:def", start=3)
    music.spotify_play("playlist", "spotify:playlist:ghi", shuffle=True)
    plays = [call for call in calls if call[0] == "PUT"]
    assert plays == [
        ("PUT", "/me/player/play?device_id=pi%201", {"uris": ["spotify:track:abc"]}),
        ("PUT", "/me/player/shuffle?state=false&device_id=pi%201", None),
        ("PUT", "/me/player/play?device_id=pi%201", {"context_uri": "spotify:album:def", "offset": {"position": 3}}),
        ("PUT", "/me/player/shuffle?state=true&device_id=pi%201", None),
        ("PUT", "/me/player/play?device_id=pi%201", {"context_uri": "spotify:playlist:ghi"}),
    ]
    with pytest.raises(music.MusicError):
        music.spotify_play("song", "https://evil.example/")


def test_spotify_play_needs_paired_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_spotify(monkeypatch, {("GET", "/me/player/devices"): {"devices": [{"name": "Handy", "id": "h"}]}})
    with pytest.raises(music.MusicError, match="koppeln"):
        music.spotify_play("song", "spotify:track:abc")


def test_spotify_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_spotify(monkeypatch, {("GET", "/me/player"): {"is_playing": True}})
    music.spotify_control("toggle")
    music.spotify_control("next")
    music.spotify_control("volume", 140)
    music.spotify_control("seek", 61.5)
    music.spotify_control("repeat", "one")
    music.spotify_control("shuffle", True)
    assert calls[1:] == [
        ("PUT", "/me/player/pause", None), ("POST", "/me/player/next", None), ("PUT", "/me/player/volume?volume_percent=100", None),
        ("PUT", "/me/player/seek?position_ms=61500", None), ("PUT", "/me/player/repeat?state=track", None), ("PUT", "/me/player/shuffle?state=true", None),
    ]
    for action in ("explode", "move"):
        with pytest.raises(music.MusicError):
            music.spotify_control(action)


def test_player_search_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeApi:
        def search(self, query: str, limit: int) -> list[dict[str, Any]]:
            return [
                {"resultType": "song", "videoId": "v1", "title": "Lied", "artists": [{"name": "A"}], "thumbnails": [{"url": "https://lh3.googleusercontent.com/x=w60-h60"}]},
                {"resultType": "artist", "browseId": "UC1", "artist": "A"},
                {"resultType": "album", "browseId": "MPRE1", "title": "Platte", "artists": [{"name": "A"}]},
                {"resultType": "playlist", "playlistId": "PL1", "title": "Mix", "author": "Ich"},
                {"resultType": "song", "title": "ohne ID"},
            ]

    monkeypatch.setattr(player.ytmusic, "api", lambda: FakeApi())
    results = YouTubeMusicLibrary().search("lied")
    assert [(item["kind"], item["id"], item["title"], item["subtitle"]) for item in results] == [
        ("song", "v1", "Lied", "A"), ("artist", "UC1", "A", ""), ("album", "MPRE1", "Platte", "A"), ("playlist", "PL1", "Mix", "Ich"),
    ]
    assert YouTubeMusicLibrary().search("  ") == []


def test_player_controls_need_something_playing() -> None:
    assert player.now_playing() is None
    with pytest.raises(player.PlayerError, match="läuft gerade nichts"):
        player.control("toggle")
    with pytest.raises(player.PlayerError):
        player.play("artist", "UC1")
    player.control("volume", "35")
    assert player.default_player().volume == 35


def test_librespot_output_is_parsed() -> None:
    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = io.StringIO(
                "[2026-10-25T10:00:00Z INFO  librespot] Browse to: https://spotify.com/pair?code=ABCD\n"
                "[2026-10-25T10:00:00Z INFO  librespot] If prompted, enter code: ABCD\n"
            )

        def wait(self) -> int:
            return 0

        def poll(self) -> int:
            return 0

    process = FakeProcess()
    spotify_connect._state.update({"process": process, "status": "pairing", "url": "", "code": ""})
    try:
        spotify_connect._read_output(process)  # type: ignore[arg-type]
        status = spotify_connect.status()
        assert status["url"] == "https://spotify.com/pair?code=ABCD" and status["code"] == "ABCD"
        assert status["status"] == "error"  # Prozess beendet, bevor die Kopplung bestätigt wurde
    finally:
        spotify_connect._state.update({"process": None, "status": "stopped", "url": "", "code": "", "started": 0.0})


def test_pairing_without_librespot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_connect.shutil, "which", lambda _name: None)
    with pytest.raises(spotify_connect.SpotifyConnectError, match="nicht installiert"):
        spotify_connect.pair()
    spotify_connect.ensure_running()  # nicht gekoppelt: startet nichts und wirft nicht


def test_player_endpoints(monkeypatch: pytest.MonkeyPatch, config: dict, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    played: list[tuple[str, str]] = []
    controlled: list[tuple[str, Any]] = []
    monkeypatch.setattr(bluetooth, "_run", FakeBluez(adapter=False))
    monkeypatch.setattr(music, "spotify_play", lambda kind, uri, start=0, shuffle=False: played.append((kind, uri)))
    monkeypatch.setattr(music, "spotify_control", lambda action, value=None: controlled.append((action, value)))
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, result = request(address, "GET", "/api/bluetooth")
        assert status == 200 and result["available"] is False and "kein Bluetooth" in result["error"]
        status, _headers, result = post_json(address, "/api/bluetooth/scan", {})
        assert status == 400 and "kein Bluetooth" in result["error"]
        assert post_json(address, "/api/player/play", {"service": "spotify", "kind": "song", "id": "spotify:track:abc"})[0] == 200
        assert post_json(address, "/api/player/control", {"target": "spotify", "action": "volume", "value": 40})[0] == 200
        assert post_json(address, "/api/player/control", {"target": "radio", "action": "toggle"})[0] == 400
        assert post_json(address, "/api/player/search", {"service": "napster", "query": "x"})[0] == 400
        status, _headers, result = post_json(address, "/api/player/control", {"target": "pi", "action": "toggle"})
        assert status == 502 and "läuft gerade nichts" in result["error"]
    assert played == [("song", "spotify:track:abc")] and controlled == [("volume", 40)]


def test_own_playlists(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeApi:
        def get_library_playlists(self, limit: int) -> list[dict[str, Any]]:
            return [
                {"playlistId": "LM", "title": "Liked Music", "count": None, "thumbnails": []},
                {"playlistId": "PL1", "title": "jogging", "count": "53", "thumbnails": [{"url": "https://lh3.googleusercontent.com/x=w60-h60"}]},
                {"title": "ohne ID"},
            ]

    monkeypatch.setattr(player.ytmusic, "api", lambda: FakeApi())
    assert [(item["id"], item["subtitle"]) for item in YouTubeMusicLibrary().playlists()] == [("LM", ""), ("PL1", "53 Songs")]
    fake_spotify(monkeypatch, {("GET", "/me/playlists"): {"items": [
        {"uri": "spotify:playlist:a", "name": "Neu", "items": {"total": 12}, "images": [{"url": "c"}]},
        {"uri": "spotify:playlist:b", "name": "Alt", "tracks": {"total": 3}, "images": None},
        None,
    ]}})
    assert [(item["id"], item["subtitle"], item["cover"]) for item in SpotifyLibrary(music._spotify_api).playlists()] == [
        ("spotify:playlist:a", "12 Songs", "c"), ("spotify:playlist:b", "3 Songs", ""),
    ]
