"""Small local PIN authentication and expiring in-memory admin sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

PBKDF2_ITERATIONS = 240_000


def validate_pin(pin: str) -> None:
    if not pin.isdigit() or not 4 <= len(pin) <= 12:
        raise ValueError("Die PIN muss aus 4 bis 12 Ziffern bestehen")


def hash_pin(pin: str) -> str:
    validate_pin(pin)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class SessionStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        expires = time.monotonic() + self.ttl_seconds
        with self._lock:
            self._tokens[token] = expires
            self._purge_locked()
        return token, self.ttl_seconds

    def valid(self, token: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expires = self._tokens.get(token, 0)
            self._purge_locked(now)
            return expires > now

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def _purge_locked(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self._tokens = {token: expiry for token, expiry in self._tokens.items() if expiry > current}
