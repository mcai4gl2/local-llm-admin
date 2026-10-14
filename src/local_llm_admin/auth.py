"""Single admin password (argon2), signed session cookie, CSRF token, login rate limit."""

from __future__ import annotations

import getpass
import hmac
import secrets
import time
from collections.abc import Callable
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE_NAME = "llm_admin_session"
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


class SessionManager:
    def __init__(self, secret: str, max_age_s: int) -> None:
        # No configured secret: sessions only last until the admin restarts.
        self._s = URLSafeTimedSerializer(secret or secrets.token_hex(32), salt="llm-admin-session")
        self.max_age_s = max_age_s

    def issue(self) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(32)
        return self._s.dumps({"csrf": csrf}), csrf

    def load(self, cookie: str | None) -> dict[str, Any] | None:
        if not cookie:
            return None
        try:
            data = self._s.loads(cookie, max_age=self.max_age_s)
        except BadSignature:
            return None
        return data if isinstance(data, dict) and isinstance(data.get("csrf"), str) else None


def csrf_ok(session: dict[str, Any], token: str | None) -> bool:
    return token is not None and hmac.compare_digest(str(session["csrf"]), token)


class LoginLimiter:
    """At most ``max_failures`` failed logins per client IP per ``window_s``."""

    def __init__(
        self, max_failures: int, window_s: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._clock = clock
        self._fails: dict[str, list[float]] = {}

    def _recent(self, ip: str) -> list[float]:
        cutoff = self._clock() - self.window_s
        fails = [t for t in self._fails.get(ip, []) if t > cutoff]
        self._fails[ip] = fails
        return fails

    def allowed(self, ip: str) -> bool:
        return len(self._recent(ip)) < self.max_failures

    def fail(self, ip: str) -> None:
        self._recent(ip).append(self._clock())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


def hash_password_cli() -> int:
    """``local-llm-admin-hash``: prompt twice, print an env line for the config file."""
    pw = getpass.getpass("Admin password: ")
    if pw != getpass.getpass("Repeat: ") or not pw:
        print("Passwords do not match (or are empty).")
        return 1
    print(f"ADMIN_PASSWORD_HASH='{hash_password(pw)}'")
    return 0
