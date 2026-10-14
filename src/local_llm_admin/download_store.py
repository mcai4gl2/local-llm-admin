"""SQLite record of download runs (M3b). The host's status.json stays authoritative."""

from __future__ import annotations

import time
from typing import Any

from .storage import Database

ACTIVE_STATES = ("starting", "running", "publishing")
_STATUS_FIELDS = (
    "state",
    "bytes_total",
    "bytes_done",
    "files_total",
    "files_done",
    "current_file",
    "error_kind",
    "message",
    "retryable",
    "published_path",
    "finished",
)


class DownloadStore(Database):
    def create(self, run_id: str, repo: str, revision: str, dest: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO downloads (id, repo, revision, dest, state, created, updated)"
                " VALUES (?, ?, ?, ?, 'starting', ?, ?)",
                (run_id, repo, revision, dest, now, now),
            )

    def update(self, run_id: str, status: dict[str, Any]) -> None:
        """Copy the known fields of a host status document onto the row."""
        values = {k: status.get(k) for k in _STATUS_FIELDS if k in status}
        if "retryable" in values:
            values["retryable"] = None if values["retryable"] is None else int(values["retryable"])
        if not values:
            return
        sets = ", ".join(f"{k} = :{k}" for k in values)
        with self._conn() as c:
            c.execute(
                f"UPDATE downloads SET {sets}, updated = :updated WHERE id = :id",
                {**values, "updated": time.time(), "id": run_id},
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM downloads WHERE id = ?", (run_id,)).fetchone()
        return _view(dict(row)) if row else None

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM downloads ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_view(dict(r)) for r in rows]

    def active(self) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(ACTIVE_STATES))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM downloads WHERE state IN ({marks}) ORDER BY created",
                ACTIVE_STATES,
            ).fetchall()
        return [_view(dict(r)) for r in rows]

    def published(self) -> dict[str, float]:
        """Published directory -> finish time, for the inventory's download date."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT published_path, finished FROM downloads WHERE state = 'published'"
                " AND published_path IS NOT NULL AND finished IS NOT NULL"
            ).fetchall()
        return {r["published_path"]: r["finished"] for r in rows}


def _view(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("retryable") is not None:
        row["retryable"] = bool(row["retryable"])
    return row
