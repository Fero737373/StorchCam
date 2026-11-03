"""Shared JSON-over-HTTP requests for the external providers."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..version import __version__

USER_AGENT = f"StorchCam/{__version__}"


def fetch_json(url: str, *, headers: dict[str, str], timeout: int, form: dict[str, Any] | None = None) -> Any:
    """GET (oder POST mit Formulardaten) und Antwort als JSON dekodieren."""
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8") if form is not None else None,
        headers=headers,
        method="POST" if form is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def fetch_json_object(
    url: str, source: str, *, headers: dict[str, str], timeout: int, form: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = fetch_json(url, headers=headers, timeout=timeout, form=form)
    if not isinstance(result, dict):
        raise ValueError(f"{source} lieferte kein JSON-Objekt")
    return result
