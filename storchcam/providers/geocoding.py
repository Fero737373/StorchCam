"""Worldwide search for addresses, streets and places (no key required).

Primary source is Nominatim (OpenStreetMap), which also knows streets and house
numbers. If it fails, the Open-Meteo geocoding API (places only) is used.
Nominatim returns no timezone, so it is resolved after selection via Open-Meteo.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from . import http_json

LOGGER = logging.getLogger(__name__)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SEARCH_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Nominatim verlangt einen aussagekräftigen User-Agent (Nutzungsrichtlinie).
USER_AGENT = http_json.USER_AGENT


def _fetch_json(url: str, timeout: int = 10) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "de"}
    return http_json.fetch_json(url, headers=headers, timeout=timeout)


def _clean(query: str) -> str:
    return " ".join(query.split())[:120]


def _nominatim(clean: str, count: int) -> list[dict[str, Any]]:
    params = {"q": clean, "format": "jsonv2", "addressdetails": "1", "limit": str(count), "accept-language": "de"}
    data = _fetch_json(NOMINATIM_URL + "?" + urllib.parse.urlencode(params))
    if not isinstance(data, list):
        raise ValueError("Nominatim lieferte keine Liste")
    places: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            latitude = round(float(item["lat"]), 5)
            longitude = round(float(item["lon"]), 5)
        except (KeyError, TypeError, ValueError):
            continue
        address = item.get("address", {}) if isinstance(item.get("address"), dict) else {}
        city = next((str(address[key]) for key in ("city", "town", "village", "municipality", "hamlet", "suburb") if address.get(key)), "")
        road = str(address.get("road") or address.get("pedestrian") or address.get("footway") or "")
        if road:
            street = f"{road} {address.get('house_number', '')}".strip()
            locality = f"{address.get('postcode', '')} {city}".strip()
            name = ", ".join(part for part in (street, locality) if part)
        else:
            name = str(item.get("name") or city or str(item.get("display_name", "")).split(",")[0]).strip()
        if not name:
            continue
        region = ", ".join(dict.fromkeys(str(part) for part in (address.get("state"), address.get("country")) if part and part != name))
        places.append({"name": name[:120], "region": region, "latitude": latitude, "longitude": longitude, "timezone": ""})
    return places


def _open_meteo(clean: str, count: int) -> list[dict[str, Any]]:
    params = {"name": clean, "count": str(count), "language": "de", "format": "json"}
    data = _fetch_json(SEARCH_URL + "?" + urllib.parse.urlencode(params))
    places: list[dict[str, Any]] = []
    for item in (data.get("results", []) if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        try:
            latitude = round(float(item["latitude"]), 4)
            longitude = round(float(item["longitude"]), 4)
        except (KeyError, TypeError, ValueError):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        region = ", ".join(str(part) for part in (item.get("admin1"), item.get("country")) if part and part != name)
        places.append({
            "name": name[:120],
            "region": region,
            "latitude": latitude,
            "longitude": longitude,
            "timezone": str(item.get("timezone") or ""),
        })
    return places


def search_places(query: str, *, count: int = 8) -> list[dict[str, Any]]:
    clean = _clean(query)
    if len(clean) < 2:
        return []
    try:
        return _nominatim(clean, count)
    except Exception as exc:
        LOGGER.warning("Nominatim fehlgeschlagen (%s), nutze Open-Meteo-Ortssuche", exc)
        return _open_meteo(clean, count)


def timezone_for(latitude: float, longitude: float) -> str:
    params = {"latitude": str(latitude), "longitude": str(longitude), "timezone": "auto", "forecast_days": "1", "current": "temperature_2m"}
    data = _fetch_json(FORECAST_URL + "?" + urllib.parse.urlencode(params))
    timezone = str(data.get("timezone", "")) if isinstance(data, dict) else ""
    if not timezone:
        raise ValueError("Keine Zeitzone für diese Koordinaten")
    return timezone
