"""Open-Meteo weather provider with timezone-aware future forecast selection."""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .http_json import USER_AGENT, fetch_json_object

LOGGER = logging.getLogger(__name__)
CODES = {
    0: "klar", 1: "meist klar", 2: "teils bewölkt", 3: "bewölkt", 45: "Nebel", 48: "Reifnebel",
    51: "leichter Niesel", 53: "Niesel", 55: "starker Niesel", 61: "leichter Regen", 63: "Regen",
    65: "starker Regen", 71: "leichter Schnee", 73: "Schnee", 75: "starker Schnee", 80: "Schauer",
    81: "Schauer", 82: "starke Schauer", 95: "Gewitter",
}
_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    return fetch_json_object(url, "Open-Meteo", headers=headers, timeout=timeout)


def _clean_label(value: Any) -> str:
    label = re.sub(r"\s+", " ", str(value or "Berlin")).strip()
    return (label.split(",", 1)[0] or "Berlin")[:64]


def cache_key(config: dict[str, Any]) -> str:
    weather = config.get("weather", {})
    location = config.get("location", {})
    relevant = {
        "provider": weather.get("provider"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "timezone": config.get("app", {}).get("timezone"),
        "forecast_hours": weather.get("forecast_hours"),
        "rain_mm_threshold": weather.get("rain_mm_threshold"),
        "rain_probability_threshold": weather.get("rain_probability_threshold"),
    }
    return json.dumps(relevant, sort_keys=True, separators=(",", ":"))


def future_forecast(
    hourly: dict[str, Any], timezone_name: str, forecast_hours: int, now: datetime | None = None
) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now else datetime.now(zone)
    times = hourly.get("time", [])
    rain = hourly.get("precipitation", [])
    probability = hourly.get("precipitation_probability", [])
    codes = hourly.get("weather_code", [])
    selected: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times):
        try:
            stamp = datetime.fromisoformat(str(raw_time))
            stamp = stamp.replace(tzinfo=zone) if stamp.tzinfo is None else stamp.astimezone(zone)
        except (TypeError, ValueError):
            continue
        if stamp < current:
            continue
        selected.append({
            "time": stamp,
            "rain_mm": float(rain[index] or 0) if index < len(rain) else 0.0,
            "probability": int(probability[index] or 0) if index < len(probability) else 0,
            "code": int(codes[index] or 0) if index < len(codes) else 0,
        })
        if len(selected) >= forecast_hours:
            break
    return selected


def _number(values: list[Any], index: int) -> float | None:
    try:
        value = values[index]
    except (IndexError, TypeError):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def hourly_series(hourly: dict[str, Any], timezone_name: str, hours: int = 25, now: datetime | None = None) -> list[dict[str, Any]]:
    """Stunden ab der laufenden Stunde für den Wetterstreifen und die 24-Stunden-Ansicht."""
    zone = ZoneInfo(timezone_name)
    current = (now.astimezone(zone) if now else datetime.now(zone)).replace(minute=0, second=0, microsecond=0)
    series: list[dict[str, Any]] = []
    for index, raw_time in enumerate(hourly.get("time", [])):
        try:
            stamp = datetime.fromisoformat(str(raw_time))
            stamp = stamp.replace(tzinfo=zone) if stamp.tzinfo is None else stamp.astimezone(zone)
        except (TypeError, ValueError):
            continue
        if stamp < current:
            continue
        temp = _number(hourly.get("temperature_2m", []), index)
        series.append({
            "time": stamp.isoformat(),
            "hour": stamp.hour,
            "temp": round(temp) if temp is not None else None,
            "probability": int(_number(hourly.get("precipitation_probability", []), index) or 0),
            "rain_mm": round(_number(hourly.get("precipitation", []), index) or 0, 1),
            "wind": round(_number(hourly.get("wind_speed_10m", []), index) or 0),
            "code": int(_number(hourly.get("weather_code", []), index) or 0),
            "is_day": bool(_number(hourly.get("is_day", []), index) if hourly.get("is_day") else 7 <= stamp.hour < 19),
        })
        if len(series) >= hours:
            break
    return series


def sun_times(daily: dict[str, Any] | None, timezone_name: str) -> list[dict[str, str]]:
    """Sonnenauf- und -untergang der nächsten Tage als ISO-Zeiten in der Ortszeit."""
    if not isinstance(daily, dict):
        return []
    zone = ZoneInfo(timezone_name)
    result: list[dict[str, str]] = []
    for rise, sunset in zip(daily.get("sunrise", []) or [], daily.get("sunset", []) or [], strict=False):
        try:
            result.append({
                "rise": datetime.fromisoformat(str(rise)).replace(tzinfo=zone).isoformat(),
                "set": datetime.fromisoformat(str(sunset)).replace(tzinfo=zone).isoformat(),
            })
        except (TypeError, ValueError):
            continue
    return result


