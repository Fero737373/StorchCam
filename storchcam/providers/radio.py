"""Internet radio search through the free community database Radio Browser (radio-browser.info).

The API runs on several mirrors; the list comes from DNS (all.api.radio-browser.info) as the project
asks, with a short fixed list as fallback. Stations are returned in a compact form the menu shows
directly; playback itself happens in mpv (see player.py).
"""

from __future__ import annotations

import logging
import random
import socket
import threading
import time
import urllib.parse
from typing import Any

from .http_json import USER_AGENT, fetch_json

LOGGER = logging.getLogger(__name__)
DNS_NAME = "all.api.radio-browser.info"
FALLBACK_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]
SERVER_TTL = 3600
RESULT_LIMIT = 30
# Filter-Chips im Menü: Stichwort (tag) oder Land.
FILTERS = {
    "news": {"tag": "news"},
    "pop": {"tag": "pop"},
    "rock": {"tag": "rock"},
    "jazz": {"tag": "jazz"},
    "klassik": {"tag": "classical"},
    "de": {"countrycode": "DE"},
}

_servers: dict[str, Any] = {"names": [], "at": 0.0}
_lock = threading.Lock()


class RadioError(RuntimeError):
    pass


def _discover() -> list[str]:
    names: set[str] = set()
    try:
        for info in socket.getaddrinfo(DNS_NAME, 443, proto=socket.IPPROTO_TCP):
            try:
                names.add(socket.gethostbyaddr(str(info[4][0]))[0])
            except OSError:
                continue
    except OSError as exc:
        LOGGER.info("Radio-Browser-Server nicht per DNS gefunden: %s", exc)
    found = [name for name in names if name.endswith(".api.radio-browser.info")]
    random.shuffle(found)
    return found or list(FALLBACK_SERVERS)


def servers() -> list[str]:
    with _lock:
        if _servers["names"] and time.monotonic() - _servers["at"] < SERVER_TTL:
            return list(_servers["names"])
    names = _discover()
    with _lock:
        _servers.update({"names": names, "at": time.monotonic()})
    return list(names)


def _get(path: str, params: dict[str, str]) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    errors: list[str] = []
    for host in servers()[:3]:
        url = f"https://{host}{path}?{urllib.parse.urlencode(params)}"
        try:
            return fetch_json(url, headers=headers, timeout=8)
        except (OSError, ValueError) as exc:
            errors.append(f"{host}: {exc}")
            with _lock:
                if host in _servers["names"]:
                    _servers["names"].remove(host)
    raise RadioError("Radio-Datenbank nicht erreichbar" + (f" ({errors[-1][:120]})" if errors else ""))


def station(raw: Any) -> dict[str, Any] | None:
    """Sender in der Form, die Menü und Konfiguration verwenden; None für unbrauchbare Einträge."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url_resolved") or raw.get("url") or "").strip()
    uuid = str(raw.get("stationuuid") or "").strip()
    if not uuid or urllib.parse.urlparse(url).scheme not in {"http", "https"}:
        return None
    tags = [tag.strip() for tag in str(raw.get("tags") or "").split(",") if tag.strip()][:6]
    try:
        bitrate = max(0, min(10000, int(raw.get("bitrate") or 0)))
    except (TypeError, ValueError):
        bitrate = 0
    return {
        "uuid": uuid,
        "name": " ".join(str(raw.get("name") or "Radiosender").split())[:120],
        "url": url[:1024],
        "country": str(raw.get("country") or "")[:80],
        "state": str(raw.get("state") or "")[:80],
        "codec": str(raw.get("codec") or "")[:20],
        "bitrate": bitrate,
        "tags": [tag[:40] for tag in tags],
        "votes": int(raw.get("votes") or 0),
    }


def search(query: str = "", filter_key: str = "") -> list[dict[str, Any]]:
    """Sender nach Name (und passend als Stichwort) suchen; ohne Suchwort die beliebtesten."""
    base = {"hidebroken": "true", "order": "clickcount", "reverse": "true", "limit": str(RESULT_LIMIT)}
    base.update(FILTERS.get(filter_key, {}))
    clean = " ".join(query.split())[:80]
    requests = [dict(base, name=clean)] if clean else [dict(base, **({} if filter_key else {"countrycode": "DE"}))]
    if clean and "tag" not in base:
        requests.append(dict(base, tag=clean.lower(), limit="20"))
    found: dict[str, dict[str, Any]] = {}
    for params in requests:
        result = _get("/json/stations/search", params)
        for raw in result if isinstance(result, list) else []:
            item = station(raw)
            if item and item["uuid"] not in found:
                found[item["uuid"]] = item
    return sorted(found.values(), key=lambda item: -item["votes"])[:RESULT_LIMIT]


def count_click(uuid: str) -> None:
    """Radio Browser bittet darum, jedes Abspielen zu melden; das hält die Beliebtheit aktuell."""
    def send() -> None:
        try:
            _get(f"/json/url/{urllib.parse.quote(uuid)}", {})
        except RadioError as exc:
            LOGGER.debug("Klick für %s nicht gemeldet: %s", uuid, exc)

    threading.Thread(target=send, name="radio-click", daemon=True).start()
