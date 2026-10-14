"""Per-client keys for the decision gateway (M6 Phase 5).

A key is ``dsk_<8-char prefix>_<43-char secret>`` and is shown once at creation. Only its
SHA-256 is stored; a request is matched by prefix, then compared in constant time. There
is no cache, so a revoked key fails on its next request.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
import time
from typing import Any

from .storage import Database

KEY_RE = re.compile(r"^dsk_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
TOUCH_EVERY_S = 60.0
_PREFIX_CHARS = string.ascii_lowercase + string.digits


class KeyError_(Exception):  # noqa: N801 - not the builtin KeyError
    """A refused key-management request; ``code`` is the HTTP status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _check_name(name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise KeyError_(400, "name must be 1-48 characters of A-Z a-z 0-9 _ . -")
    return name


class ClientKeys(Database):
    def create(self, name: str, note: str | None = None) -> dict[str, Any]:
        """A new client and its key; the key is in this result only."""
        _check_name(name)
        with self._conn() as c:
            taken = c.execute(
                "SELECT 1 FROM decision_clients WHERE name = ? AND revoked_at IS NULL", (name,)
            ).fetchone()
            if taken:
                raise KeyError_(409, "a client with that name already exists")
            while True:
                prefix = "".join(secrets.choice(_PREFIX_CHARS) for _ in range(8))
                if not c.execute(
                    "SELECT 1 FROM decision_clients WHERE key_prefix = ?", (prefix,)
                ).fetchone():
                    break
            key = f"dsk_{prefix}_{secrets.token_urlsafe(32)}"
            cur = c.execute(
                "INSERT INTO decision_clients (name, key_prefix, key_hash, created_at, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, prefix, hash_key(key), time.time(), (note or "")[:200] or None),
            )
        return {"id": int(cur.lastrowid or 0), "name": name, "prefix": prefix, "key": key}

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, name, key_prefix AS prefix, created_at, revoked_at, last_used_at,"
                " note FROM decision_clients ORDER BY revoked_at IS NOT NULL, name, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def names(self) -> dict[str, str]:
        """client id (as stored in usage rows) -> name, revoked clients included."""
        return {str(r["id"]): r["name"] for r in self.list()}

    def _get(self, c: Any, client_id: int) -> Any:
        row = c.execute("SELECT * FROM decision_clients WHERE id = ?", (client_id,)).fetchone()
        if row is None:
            raise KeyError_(404, "unknown client")
        return row

    def rename(self, client_id: int, name: str) -> None:
        _check_name(name)
        with self._conn() as c:
            self._get(c, client_id)
            taken = c.execute(
                "SELECT 1 FROM decision_clients WHERE name = ? AND revoked_at IS NULL AND id != ?",
                (name, client_id),
            ).fetchone()
            if taken:
                raise KeyError_(409, "a client with that name already exists")
            c.execute("UPDATE decision_clients SET name = ? WHERE id = ?", (name, client_id))

    def revoke(self, client_id: int) -> None:
        with self._conn() as c:
            self._get(c, client_id)
            c.execute(
                "UPDATE decision_clients SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (time.time(), client_id),
            )

    def verify(self, authorization: str | None) -> dict[str, Any] | None:
        """The active client for an ``Authorization: Bearer <key>`` header, or None."""
        if not authorization or not authorization.startswith("Bearer "):
            return None
        key = authorization[7:].strip()
        m = KEY_RE.fullmatch(key)
        if not m:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT id, name, key_hash, revoked_at, last_used_at FROM decision_clients"
                " WHERE key_prefix = ?",
                (m.group(1),),
            ).fetchone()
            if row is None or not hmac.compare_digest(row["key_hash"], hash_key(key)):
                return None
            if row["revoked_at"] is not None:
                return None
            now = time.time()
            if row["last_used_at"] is None or not 0 <= now - row["last_used_at"] < TOUCH_EVERY_S:
                c.execute(
                    "UPDATE decision_clients SET last_used_at = ? WHERE id = ?", (now, row["id"])
                )
        return {"id": int(row["id"]), "name": row["name"]}
