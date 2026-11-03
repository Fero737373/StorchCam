"""Gaming-Endpunkte von StorchCam: reichen über den Unix-Socket an den (gespielten) Hilfsdienst weiter."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from storchcam import gaming
from tests.test_gaming_agent import FakeDocker, ga, make_agent, make_screen
from tests.test_server import post_json, request, running_server


@pytest.fixture
def agent_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = make_agent(tmp_path, FakeDocker())
    path = tmp_path / "agent.sock"
    server = ga.AgentServer(str(path), agent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("STORCHCAM_GAMING_SOCKET", str(path))
    yield agent
    server.shutdown()
    server.server_close()


def test_without_agent_the_menu_explains_the_setup(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config["admin"]["require_pin"] = False
    monkeypatch.setenv("STORCHCAM_GAMING_SOCKET", str(tmp_path / "fehlt.sock"))
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, result = request(address, "GET", "/api/gaming/status")
        assert status == 200 and result["agent"] is False and "install-agent.sh" in result["install_command"]
        assert request(address, "GET", "/api/gaming/roms")[0] == 503
    assert gaming.brief(gaming.AgentClient(tmp_path / "fehlt.sock")) == {"available": False, "running": False}


def test_install_needs_confirmation_then_runs_and_uploads(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_socket: ga.GamingAgent) -> None:
    config["admin"]["require_pin"] = False
    home = tmp_path / "home"
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, folders = request(address, "GET", "/api/gaming/folders?path=")
        assert status == 200 and folders["roots"][0]["path"] == str(home)
        assert post_json(address, "/api/gaming/folders", {"path": str(home), "name": "GAMING"})[0] == 200
        assert post_json(address, "/api/gaming/install", {"folder": str(home / "GAMING"), "port": 3001})[0] == 400  # ohne Bestätigung
        status, _headers, job = post_json(address, "/api/gaming/install", {"folder": str(home / "GAMING"), "port": 3001, "confirm": True})
        assert status == 200 and job["state"] == "done"
        status, _headers, state = request(address, "GET", "/api/gaming/status")
        assert state["agent"] and state["container"]["running"] and state["settings"] == {"pause_camera": False}
        # Am Bildschirm selbst nur die IP des Pi: der Kiosk im Container kennt weder raspberrypi.local noch localhost des Pi.
        agent_socket.address = lambda: "192.168.178.40"
        status, _headers, access = request(address, "GET", "/api/gaming/access")
        assert access["urls"] == ["https://192.168.178.40:3001/"] and access["on_pi"] is True
        assert access["user"] == "spieler" and access["qr"].startswith("data:image/svg+xml") and access["scan_url"] == "https://192.168.178.40:3001/"
        status, _headers, saved = request(address, "PUT", "/api/gaming/roms?name=Tetris.gb", body=b"GB-ROM", headers={"Content-Length": "6"})
        assert status == 200 and saved["system"] == "Game Boy"
        assert (home / "GAMING" / "roms" / "Tetris.gb").read_bytes() == b"GB-ROM"
        status, _headers, roms = request(address, "GET", "/api/gaming/roms")
        assert [item["name"] for item in roms["files"]] == ["Tetris.gb"]
        status, _headers, stopped = post_json(address, "/api/gaming/control", {"action": "stop"})
        assert status == 200
        assert post_json(address, "/api/gaming/update", {"rollback": True})[0] == 400


def test_gaming_needs_a_session_from_other_devices(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_socket: ga.GamingAgent) -> None:
    with running_server(config, monkeypatch, tmp_path) as address:
        # require_pin ist an und keine Sitzung vorhanden
        assert request(address, "GET", "/api/gaming/status")[0] == 401
        assert request(address, "PUT", "/api/gaming/roms?name=x.nes", body=b"x", headers={"Content-Length": "1"})[0] == 401


def test_gaming_on_the_pi_screen_is_started_and_ended_over_the_menu(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_socket: ga.GamingAgent) -> None:
    config["admin"]["require_pin"] = False
    screen, calls = make_screen(tmp_path)
    agent_socket.screen = screen
    with running_server(config, monkeypatch, tmp_path) as address:
        status, _headers, result = post_json(address, "/api/gaming/screen", {"action": "start"})
        assert status == 409 and "Speicherordner" in result["error"]  # erst Gaming einrichten
        agent_socket.install(str(tmp_path / "home"), 3001)
        status, _headers, started = post_json(address, "/api/gaming/screen", {"action": "start"})
        assert status == 200 and started["running"] is True and calls[0][0][1] == "--fullscreen"
        assert request(address, "GET", "/api/gaming/status")[2]["screen"]["running"] is True
        assert request(address, "GET", "/api/gaming/screen")[2]["running"] is True
        # Der Nachtmodus schaltet das Licht nicht ab, solange am Bildschirm gespielt wird.
        assert gaming.screen_running() is True
        status, _headers, power = post_json(address, "/api/display/power", {"on": False})
        assert status == 200 and power["switched"] is False and power["gaming"] is True
        status, _headers, stopped = post_json(address, "/api/gaming/screen", {"action": "stop"})
        assert status == 200 and stopped["running"] is False


def test_screen_needs_a_session_from_other_devices(config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_socket: ga.GamingAgent) -> None:
    with running_server(config, monkeypatch, tmp_path) as address:
        assert post_json(address, "/api/gaming/screen", {"action": "start"})[0] == 401
        assert request(address, "GET", "/api/gaming/screen")[0] == 401
