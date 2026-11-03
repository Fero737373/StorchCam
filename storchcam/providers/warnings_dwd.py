"""Official weather warnings of the Deutscher Wetterdienst through the free Bright Sky API.

Bright Sky maps the location to the DWD municipality cell and returns the CAP alerts for it.
Outside Germany there are no cells; the API answers 404 and the kiosk simply shows nothing.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from .http_json import USER_AGENT, fetch_json_object

LOGGER = logging.getLogger(__name__)
ALERTS_URL = "https://api.brightsky.dev/alerts"
SEVERITY = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
# Vorabinformationen, die bald beginnen, schon zeigen („ab 18 Uhr“).
LOOKAHEAD = timedelta(hours=6)


def _time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _headline(alert: dict[str, Any]) -> str:
    """„Amtliche WARNUNG vor STURMBÖEN“ → „Amtliche Warnung vor Sturmböen“ (DWD schreibt Teile groß)."""
    text = str(alert.get("headline_de") or alert.get("event_de") or "Wetterwarnung").strip()
    return " ".join(word.capitalize() if word.isupper() and len(word) > 2 else word for word in text.split())


def relevant(alerts: list[Any], minimum: str, now: datetime) -> list[dict[str, Any]]:
    threshold = SEVERITY.get(minimum, SEVERITY["moderate"])
    found: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, dict) or str(alert.get("status", "actual")).lower() not in {"actual", ""}:
            continue
        severity = str(alert.get("severity") or "").lower()
        if SEVERITY.get(severity, 0) < threshold:
            continue
        onset = _time(alert.get("onset") or alert.get("effective"))
        expires = _time(alert.get("expires"))
        if expires and expires <= now:
            continue
        if onset and onset > now + LOOKAHEAD:
            continue
        found.append({
            "id": str(alert.get("alert_id") or alert.get("id") or ""),
            "headline": _headline(alert),
            "event": str(alert.get("event_de") or ""),
            "severity": severity,
            "level": SEVERITY.get(severity, 0),
            "onset": onset.isoformat() if onset else "",
            "expires": expires.isoformat() if expires else "",
            "description": str(alert.get("description_de") or "")[:600],
            "instruction": str(alert.get("instruction_de") or "")[:400],
        })
    found.sort(key=lambda item: (-item["level"], item["onset"]))
    return found


def get_warnings(config: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    minimum = str(config.get("weather", {}).get("warnings", "moderate"))
    if minimum == "off":
        return {"ok": True, "enabled": False, "alerts": []}
    location = config.get("location", {})
    params = {
        "lat": str(location.get("latitude", 52.5200)),
        "lon": str(location.get("longitude", 13.4050)),
        "tz": str(config.get("app", {}).get("timezone", "Europe/Berlin")),
    }
    url = f"{ALERTS_URL}?{urllib.parse.urlencode(params)}"
    current = now or datetime.now(timezone.utc)
    try:
        data = fetch_json_object(url, "Bright Sky", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=12)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # außerhalb Deutschlands gibt es keine DWD-Warnzellen
            return {"ok": True, "enabled": True, "covered": False, "alerts": []}
        raise
    alerts = data.get("alerts")
    found = relevant(alerts if isinstance(alerts, list) else [], minimum, current)
    place = data.get("location")
    cell = str(place.get("name") or "") if isinstance(place, dict) else ""
    return {"ok": True, "enabled": True, "covered": True, "alerts": found, "cell": cell}
