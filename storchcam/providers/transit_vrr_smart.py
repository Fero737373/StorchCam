"""VRR departure provider with complete configuration-aware caching."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from .http_json import USER_AGENT, fetch_json_object

LOGGER = logging.getLogger(__name__)
SEARCH_URL = "https://haltestellenmonitor.vrr.de/backend/api/stations/search"
TABLE_URL = "https://haltestellenmonitor.vrr.de/backend/api/stations/table"
_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _fetch_json(url: str, data: dict[str, Any] | None = None, timeout: int = 12) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    return fetch_json_object(url, "VRR", headers=headers, timeout=timeout, form=data)


def search_station(query: str) -> list[dict[str, str]]:
    clean = query.strip()
    if len(clean) < 2:
        return []
    data = _fetch_json(SEARCH_URL + "?query=" + urllib.parse.quote(clean))
    results: list[dict[str, str]] = []
    for item in data.get("suggestions", [])[:12]:
        if not isinstance(item, dict):
            continue
        station_id = str(item.get("data", "")).strip()
        station_name = str(item.get("value", "")).strip()
        if station_id and station_name:
            results.append({"station_id": station_id, "station_name": station_name})
    return results


def cache_key(config: dict[str, Any]) -> str:
    transit = config.get("transit", {})
    relevant = {
        "provider": transit.get("provider"),
        "refresh_seconds": transit.get("refresh_seconds"),
        "default_max_rows": transit.get("default_max_rows"),
        "target_len": transit.get("target_len"),
        "stops": transit.get("stops", []),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _matches_line(line: str, filters: list[str], nightbus_only: bool) -> bool:
    candidate = line.upper().strip()
    if nightbus_only and not candidate.startswith("N"):
        return False
    wanted = {item.upper().strip() for item in filters if item.strip()}
    return not wanted or candidate in wanted or ("N" in wanted and candidate.startswith("N"))


def _minutes(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _format_minutes(value: Any) -> str:
    minutes = _minutes(value)
    if minutes is None:
        return "–"
    if minutes <= 0:
        return "jetzt"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60}h {minutes % 60:02d}"


# VRR-Verkehrsmittel: 0–2 Bahn, 3 U-Bahn, 4 Straßen-/Stadtbahn, 5 Bus, 6 Schwebebahn.
TRAM_TYPES = {3, 4, 6}
TRAIN_TYPES = {0, 1, 2}


def vehicle(line: str, kind: Any) -> str:
    """Art der Linie für Farbe und Form des Linienschilds: bus, tram, night oder train."""
    try:
        number = int(kind)
    except (TypeError, ValueError):
        number = -1
    if line.upper().startswith("N") and number not in TRAM_TYPES | TRAIN_TYPES:
        return "night"
    if number in TRAM_TYPES:
        return "tram"
    if number in TRAIN_TYPES:
        return "train"
    return "bus"


def _epoch(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 1_000_000_000 else None


def short_name(name: str, city: str = "") -> str:
    """Nur den Haltestellennamen ohne Ort/Stadtteil: „Altstadt, Lindenstraße“ → „Lindenstraße“.

    ``city`` ist der Ort der eigenen Haltestelle; Fahrtziele im selben Ort verlieren ihn als Vorsilbe
    („Musterstadt Marktplatz“ → „Marktplatz“).
    """
    clean = " ".join(str(name or "").split())
    if "," in clean:
        clean = clean.rsplit(",", 1)[1].strip()
    if city and clean.casefold().startswith(f"{city.casefold()} ") and len(clean) > len(city) + 1:
        clean = clean[len(city):].strip()
    return clean


def city_of(name: str) -> str:
    """Ort aus einem VRR-Haltestellennamen „Ort, Haltestelle“ (sonst leer)."""
    clean = " ".join(str(name or "").split())
    return clean.split(",", 1)[0].strip() if "," in clean else ""


def _board(stop: dict[str, Any], default_max: int, target_len: int) -> dict[str, Any]:
    station_id = str(stop.get("station_id", "")).strip()
    station_name = str(stop.get("station_name", "")).strip()
    if not station_id:
        matches = search_station(station_name)
        if not matches:
            raise ValueError(f"Haltestelle nicht gefunden: {station_name}")
        station_id = matches[0]["station_id"]
        station_name = matches[0]["station_name"]
    payload = {
        "table[departure][stationId]": station_id,
        "table[departure][stationName]": station_name,
        "table[departure][platformVisibility]": "1",
        "table[departure][transport]": "0,1,2,3,4,5,15,6",
        "table[departure][useAllLines]": "1",
        "table[departure][linesFilter]": "",
        "table[departure][optimizedForStation]": "0",
        "table[departure][rowCount]": "40",
        "table[departure][refreshInterval]": "60",
        "table[departure][distance]": "0",
        "table[departure][marquee]": "-1",
        "table[sortBy]": "0",
    }
    data = _fetch_json(TABLE_URL, payload)
    city = city_of(str(data.get("stationName") or station_name))
    filters = [str(item) for item in stop.get("line_filter", [])]
    nightbus = bool(stop.get("nightbus_only", False))
    limit = int(stop.get("max_rows") or default_max)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    lines: dict[str, str] = {}
    for departure in data.get("departureData", []):
        if not isinstance(departure, dict):
            continue
        line = str(departure.get("lineNumber") or departure.get("name") or "–").strip()
        lines.setdefault(line, vehicle(line, departure.get("type")))
        if not _matches_line(line, filters, nightbus) or len(rows) >= limit:
            continue
        target = short_name(str(departure.get("direction") or departure.get("route") or ""), city) or "–"
        key = (line.casefold(), target.casefold())
        if key in seen:
            continue
        seen.add(key)
        if len(target) > target_len:
            target = target[: target_len - 1].rstrip() + "…"
        countdown = departure.get("countdown")
        delay = _minutes(departure.get("delay"))
        rows.append({
            "line": line,
            "target": target,
            "mins": _format_minutes(countdown),
            "minutes": _minutes(countdown),
            "type": vehicle(line, departure.get("type")),
            # Verspätung in Minuten (nur bei Echtzeitdaten), dazu Plan- und erwartete Abfahrt als Unix-Zeit.
            "delay": delay if delay and delay > 0 else 0,
            "realtime": bool(departure.get("isRealTime")),
            "planned": _epoch(departure.get("orgFullTime")),
            "at": _epoch(departure.get("fullTime")),
            "platform": str(departure.get("platform") or "").strip()[:12],
        })
    # Ohne eigenen Anzeigenamen (oder mit dem automatisch übernommenen Suchtreffer) zeigt die Tafel
    # den Haltestellennamen aus der VRR-Antwort; dort sind Ort und Haltestelle per Komma getrennt.
    custom = str(stop.get("title") or "").strip()
    if custom and custom != station_name:
        title = custom
    else:
        title = short_name(str(data.get("stationName") or station_name))
    title = title or "Haltestelle"
    return {
        "title": title,
        "station_id": station_id,
        "station_name": station_name,
        "rows": rows,
        "hide_if_empty": bool(stop.get("hide_if_empty", True)),
        "walk_minutes": int(stop.get("walk_minutes") or 0),
        # Alle Linien dieser Haltestelle (auch gefilterte), für die Filterauswahl im Menü.
        "lines": [{"line": name, "type": kind} for name, kind in sorted(lines.items(), key=lambda item: (len(item[0]), item[0]))][:24],
        "ok": True,
    }


def get_boards(config: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
    transit = config.get("transit", {})
    key = cache_key(config)
    refresh = int(transit.get("refresh_seconds", 60))
    now = time.monotonic()
    with _cache_lock:
        cached = copy.deepcopy(_cache.get(key))
    if cached and not force and now - float(cached.get("cached_at", 0)) < refresh:
        return cached["boards"]

    boards: list[dict[str, Any]] = []
    for stop in transit.get("stops", []):
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            board = _board(stop, int(transit.get("default_max_rows", 2)), int(transit.get("target_len", 16)))
        except Exception as exc:
            LOGGER.warning("VRR-Abfrage für %s fehlgeschlagen: %s", stop.get("station_name"), exc)
            board = {
                "title": str(stop.get("title") or short_name(str(stop.get("station_name", ""))) or "Haltestelle"),
                "station_id": str(stop.get("station_id", "")),
                "station_name": str(stop.get("station_name", "")),
                "rows": [],
                "hide_if_empty": bool(stop.get("hide_if_empty", True)),
                "walk_minutes": int(stop.get("walk_minutes") or 0),
                "lines": [],
                "ok": False,
                "error": str(exc)[:160],
            }
        board["checked_at"] = checked_at
        if board["rows"] or not board["hide_if_empty"] or not board["ok"]:
            boards.append(board)
    with _cache_lock:
        _cache[key] = {"cached_at": now, "boards": copy.deepcopy(boards)}
    return boards


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
