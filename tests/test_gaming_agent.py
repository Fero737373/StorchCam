"""Gaming-Hilfsdienst: Ordnerwahl, Installation, Start/Stopp, Update, Spiele – mit gespieltem Docker."""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

ga = pytest.importorskip("storchcam.gaming_agent", reason="Der Gaming-Hilfsdienst läuft nur unter Linux")


class FakeDocker(ga.Docker):
    """Merkt sich Aufrufe und spielt einen Docker-Daemon mit höchstens einem Container."""

    def __init__(self, *, running: bool = True, existing: dict[str, Any] | None = None, fail: str = "") -> None:
        super().__init__("docker")
        self.calls: list[tuple[str, ...]] = []
        self.containers_by_name: dict[str, dict[str, Any]] = dict(existing or {})
        self.running = running
        self.fail = fail
        self.images = {"lscr.io/linuxserver/retroarch:latest": "sha256:new"}

    def call(self, *arguments: str, timeout: float = 60) -> str:
        self.calls.append(arguments)
        if self.fail and arguments[0] == self.fail:
            raise ga.AgentError(f"{self.fail} kaputt", 502)
        return ""

    def version(self) -> str:
        if self.fail == "version":
            raise ga.AgentError("Cannot connect to the Docker daemon", 503)
        return "27.3.1"

    def containers(self) -> list[dict[str, Any]]:
        return [{"Names": name, "Image": data["Config"]["Image"], "State": data["State"]["Status"], "Status": "Up"} for name, data in self.containers_by_name.items()]

    def inspect(self, name: str) -> dict[str, Any] | None:
        return self.containers_by_name.get(name)

    def image_id(self, reference: str) -> str:
        return self.images.get(reference, "")

    def pull(self, image: str, progress: Any) -> None:
        self.calls.append(("pull", image))
        if self.fail == "pull":
            raise ga.AgentError("Image ließ sich nicht laden: no space left", 502)
        progress("latest: Pulling from linuxserver/retroarch")
        progress("Status: Downloaded newer image")

    def compose_up(self, compose_file: Path, project: str = ga.PROJECT) -> None:
        self.calls.append(("compose", str(compose_file), project))
        text = compose_file.read_text()
        image = json.loads(re.search(r"image: (.+)", text)[1])
        name = re.search(r"container_name: (\S+)", text)[1]
        self.containers_by_name[name] = container(name, image=image, image_id=self.images.get(image, "sha256:old"), running=self.running)

    def tag(self, source: str, target: str) -> None:
        self.calls.append(("tag", source, target))
        self.images[target] = source


