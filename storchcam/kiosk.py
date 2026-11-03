"""Cross-platform display preparation and supervised Chromium-family kiosk."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, BinaryIO

LOGGER = logging.getLogger(__name__)
SUPPORTED_NAMES = {
    "chromium": ("chromium", "chromium-browser"),
    "chrome": ("google-chrome-stable", "google-chrome", "chrome"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
}
PROFILE_ROTATIONS = {"raspberry_pi_dsi_portrait": "right", "raspberry_pi_dsi_landscape": "normal"}
NO_ROTATION = {"", "none"}


def session_type(env: dict[str, str] | None = None) -> str:
    values = env or os.environ
    explicit = values.get("XDG_SESSION_TYPE", "").lower()
    if explicit in {"x11", "wayland"}:
        return explicit
    if values.get("WAYLAND_DISPLAY"):
        return "wayland"
    if values.get("DISPLAY"):
        return "x11"
    return "unknown"


def _windows_candidates() -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for root in filter(None, (
        os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")
    )):
        result.extend([
            ("edge", str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")),
            ("chrome", str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")),
            ("chromium", str(Path(root) / "Chromium" / "Application" / "chrome.exe")),
        ])
    return result


def browser_candidates(configured: str) -> list[tuple[str, str]]:
    requested = configured.strip()
    candidates: list[tuple[str, str]] = []
    if requested.lower() not in {"", "auto", "chromium", "chrome", "edge"}:
        candidates.append(("custom", os.path.expandvars(os.path.expanduser(requested))))
    kinds = [requested.lower()] if requested.lower() in SUPPORTED_NAMES else ["chromium", "chrome", "edge"]
    if os.name == "nt":
        for kind, path in _windows_candidates():
            if kind in kinds:
                candidates.append((kind, path))
    for kind in kinds:
        candidates.extend((kind, name) for name in SUPPORTED_NAMES[kind])
    return candidates


def find_browser(configured: str = "auto") -> tuple[str, str] | None:
    for kind, candidate in browser_candidates(configured):
        if Path(candidate).is_absolute():
            if Path(candidate).is_file():
                return kind if kind != "custom" else "chromium", candidate
        else:
            found = shutil.which(candidate)
            if found:
                return kind, found
    return None


def browser_arguments(kind: str, executable: str, url: str, config: dict[str, Any], env: dict[str, str]) -> list[str]:
    if kind not in SUPPORTED_NAMES:
        raise ValueError(f"Nicht unterstützter Browsertyp: {kind}")
    kiosk = config.get("kiosk", {})
    raw_profile = str(kiosk.get("profile_dir", "")).strip()
    if raw_profile:
        profile = Path(os.path.expandvars(os.path.expanduser(raw_profile)))
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        profile = Path(root) / "StorchCam" / "browser-profile"
    else:
        profile = Path("/tmp/storchcam-browser-profile")
    profile.mkdir(parents=True, exist_ok=True)
    arguments = [
        executable,
        "--kiosk",
        "--no-first-run",
        "--no-default-browser-check",
        "--noerrdialogs",
        "--disable-session-crashed-bubble",
        "--autoplay-policy=no-user-gesture-required",
        # Das Video soll weiterlaufen, auch wenn ein anderes Fenster (z. B. die Google-Anmeldung) darüber liegt.
        "--disable-background-media-suspend",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=TranslateUI,MediaRouter",
        f"--user-data-dir={profile}",
    ]
    current_session = session_type(env)
    if os.name != "nt" and current_session == "wayland":
        arguments.extend(["--ozone-platform=wayland", "--enable-features=UseOzonePlatform"])
    elif os.name != "nt" and current_session == "x11":
        arguments.append("--ozone-platform=x11")
    if not kiosk.get("use_gpu", True):
        arguments.extend(["--disable-gpu", "--disable-gpu-compositing"])
    arguments.extend(str(flag) for flag in kiosk.get("extra_flags", []))
    arguments.append(url)
    return arguments


def _run_checked(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str] | None:
    if not shutil.which(command[0]):
        return None
    try:
        return subprocess.run(command, env=env, text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("Displaykommando fehlgeschlagen (%s): %s", command[0], exc)
        return None


def _x11_outputs(env: dict[str, str]) -> list[str]:
    result = _run_checked(["xrandr", "--query"], env)
    if not result or result.returncode:
        return []
    return [line.split()[0] for line in result.stdout.splitlines() if " connected" in line]


def _apply_rotation(env: dict[str, str], profile: str, output: str, rotation: str) -> None:
    outputs = _x11_outputs(env)
    if output == "auto" and profile.startswith("raspberry_pi_dsi"):
        dsi_outputs = [item for item in outputs if item.upper().startswith("DSI")]
        output = dsi_outputs[0] if len(dsi_outputs) == 1 else ""
    if output and output in outputs and rotation not in NO_ROTATION:
        result = _run_checked(["xrandr", "--output", output, "--rotate", rotation], env)
        if result and result.returncode:
            LOGGER.warning("Rotation für %s konnte nicht gesetzt werden: %s", output, result.stderr.strip())
    elif rotation not in NO_ROTATION:
        LOGGER.warning("Konfiguriertes Display %s wurde nicht eindeutig gefunden; keine Rotation angewendet", output)


def _apply_touch_matrix(env: dict[str, str], touch_device: str, matrix: str) -> None:
    if not matrix:
        return
    if not touch_device:
        LOGGER.warning("Touchmatrix ist gesetzt, aber touch_device fehlt; keine Eingabeänderung angewendet")
        return
    if not shutil.which("xinput"):
        return
    result = _run_checked(["xinput", "list", "--name-only"], env)
    names = result.stdout.splitlines() if result and not result.returncode else []
    exact = [name for name in names if name.strip() == touch_device]
    if len(exact) == 1:
        _run_checked(["xinput", "set-prop", touch_device, "Coordinate Transformation Matrix", *matrix.split()], env)
    else:
        LOGGER.warning("Touchgerät %s wurde nicht eindeutig gefunden", touch_device)


def _disable_screensaver(env: dict[str, str]) -> None:
    for command in (["xset", "s", "off"], ["xset", "-dpms"], ["xset", "s", "noblank"]):
        _run_checked(command, env)


def prepare_display(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    screen = config.get("screen", {})
    if os.name == "nt" or session_type(env) != "x11":
        if screen.get("rotation") not in {"", "none", "normal"}:
            LOGGER.warning("Displayrotation wird außerhalb von X11 nicht automatisch angewendet")
        return env

    profile = str(screen.get("hardware_profile", "generic"))
    if profile == "generic":
        return env
    rotation = PROFILE_ROTATIONS.get(profile, str(screen.get("rotation", "none")))
    _apply_rotation(env, profile, str(screen.get("output", "auto")), rotation)
    _apply_touch_matrix(env, str(screen.get("touch_device", "")).strip(), str(screen.get("touch_matrix", "")).strip())
    if config.get("kiosk", {}).get("disable_screensaver", True):
        _disable_screensaver(env)
    return env


def _rotate_file(path: Path, maximum: int, backups: int) -> None:
    if not path.exists() or path.stat().st_size < maximum:
        return
    path.with_name(f"{path.name}.{backups}").unlink(missing_ok=True)
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _clear_singletons(profile: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
        candidate = profile / name
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Browser-Singleton %s konnte nicht entfernt werden: %s", candidate, exc)


class BrowserManager:
    def __init__(self, config: dict[str, Any], url: str) -> None:
        self.config = config
        self.url = url
        self.process: subprocess.Popen[bytes] | None = None
        self._log_handle: BinaryIO | None = None
        self.started_at = 0.0
        self.next_start = 0.0
        self.delay = float(config["kiosk"]["browser_restart_seconds"])
        self.failures = 0
        self.exhausted = False

    def start(self) -> bool:
        if self.exhausted or not self.config.get("kiosk", {}).get("enabled", True):
            return False
        resolved = find_browser(str(self.config["kiosk"].get("browser", "auto")))
        if not resolved:
            self.exhausted = True
            LOGGER.error("Kein unterstützter Chromium-, Chrome- oder Edge-Browser gefunden")
            return False
        kind, executable = resolved
        env = prepare_display(self.config)
        arguments = browser_arguments(kind, executable, self.url, self.config, env)
        profile_arg = next(arg for arg in arguments if arg.startswith("--user-data-dir="))
        _clear_singletons(Path(profile_arg.split("=", 1)[1]))
        log_path = Path(os.path.expanduser(str(self.config["kiosk"]["log_file"])))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_file(log_path, int(self.config["kiosk"]["log_max_bytes"]), int(self.config["kiosk"]["log_backups"]))
        self._log_handle = log_path.open("ab", buffering=0)
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            self.process = subprocess.Popen(
                arguments,
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
        except OSError as exc:
            LOGGER.error("Browserstart fehlgeschlagen: %s", exc)
            self._close_log()
            self._schedule_failure(time.monotonic())
            return False
        self.started_at = time.monotonic()
        LOGGER.info("Kiosk-Browser gestartet: %s", " ".join(shlex.quote(item) for item in arguments))
        return True

    def tick(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self.exhausted:
            return
        if self.process and self.process.poll() is None:
            stable = float(self.config["kiosk"]["browser_stable_seconds"])
            if current - self.started_at >= stable:
                self.delay = float(self.config["kiosk"]["browser_restart_seconds"])
                self.failures = 0
            return
        if self.process:
            lifetime = current - self.started_at
            LOGGER.warning("Kiosk-Browser endete mit Code %s nach %.1f Sekunden", self.process.poll(), lifetime)
            self.process = None
            self._close_log()
            self._schedule_failure(current)
        if current >= self.next_start and not self.exhausted:
            self.start()

    def _schedule_failure(self, now: float) -> None:
        self.failures += 1
        maximum_failures = int(self.config["kiosk"]["browser_max_failures"])
        if self.failures >= maximum_failures:
            self.exhausted = True
            LOGGER.error("Browser-Watchdog nach %s aufeinanderfolgenden Fehlern angehalten", self.failures)
            return
        self.next_start = now + self.delay
        self.delay = min(self.delay * 2, float(self.config["kiosk"]["browser_restart_max_seconds"]))

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
        self._close_log()

    def _close_log(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