def is_rainy(item: dict[str, Any], cfg: dict[str, Any]) -> bool:
    mm_threshold = float(cfg.get("rain_mm_threshold", 0.1))
    probability_threshold = int(cfg.get("rain_probability_threshold", 45))
    return bool(item["rain_mm"] >= mm_threshold or item["probability"] >= probability_threshold)


def _weather_code(current: dict[str, Any]) -> int:
    return int(current.get("weather_code", 0) or 0)


def _condition(current: dict[str, Any]) -> str:
    return CODES.get(_weather_code(current), "aktuell")


def _summary(label: str, current: dict[str, Any], forecast: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    temperature = round(float(current.get("temperature_2m", 0)))
    condition = _condition(current)
    prefix = f"{_clean_label(label)} · {condition} · {temperature} °C"
    rainy = next((item for item in forecast if is_rainy(item, cfg)), None)
    if rainy:
        return f"{prefix} · Regen ab {rainy['time'].strftime('%H:%M')} ({rainy['probability']} %)"
    return f"{prefix} · kein Regen in den nächsten {len(forecast)} h"


def get_weather(config: dict[str, Any], *, force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    weather = config.get("weather", {})
    location = config.get("location", {})
    timezone_name = str(config.get("app", {}).get("timezone", "Europe/Berlin"))
    label = _clean_label(location.get("label"))
    key = cache_key(config)
    refresh = int(weather.get("refresh_seconds", 300))
    monotonic_now = time.monotonic()
    with _cache_lock:
        cached = copy.deepcopy(_cache.get(key))
    if cached and not force and monotonic_now - float(cached.get("_cached_at", 0)) < refresh:
        cached.pop("_cached_at", None)
        return cached

    params = {
        "latitude": str(location.get("latitude", 52.5200)),
        "longitude": str(location.get("longitude", 13.4050)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code,wind_speed_10m,is_day",
        "daily": "sunrise,sunset",
        "forecast_days": "3",
        "timezone": timezone_name,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    try:
        data = _fetch_json(url)
        current_data = data.get("current")
        hourly = data.get("hourly")
        if not isinstance(current_data, dict) or not isinstance(hourly, dict):
            raise ValueError("Open-Meteo-Antwort enthält keine gültigen Wetterdaten")
        forecast = future_forecast(hourly, timezone_name, int(weather.get("forecast_hours", 8)), now)
        if not forecast:
            raise ValueError("Keine aktuellen oder zukünftigen stündlichen Prognosen verfügbar")
        result = {
            "ok": True,
            "provider": "openmeteo",
            "label": label,
            "text": _summary(str(location.get("label", "Berlin")), current_data, forecast, weather),
            "condition": _condition(current_data),
            "code": _weather_code(current_data),
            "is_day": bool(current_data.get("is_day", 1)),
            "temp": round(float(current_data.get("temperature_2m", 0))),
            "feels": round(float(current_data.get("apparent_temperature", 0))),
            "wind": round(float(current_data.get("wind_speed_10m", 0))),
            "hum": round(float(current_data.get("relative_humidity_2m", 0))),
            "rain": round(float(current_data.get("precipitation", 0)), 1),
            "forecast": [
                {**item, "time": item["time"].isoformat(), "rainy": is_rainy(item, weather)} for item in forecast
            ],
            "hourly": hourly_series(hourly, timezone_name, 25, now),
            "sun": sun_times(data.get("daily"), timezone_name),
            "rain_threshold": int(weather.get("rain_probability_threshold", 45)),
            "updated_at": datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds"),
            "stale": False,
        }
        with _cache_lock:
            _cache[key] = {**copy.deepcopy(result), "_cached_at": monotonic_now}
        return result
    except Exception as exc:
        LOGGER.warning("Wetterprovider fehlgeschlagen: %s", exc)
        if cached and cached.get("ok"):
            cached.pop("_cached_at", None)
            cached.update({"stale": True, "error": str(exc)[:160]})
            return cached
        return {
            "ok": False,
            "provider": "openmeteo",
            "label": label,
            "text": f"{label} · Wetter offline",
            "error": str(exc)[:160],
            "stale": False,
        }


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