def container(name: str, *, image: str = "lscr.io/linuxserver/retroarch:latest", image_id: str = "sha256:old", running: bool = True, port: int = 3001) -> dict[str, Any]:
    return {"Name": f"/{name}", "Image": image_id, "State": {"Status": "running" if running else "exited"},
            "Config": {"Image": image, "Labels": {"org.opencontainers.image.version": "1.22.2-ls96", ga.MANAGED_LABEL: "managed"}},
            "NetworkSettings": {"Ports": {"3001/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(port)}]}},
            "Mounts": [{"Source": "/home/pi/GAMING/config", "Destination": "/config"}]}


def make_agent(tmp_path: Path, docker: FakeDocker, *, ready: bool = True, free: bool = True) -> ga.GamingAgent:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    config = ga.AgentConfig(allowed_roots=[str(home)], state_dir=str(tmp_path / "state"), socket_path=str(tmp_path / "agent.sock"))
    return ga.GamingAgent(config, docker, ready=lambda _port: ready, port_check=lambda _port: free, spawn=lambda work: work())


def test_folders_stay_inside_the_allowed_roots(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, FakeDocker())
    home = tmp_path / "home"
    (home / "Spiele").mkdir()
    (home / ".versteckt").mkdir()
    (home / "raus").symlink_to(tmp_path)  # Symlink aus der Freigabe heraus
    listing = agent.folders.listing(str(home))
    assert [entry["name"] for entry in listing["entries"]] == ["Spiele"]
    assert listing["writable"] is True and listing["free_bytes"] > 0
    for bad in (str(tmp_path), "/etc", f"{home}/../", "relativ", str(home / "raus")):
        with pytest.raises(ga.AgentError):
            agent.folders.resolve(bad)
    created = agent.folders.create(str(home), "GAMING")
    assert Path(created["path"]).is_dir()
    for name in ("../x", ".x", "a/b", "", "x" * 80):
        with pytest.raises(ga.AgentError):
            agent.folders.create(str(home), name)
    with pytest.raises(ga.AgentError, match="gibt es schon"):
        agent.folders.create(str(home), "GAMING")


def test_status_without_docker_and_with_a_foreign_retroarch(tmp_path: Path) -> None:
    assert make_agent(tmp_path, FakeDocker(fail="version")).status()["docker"] == {"ok": False, "version": "", "error": "Cannot connect to the Docker daemon"}
    docker = FakeDocker(existing={"retroarch": container("retroarch")})
    status = make_agent(tmp_path, docker).status()
    assert status["container"] is None and status["candidates"][0]["name"] == "retroarch"


def test_install_runs_real_steps_and_keeps_existing_data(tmp_path: Path) -> None:
    docker = FakeDocker()
    agent = make_agent(tmp_path, docker)
    folder = tmp_path / "home" / "GAMING"
    (folder / "roms").mkdir(parents=True)
    (folder / "roms" / "mario.nes").write_bytes(b"nes")
    job = agent.install(str(folder), 3001)
    assert job["state"] == "done" and [step["status"] for step in job["steps"]] == ["done"] * 5
    assert (folder / "roms" / "mario.nes").read_bytes() == b"nes"
    assert all((folder / name).is_dir() for name in ga.SUBFOLDERS)
    compose = (tmp_path / "state" / "compose" / "compose.yaml").read_text()
    assert f'"{folder}/roms:/config/roms"' in compose and '"3001:3001"' in compose and "privileged" not in compose
    assert ("pull", ga.IMAGE) in docker.calls
    status = agent.status()
    assert status["container"]["running"] and status["container"]["reachable"] and status["setup"]["folder"] == str(folder)
    access = agent.access()
    assert access["user"] == "spieler" and len(access["password"]) >= 12 and access["port"] == 3001
    with pytest.raises(ga.AgentError, match="bereits eingerichtet"):
        agent.install(str(folder), 3001)


def test_install_stops_on_busy_port_and_failed_pull_without_deleting(tmp_path: Path) -> None:
    folder = tmp_path / "home"
    busy = make_agent(tmp_path, FakeDocker(), free=False).install(str(folder), 3001)
    assert busy["state"] == "error" and "belegt" in busy["error"] and busy["steps"][2]["status"] == "pending"
    docker = FakeDocker(fail="pull")
    failed = make_agent(tmp_path, docker).install(str(folder), 3001)
    assert failed["state"] == "error" and failed["steps"][2]["status"] == "error" and not docker.containers_by_name
    assert (folder / "config").is_dir()  # angelegte Ordner bleiben


def test_start_stop_and_adopting_only_touch_the_gaming_container(tmp_path: Path) -> None:
    docker = FakeDocker(existing={"retroarch": container("retroarch"), "pihole": container("pihole", image="pihole/pihole")})
    agent = make_agent(tmp_path, docker)
    with pytest.raises(ga.AgentError):
        agent.adopt("pihole")
    agent.adopt("retroarch")
    agent.control("stop")
    agent.control("start")
    assert ("stop", "retroarch") in docker.calls and ("start", "retroarch") in docker.calls
    assert not any("pihole" in call for call in docker.calls)
    with pytest.raises(ga.AgentError, match="Übernommene"):
        agent.update()
    with pytest.raises(ga.AgentError):
        agent.control("rm")


def test_update_keeps_the_previous_version_for_a_way_back(tmp_path: Path) -> None:
    docker = FakeDocker()
    agent = make_agent(tmp_path, docker)
    agent.install(str(tmp_path / "home"), 3001)
    same = agent.update()
    assert same["state"] == "done" and same["steps"][0]["detail"] == "bereits aktuell"
    docker.images["lscr.io/linuxserver/retroarch:latest"] = "sha256:newer"
    job = agent.update()
    assert job["state"] == "done" and ("tag", "sha256:new", ga.PREVIOUS_TAG) in docker.calls
    back = agent.update(rollback=True)
    assert back["state"] == "done" and ga.PREVIOUS_TAG in (tmp_path / "state" / "compose" / "compose.yaml").read_text()


def test_an_installation_from_before_the_rename_keeps_its_container(tmp_path: Path) -> None:
    legacy = ga.LEGACY_NAMES
    docker = FakeDocker(existing={legacy.container: container(legacy.container, image_id="sha256:old")})
    agent = make_agent(tmp_path, docker)
    folder = tmp_path / "home"
    agent.store.update(folder=str(folder), port=3001, gpu=False, user="spieler", password="geheim", uid=1000, gid=1000, adopted="")
    status = agent.status()
    assert status["container"]["name"] == legacy.container and status["managed"] is True
    assert agent.store.read()["names"]["container"] == legacy.container
    with pytest.raises(ga.AgentError, match="bereits eingerichtet"):
        agent.install(str(folder), 3001)
    agent.control("stop")
    assert ("stop", legacy.container) in docker.calls
    job = agent.update()
    assert job["state"] == "done" and ("tag", "sha256:old", legacy.previous) in docker.calls
    compose = next(call for call in docker.calls if call[0] == "compose")
    assert compose[2] == legacy.project and f"container_name: {legacy.container}" in Path(compose[1]).read_text()
    assert ga.CONTAINER not in docker.containers_by_name  # kein zweiter Container unter neuem Namen


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.pid = 4242
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


def make_screen(tmp_path: Path, *, installed: bool = True, cores: bool = True, display: bool = True) -> tuple[ga.Screen, list[Any]]:
    """RetroArch am Bildschirm mit gespieltem Programmstart, Kern-Ordner und Wayland-Socket in tmp_path."""
    core_dir = tmp_path / "cores"
    core_dir.mkdir(exist_ok=True)
    if cores:
        (core_dir / "snes9x_libretro.so").write_bytes(b"")
    runtime = tmp_path / "run"
    runtime.mkdir(exist_ok=True)
    started: list[Any] = []
    if display:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(runtime / "wayland-0"))
        started.append(listener)
    config = ga.AgentConfig(allowed_roots=[str(tmp_path / "home")], state_dir=str(tmp_path / "state"), socket_path=str(tmp_path / "agent.sock"),
                            cores_dir=str(core_dir), runtime_dir=str(runtime))
    calls: list[Any] = []

    def popen(arguments: list[str], **options: Any) -> FakeProcess:
        calls.append((arguments, options))
        return FakeProcess()

    screen = ga.Screen(config, popen=popen, which=lambda name: f"/usr/bin/{name}" if installed else None, clock=lambda: 1000.0,
                       kill=lambda pid, sig: calls.append(("kill", pid, sig)))
    return screen, calls


def test_screen_explains_what_is_missing(tmp_path: Path) -> None:
    screen, _calls = make_screen(tmp_path, installed=False, cores=False, display=False)
    status = screen.status()
    assert status["installed"] is False and status["cores"] == 0 and status["display"] is False and status["running"] is False
    assert status["install_command"] == "sudo ./scripts/gaming/install-agent.sh --screen"
    folder = tmp_path / "home" / "GAMING"
    folder.mkdir(parents=True)
    with pytest.raises(ga.AgentError, match="nicht installiert"):
        screen.start(folder)


def test_screen_starts_retroarch_on_the_display_with_the_gaming_folder(tmp_path: Path) -> None:
    screen, calls = make_screen(tmp_path)
    folder = tmp_path / "home" / "GAMING"
    folder.mkdir(parents=True)
    status = screen.start(folder)
    assert status["running"] is True and status["cores"] == 1 and status["display"] is True
    arguments, options = calls[0]
    assert arguments[:2] == ["/usr/bin/retroarch", "--fullscreen"]
    assert arguments[2] == f"--config={folder / 'pi' / 'retroarch.cfg'}" and arguments[3] == f"--appendconfig={folder / 'pi' / 'storchcam.cfg'}"
    assert options["env"]["WAYLAND_DISPLAY"] == "wayland-0" and options["env"]["XDG_RUNTIME_DIR"] == str(tmp_path / "run")
    assert "DISPLAY" not in options["env"] and options["start_new_session"] is True
    overrides = (folder / "pi" / "storchcam.cfg").read_text()
    for line in (f'rgui_browser_directory = "{folder / "roms"}"', f'savefile_directory = "{folder / "saves"}"',
                 f'savestate_directory = "{folder / "states"}"', f'system_directory = "{folder / "bios"}"',
                 f'libretro_directory = "{tmp_path / "cores"}"', 'video_fullscreen = "true"', 'input_joypad_driver = "udev"'):
        assert line in overrides
    (folder / "pi" / "retroarch.cfg").write_text('menu_driver = "xmb"\n')
    with pytest.raises(ga.AgentError, match="läuft schon"):
        screen.start(folder)
    stopped = screen.stop()
    assert ("kill", 4242, ga.signal.SIGTERM) in calls and stopped["running"] is False and stopped["last_exit"]["code"] == -15
    screen.start(folder)
    assert (folder / "pi" / "retroarch.cfg").read_text() == 'menu_driver = "xmb"\n'  # eigene Einstellungen bleiben


def test_screen_reports_a_failed_start(tmp_path: Path) -> None:
    screen, _calls = make_screen(tmp_path)
    folder = tmp_path / "home" / "GAMING"
    folder.mkdir(parents=True)
    screen.start(folder)
    screen.log_path.write_text("[ERROR] Failed to initialize video driver.\n")
    screen.process.returncode = 1
    status = screen.status()
    assert status["running"] is False and status["last_exit"]["code"] == 1 and "video driver" in status["last_exit"]["error"]


def test_agent_starts_the_screen_only_with_a_gaming_folder(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir()
    screen, _calls = make_screen(tmp_path)
    agent = ga.GamingAgent(screen.config, FakeDocker(), ready=lambda _port: True, port_check=lambda _port: True, spawn=lambda work: work(), screen=screen)
    with pytest.raises(ga.AgentError, match="Speicherordner"):
        agent.screen_action("start")
    agent.install(str(tmp_path / "home"), 3001)
    assert agent.screen_action("start")["running"] is True and agent.status()["screen"]["running"] is True
    with pytest.raises(ga.AgentError, match="Unbekannte"):
        agent.screen_action("rm")
    assert agent.screen_action("stop")["running"] is False


def test_roms_are_listed_and_uploaded_safely(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, FakeDocker())
    agent.install(str(tmp_path / "home"), 3001)
    saved = agent.save_rom("Zelda (DE).sfc", 5, iter([b"ab", b"cde"]))
    assert saved == {"name": "Zelda (DE).sfc", "size": 5, "system": "SNES"}
    assert agent.roms()["files"] == [{"name": "Zelda (DE).sfc", "size": 5, "system": "SNES"}]
    for name, error in (("../x.nes", "Dateiname"), ("virus.exe", "Unbekannte"), ("Zelda (DE).sfc", "gibt es schon"), (".x.nes", "Dateiname")):
        with pytest.raises(ga.AgentError, match=error):
            agent.save_rom(name, 3, iter([b"abc"]))
    with pytest.raises(ga.AgentError, match="abgebrochen"):
        agent.save_rom("halb.gba", 10, iter([b"abc"]))
    assert not list((tmp_path / "home" / "roms").glob(".*"))  # keine halben Dateien


def test_agent_answers_over_its_unix_socket(tmp_path: Path) -> None:
    agent = make_agent(tmp_path, FakeDocker())
    path = str(tmp_path / "s.sock")
    server = ga.AgentServer(path, agent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("agent")
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.connect(path)
        connection.request("GET", "/status")
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["docker"]["ok"] is True
        assert oct(Path(path).stat().st_mode & 0o777) == "0o660"
    finally:
        server.shutdown()
        server.server_close()


def test_compose_contains_only_the_gaming_service() -> None:
    text = ga.render_compose(image=ga.IMAGE, folder=Path("/home/pi/GAMING"), port=3005, uid=1000, gid=1000, timezone="Europe/Berlin",
                             user="spieler", password='a"b', gpu=False)
    assert text.count("image:") == 1 and '"3005:3001"' in text and 'PASSWORD: "a\\"b"' in text and "devices" not in text
