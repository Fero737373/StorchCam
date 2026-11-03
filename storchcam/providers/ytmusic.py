"""YouTube Music via a Google sign-in window on the Pi screen and the play history.

Google blocks the TV/device OAuth flow for YouTube Music (HTTP 400 since 2025), so the app
opens a plain Chromium window with the normal Google sign-in. Google refuses sign-ins in
browsers that are remote-controlled, so that window has no debugging access at all: the app
only watches the profile's cookie database for the session cookie's name, closes the window
and then reads the cookies with a headless Chromium through its DevTools pipe. The profile
is kept, so a later "sign in again" finds the Google account already logged in.

YouTube Music has no API for "currently playing". The newest entry of the play history
counts as the current song from the moment it appears until its length has passed;
pause and stop cannot be detected. Uses the unofficial ytmusicapi library.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic

from ..config_store import config_path
from ..kiosk import find_browser, session_type

# Gleicher Weg wie „Anmelden“ auf youtube.com, nur mit Rücksprung zu YouTube Music.
LOGIN_URL = (
    "https://accounts.google.com/ServiceLogin?service=youtube&uilel=3&passive=true&hl=de"
    "&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26app%3Ddesktop"
    "%26hl%3Dde%26next%3Dhttps%253A%252F%252Fmusic.youtube.com%252F"
)
MUSIC_URL = "https://music.youtube.com"
SESSION_COOKIE = "__Secure-3PAPISID"
LOGIN_TIMEOUT_SECONDS = 900
LOGIN_POLL_SECONDS = 3
# Google trägt den Song erst mit etwas Verzögerung in den Verlauf ein.
GRACE_SECONDS = 30
UNKNOWN_DURATION_SECONDS = 240
# So viele Verlaufseinträge werden verglichen; nach so vielen Abfragen ohne bekannten Song gilt der Verlauf als neu.
RECENT_ENTRIES = 5
STALE_LIMIT = 3
# Erst über fd 10 hinaus kopieren, damit dup2 auf 3 und 4 kein anderes Pipe-Ende überschreibt.
PIPE_LAUNCHER = (
    "import fcntl, os, sys\n"
    "high = [fcntl.fcntl(int(fd), fcntl.F_DUPFD, 10) for fd in sys.argv[1:3]]\n"
    "os.dup2(high[0], 3)\nos.dup2(high[1], 4)\n"
    "os.execv(sys.argv[3], sys.argv[3:])"
)
CLOSED_EARLY = "Das Anmeldefenster wurde geschlossen, bevor die Anmeldung fertig war"

_lock = threading.Lock()
_login: dict[str, Any] = {"status": "idle"}
_window: dict[str, Any] = {"browser": None}
_client: dict[str, Any] = {"key": None, "api": None}
_seen: dict[str, Any] = {"ids": None, "started": None, "track": None, "misses": 0}


class YTMusicError(RuntimeError):
    pass


def auth_path() -> Path:
    return config_path().parent / "ytmusic_auth.json"


def profile_dir() -> Path:
    return config_path().parent / "ytmusic-browser"


def connected() -> bool:
    return auth_path().is_file()


class DevToolsBrowser:
    """Unsichtbares Chromium mit --remote-debugging-pipe, nur zum Auslesen der Cookies nach der Anmeldung."""

    def __init__(self, arguments: list[str], env: dict[str, str], log_path: Path | None = None) -> None:
        log = _open_log(log_path)
        to_browser, self._write = os.pipe()
        self._read, from_browser = os.pipe()
        # Chromium erwartet die Pipe auf fd 3 (Befehle) und fd 4 (Antworten); ein kurzer Python-Start legt sie dorthin.
        command = [sys.executable, "-c", PIPE_LAUNCHER, str(to_browser), str(from_browser), *arguments, "--remote-debugging-pipe"]
        try:
            self.process = subprocess.Popen(
                command, env=env, pass_fds=(to_browser, from_browser), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            )
        except OSError:
            os.close(self._read)
            os.close(self._write)
            raise
        finally:
            os.close(to_browser)
            os.close(from_browser)
            if log is not subprocess.DEVNULL:
                log.close()
        self._buffer = b""
        self._next_id = 0

    def call(self, method: str, timeout: float = 10) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        try:
            os.write(self._write, json.dumps({"id": message_id, "method": method}).encode() + b"\0")
        except OSError as exc:
            raise YTMusicError("Der Browser ist unerwartet beendet") from exc
        deadline = time.monotonic() + timeout
        while True:
            while b"\0" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self._read], [], [], remaining)[0]:
                    raise YTMusicError(f"Browser antwortet nicht auf {method}")
                chunk = os.read(self._read, 1 << 16)
                if not chunk:
                    raise YTMusicError("Der Browser ist unerwartet beendet")
                self._buffer += chunk
            raw, self._buffer = self._buffer.split(b"\0", 1)
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise YTMusicError(f"Browser: {message['error'].get('message', message['error'])}")
                return dict(message.get("result") or {})

    def youtube_cookies(self) -> list[dict[str, Any]]:
        cookies = self.call("Storage.getCookies").get("cookies", [])
        return [cookie for cookie in cookies if str(cookie.get("domain", "")).lstrip(".").endswith("youtube.com")]

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.call("Browser.close", timeout=5)
                self.process.wait(timeout=10)
            except (YTMusicError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
        os.close(self._read)
        os.close(self._write)


def _open_log(path: Path | None) -> Any:
    if path is None:
        return subprocess.DEVNULL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("wb")
    except OSError:
        return subprocess.DEVNULL


def log_path(config: dict[str, Any]) -> Path:
    """Chromium-Ausgabe des Anmeldefensters, neben dem Log des Kiosk-Browsers."""
    kiosk_log = Path(os.path.expanduser(str(config.get("kiosk", {}).get("log_file", "~/.cache/StorchCam/chromium.log"))))
    return kiosk_log.with_name("ytmusic-login.log")


def _browser_arguments(config: dict[str, Any], *, headless: bool) -> list[str]:
    if os.name != "posix":
        raise YTMusicError("Die Anmeldung am Bildschirm gibt es nur unter Linux")
    resolved = find_browser(str(config.get("kiosk", {}).get("browser", "auto")))
    if not resolved:
        raise YTMusicError("Kein Chromium gefunden")
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
    # Nach einem Absturz bleibt die Sperre liegen, und Chromium würde das Profil verweigern.
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        (profile / name).unlink(missing_ok=True)
    arguments = [
        resolved[1],
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--lang=de",
        "--accept-lang=de-DE,de",
    ]
    if headless:
        return [*arguments, "--headless=new", "--mute-audio", "about:blank"]
    current = session_type()
    if current == "wayland":
        # Text-Input v3, damit die Bildschirmtastatur (squeekboard) bei Eingabefeldern aufgeht.
        arguments += ["--ozone-platform=wayland", "--enable-features=UseOzonePlatform", "--enable-wayland-ime", "--wayland-text-input-version=3"]
    elif current == "x11":
        arguments.append("--ozone-platform=x11")
    else:
        raise YTMusicError("Kein Bildschirm gefunden, auf dem sich das Anmeldefenster öffnen könnte")
    if not config.get("kiosk", {}).get("use_gpu", True):
        arguments += ["--disable-gpu", "--disable-gpu-compositing"]
    # Ein gewöhnliches Fenster mit Adressleiste: Man sieht, dass es wirklich accounts.google.com ist.
    return [*arguments, "--start-maximized", LOGIN_URL]


def auth_headers(cookies: list[dict[str, Any]], user_agent: str) -> dict[str, str]:
    values: dict[str, str] = {}
    # Allgemeine .youtube.com-Cookies zuerst, spezifischere (music.youtube.com) überschreiben sie.
    for cookie in sorted(cookies, key=lambda item: len(str(item.get("domain", "")).lstrip("."))):
        values[str(cookie["name"])] = str(cookie["value"])
    return {
        "cookie": "; ".join(f"{name}={value}" for name, value in values.items()),
        "x-goog-authuser": "0",
        # Nur die Kennung für ytmusicapi; den echten SAPISIDHASH berechnet es bei jeder Anfrage neu.
        "authorization": "SAPISIDHASH 0_0",
        "origin": MUSIC_URL,
        "x-origin": MUSIC_URL,
        "user-agent": user_agent,
        "accept": "*/*",
        "content-type": "application/json",
    }


def _store_auth(headers: dict[str, str]) -> None:
    target = auth_path()
    fd, temporary = tempfile.mkstemp(prefix=".ytmusic_auth.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(headers, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    with _lock:
        _client.update({"key": None, "api": None})


def _read_auth() -> dict[str, str]:
    try:
        data = json.loads(auth_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def api() -> YTMusic:
    try:
        key = auth_path().stat().st_mtime_ns
    except OSError as exc:
        raise YTMusicError("YouTube Music ist nicht verbunden") from exc
    with _lock:
        if _client["key"] != key or _client["api"] is None:
            _client.update({"key": key, "api": YTMusic(_read_auth())})
        return _client["api"]


def _history() -> list[dict[str, Any]]:
    try:
        return list(api().get_history())
    except YTMusicError:
        raise
    except Exception as exc:  # ytmusicapi wirft je nach Fehler sehr unterschiedliche Typen
        with _lock:
            _client.update({"key": None, "api": None})
        text = str(exc)
        if any(marker in text for marker in ("401", "403", "Sign in", "logged in", "__Secure-3PAPISID")):
            raise YTMusicError("YouTube Music: Anmeldung abgelaufen, bitte im Menü unter Musik neu anmelden") from exc
        raise YTMusicError(f"YouTube Music: {text[:140]}") from exc


def sized_cover(url: str) -> str:
    # Albumbilder von googleusercontent lassen sich über die Größenangabe größer anfordern.
    return re.sub(r"=w\d+-h\d+", "=w300-h300", url)


def _cover(thumbnails: Any) -> str:
    if not isinstance(thumbnails, list) or not thumbnails:
        return ""
    return sized_cover(str(thumbnails[-1].get("url", "")) if isinstance(thumbnails[-1], dict) else "")


def _track(item: dict[str, Any]) -> dict[str, Any]:
    artists = ", ".join(str(a.get("name", "")) for a in item.get("artists") or [] if isinstance(a, dict))
    album = item.get("album") or {}
    return {
        "source": "ytmusic",
        "title": str(item.get("title", "")),
        "artist": artists,
        "album": str(album.get("name", "")) if isinstance(album, dict) else "",
        "cover": _cover(item.get("thumbnails")),
        "duration_seconds": int(item.get("duration_seconds") or 0),
    }


def describe_latest() -> str:
    history = _history()
    if not history:
        return "Verlauf ist leer"
    track = _track(history[0])
    return f"{track['title']} · {track['artist']}"


def now_playing(now: float | None = None) -> dict[str, Any] | None:
    """Neuester Verlaufseintrag als laufender Song, solange seine Länge noch nicht verstrichen ist."""
    current = time.time() if now is None else now
    history = _history()
    if not history:
        return None
    ids = [item.get("videoId") for item in history[:RECENT_ENTRIES]]
    with _lock:
        known = _seen["ids"]
        if known is None or _seen["misses"] >= STALE_LIMIT:
            # Erster Blick (oder Verlauf grundlegend anders): wann der oberste Song lief, ist unbekannt.
            _seen.update({"ids": ids, "started": None, "track": _track(history[0]), "misses": 0})
        elif ids[0] != known[0]:
            if known[0] in ids[1:]:
                # Ein neuer Song ist über den bisher neuesten gerutscht.
                _seen.update({"ids": ids, "started": current, "track": _track(history[0]), "misses": 0})
            elif known[0] not in ids:
                # Google liefert manchmal kurz einen älteren Stand des Verlaufs; den ignorieren.
                _seen["misses"] += 1
        else:
            _seen["misses"] = 0
        started, track = _seen["started"], _seen["track"]
    if started is None or track is None:
        return None
    duration = track["duration_seconds"] or UNKNOWN_DURATION_SECONDS
    if current > started + duration + GRACE_SECONDS:
        return None
    return {
        **{name: value for name, value in track.items() if name != "duration_seconds"},
        "progress_ms": None,
        "duration_ms": None,
        "fetched_at": int(current * 1000),
    }


class LoginWindow:
    """Das sichtbare Anmeldefenster: ein gewöhnliches Chromium ohne jeden Fernsteuerungszugang."""

    def __init__(self, arguments: list[str], env: dict[str, str], log: Path | None) -> None:
        handle = _open_log(log)
        try:
            self.process = subprocess.Popen(
                arguments, env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
            )
        finally:
            if handle is not subprocess.DEVNULL:
                handle.close()

    def alive(self) -> bool:
        return self.process.poll() is None

    def close(self) -> None:
        # SIGTERM beendet Chromium geordnet, dabei schreibt es die Cookies ins Profil.
        if self.alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def profile_has_session() -> bool:
    """Steht das Sitzungscookie schon in der Cookie-Datenbank? Liest nur Namen, die Werte sind verschlüsselt."""
    database = profile_dir() / "Default" / "Cookies"
    if not database.is_file():
        return False
    # Chromium hält die Datenbank exklusiv offen; deshalb eine Kopie lesen.
    with tempfile.TemporaryDirectory(prefix="ytmusic-cookies.") as folder:
        copy = Path(folder) / "Cookies"
        try:
            shutil.copyfile(database, copy)
            with sqlite3.connect(f"file:{copy}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT 1 FROM cookies WHERE name = ? AND host_key LIKE '%youtube.com' LIMIT 1", (SESSION_COOKIE,)
                ).fetchone()
        except (OSError, sqlite3.Error):
            return False
    return row is not None


def _harvest_cookies(config: dict[str, Any]) -> None:
    """Liest nach dem Schließen des Fensters die Cookies mit einem unsichtbaren Chromium aus."""
    browser = DevToolsBrowser(_browser_arguments(config, headless=True), os.environ.copy(), log_path(config))
    try:
        user_agent = str(browser.call("Browser.getVersion").get("userAgent", "")).replace("HeadlessChrome", "Chrome")
        cookies = browser.youtube_cookies()
    finally:
        browser.close()
    if not any(cookie.get("name") == SESSION_COOKIE for cookie in cookies):
        raise YTMusicError("Die Anmeldung wurde im Browser nicht gespeichert, bitte erneut versuchen")
    _store_auth(auth_headers(cookies, user_agent))


def start_login(config: dict[str, Any]) -> dict[str, Any]:
    """Öffnet die Google-Anmeldung als eigenes Fenster; ein Hintergrund-Thread wartet auf die Sitzung."""
    with _lock:
        if _login.get("status") == "pending" and _window["browser"] and _window["browser"].alive():
            return _status_locked()
    # Das Profil bleibt erhalten: Ist das Konto dort noch angemeldet, schließt sich das Fenster gleich wieder.
    try:
        window = LoginWindow(_browser_arguments(config, headless=False), os.environ.copy(), log_path(config))
    except OSError as exc:
        raise YTMusicError(f"Das Anmeldefenster ließ sich nicht öffnen: {exc}") from exc
    with _lock:
        previous = _window["browser"]
        _window["browser"] = window
        _login.clear()
        _login["status"] = "pending"
    if previous:
        previous.close()
    threading.Thread(target=_wait_for_login, args=(config, window), name="ytmusic-login", daemon=True).start()
    return login_status()


def _finish(window: LoginWindow, message: dict[str, Any]) -> None:
    with _lock:
        if _window["browser"] is window:
            _window["browser"] = None
            _login.update(message)


def _wait_for_login(config: dict[str, Any], window: LoginWindow) -> None:
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(LOGIN_POLL_SECONDS)
        with _lock:
            if _window["browser"] is not window:
                return
        # Chromium schreibt Cookies gesammelt alle paar Sekunden; wer das Fenster selbst schließt, ist auch fertig.
        closed = not window.alive()
        if not closed and not profile_has_session():
            continue
        if not closed:
            time.sleep(4)  # YouTube Music nach dem Rücksprung fertig laden lassen
            window.close()
        if closed and not profile_has_session():
            _finish(window, {"status": "error", "error": CLOSED_EARLY})
            return
        try:
            _harvest_cookies(config)
        except (OSError, YTMusicError) as exc:
            _finish(window, {"status": "error", "error": f"Anmeldung nicht übernommen: {exc}"})
            return
        with _lock:
            _seen.update({"ids": None, "started": None, "track": None, "misses": 0})
        try:
            latest = describe_latest()
        except YTMusicError as exc:
            _finish(window, {"status": "error", "error": f"Angemeldet, aber der Verlauf ist nicht lesbar. {exc}"})
            return
        _finish(window, {"status": "connected", "latest": latest})
        return
    window.close()
    _finish(window, {"status": "error", "error": "Zeit für die Anmeldung abgelaufen, bitte erneut versuchen"})


def cancel_login() -> None:
    with _lock:
        window = _window["browser"]
        _window["browser"] = None
        if _login.get("status") == "pending":
            _login.clear()
            _login["status"] = "idle"
    if window:
        window.close()


def disconnect() -> None:
    cancel_login()
    with _lock:
        auth_path().unlink(missing_ok=True)
        # Überbleibsel der früheren Geräteanmeldung, die Google inzwischen sperrt.
        (config_path().parent / "ytmusic_oauth.json").unlink(missing_ok=True)
        _client.update({"key": None, "api": None})
        _seen.update({"ids": None, "started": None, "track": None, "misses": 0})
        _login.clear()
        _login["status"] = "idle"
    shutil.rmtree(profile_dir(), ignore_errors=True)


def _status_locked() -> dict[str, Any]:
    return {**_login, "connected": connected()}


def login_status() -> dict[str, Any]:
    with _lock:
        return _status_locked()
