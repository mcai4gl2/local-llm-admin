"""Alert store (conditions and one-shot events) and the threshold evaluator.

A condition is open until its check clears; while open it is one row whose ``last_seen``
moves with every observation (repeated polls of one outage do not add rows or counts).
An unknown observation (``None``) never resolves a condition. Events are facts: they
are stored already resolved and stay unread until acknowledged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ingestion import IngestStore, error_hint
from .storage import Database

SEVERITIES = ("info", "warning", "critical")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}
_OPEN = "(resolved_at IS NULL OR (kind = 'event' AND acked_at IS NULL))"
_UNACKED = "acked_at IS NULL AND " + _OPEN


class AlertStore(Database):
    def raise_condition(
        self,
        key: str,
        severity: str,
        title: str,
        detail: str,
        now: float,
        count: int | None = None,
    ) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM alerts WHERE key = ? AND kind = 'condition'"
                " AND resolved_at IS NULL",
                (key,),
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE alerts SET last_seen = ?, detail = ?, title = ?, severity = ?,"
                    " count = COALESCE(?, count) WHERE id = ?",
                    (now, detail, title, severity, count, row["id"]),
                )
                return int(row["id"])
            cur = c.execute(
                "INSERT INTO alerts (key, kind, severity, title, detail, first_seen, last_seen,"
                " count) VALUES (?, 'condition', ?, ?, ?, ?, ?, ?)",
                (key, severity, title, detail, now, now, count or 1),
            )
            return int(cur.lastrowid or 0)

    def resolve_condition(self, key: str, now: float) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE alerts SET resolved_at = ? WHERE key = ? AND kind = 'condition'"
                " AND resolved_at IS NULL",
                (now, key),
            )
        return cur.rowcount > 0

    def open_condition_keys(self, prefix: str) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT key FROM alerts WHERE kind = 'condition' AND resolved_at IS NULL"
                " AND key LIKE ?",
                (prefix.replace("%", r"\%") + "%",),
            ).fetchall()
        return [r["key"] for r in rows]

    def event(
        self,
        key: str,
        severity: str,
        title: str,
        detail: str,
        now: float,
        dedup: str | None = None,
    ) -> int | None:
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO alerts (key, kind, severity, title, detail, first_seen,"
                " last_seen, resolved_at, dedup) VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?)",
                (key, severity, title, detail, now, now, now, dedup),
            )
        return int(cur.lastrowid) if cur.rowcount and cur.lastrowid else None

    def list(self, state: str, limit: int, before: int | None = None) -> list[dict[str, Any]]:
        where = [_OPEN] if state == "open" else []
        args: list[Any] = []
        if before is not None:
            where.append("id < ?")
            args.append(before)
        sql = "SELECT * FROM alerts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(sql, [*args, limit]).fetchall()
        return [dict(r) for r in rows]

    def ack(self, alert_id: int, now: float) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT acked_at FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            if row is None:
                return False
            if row["acked_at"] is None:
                c.execute("UPDATE alerts SET acked_at = ? WHERE id = ?", (now, alert_id))
        return True

    def ack_all(self, now: float) -> int:
        with self._conn() as c:
            cur = c.execute(f"UPDATE alerts SET acked_at = ? WHERE {_UNACKED}", (now,))
        return cur.rowcount

    def summary(self) -> dict[str, Any]:
        with self._conn() as c:
            n_open = c.execute(f"SELECT count(*) FROM alerts WHERE {_OPEN}").fetchone()[0]
            unacked = c.execute(f"SELECT severity FROM alerts WHERE {_UNACKED}").fetchall()
        worst = max((r["severity"] for r in unacked), key=lambda s: _RANK.get(s, 0), default=None)
        return {"open": n_open, "unacked": len(unacked), "worst_severity": worst}

    def cleanup(self, now: float, retention_days: int, limit: int = 1000) -> int:
        """Drop resolved rows older than the retention window; open conditions stay."""
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM alerts WHERE id IN (SELECT id FROM alerts WHERE resolved_at"
                " IS NOT NULL AND resolved_at < ? ORDER BY id LIMIT ?)",
                (now - retention_days * 86400, limit),
            )
        return cur.rowcount


@dataclass(frozen=True)
class Snapshot:
    """One observation of the host. ``None`` fields mean "could not tell"."""

    wall: float
    mono: float
    drive_mounted: bool | None
    access_error: str | None
    access_known: bool
    engine: str  # mlx | comfyui | none | conflict | unknown
    mlx_serving: bool | None
    intent: str | None
    job_running: bool
    lock: dict[str, Any] | None
    pressure: str | None
    litellm_ok: bool | None  # None: not checked this tick
    suspended: str | None


ENGINE_DOWN_S = 30.0
UNHEALTHY_S = 300.0
STALE_LOCK_S = 600.0
PRESSURE_S = 120.0
LITELLM_FAILURES = 2
ERRORS_WINDOW_S = 900.0
ERRORS_THRESHOLD = 3
ERRORS_QUIET_S = 3600.0


class Evaluator:
    def __init__(self, store: AlertStore, events: IngestStore) -> None:
        self.store = store
        self.events = events
        self._since: dict[str, float] = {}  # condition -> monotonic time first seen
        self._litellm_failures = 0

    def _held(self, key: str, active: bool, now: float) -> float:
        """Seconds ``active`` has held continuously (0 when it just started or cleared)."""
        if not active:
            self._since.pop(key, None)
            return 0.0
        return now - self._since.setdefault(key, now)

    def _set(
        self, key: str, state: bool | None, severity: str, title: str, detail: str, wall: float
    ) -> None:
        if state is True:
            self.store.raise_condition(key, severity, title, detail, wall)
        elif state is False:
            self.store.resolve_condition(key, wall)

    def evaluate(self, s: Snapshot) -> None:
        w = s.wall
        drive = None if s.drive_mounted is None else not s.drive_mounted
        self._set(
            "drive_missing",
            drive,
            "critical",
            "Model drive is not mounted",
            "Mount /Volumes/Models; engines cannot start without it.",
            w,
        )
        access = bool(s.access_error) if s.access_known else None
        self._set(
            "drive_access",
            access,
            "critical",
            "The admin cannot read the model drive",
            s.access_error or "",
            w,
        )
        conflict = None if s.engine == "unknown" else s.engine == "conflict"
        self._set(
            "engine_conflict",
            conflict,
            "critical",
            "MLX and ComfyUI are both running",
            "Keep one engine.",
            w,
        )
        self._engine_down(s)
        self._unhealthy(s)
        self._lock(s)
        self._memory(s)
        self._litellm(s)
        self._set(
            "watchdog_suspended",
            s.suspended is not None,
            "critical",
            "Watchdog suspended",
            s.suspended or "",
            w,
        )
        self.request_errors(w)

    def _engine_down(self, s: Snapshot) -> None:
        intent = s.intent or ""
        wanted = (
            "mlx" if intent.startswith("mlx:") else intent if intent in ("comfyui", "strata") else None
        )
        if s.engine == "unknown" and wanted:
            return
        mismatch = wanted is not None and s.engine not in (wanted, "conflict")
        held = self._held("engine_down", mismatch and not s.job_running, s.mono)
        if not mismatch:
            self.store.resolve_condition("engine_down", s.wall)
        elif held > ENGINE_DOWN_S:
            name = {"comfyui": "ComfyUI", "strata": "Strata"}.get(wanted or "", f"MLX ({intent[4:]})")
            detail = f"Intended engine is {intent}; observed: {s.engine}."
            if wanted != "mlx":
                detail += f" {name} is not restarted automatically."
            self.store.raise_condition(
                "engine_down", "critical", f"{name} is not running", detail, s.wall
            )

    def _unhealthy(self, s: Snapshot) -> None:
        relevant = (s.intent or "").startswith("mlx:") and s.engine == "mlx"
        if relevant and s.mlx_serving is None:
            return
        bad = relevant and s.mlx_serving is False
        if self._held("engine_unhealthy", bad, s.mono) > UNHEALTHY_S:
            self.store.raise_condition(
                "engine_unhealthy",
                "warning",
                "MLX process is running but not serving",
                "The API has not answered /health for 5 minutes. The watchdog does not start"
                " a second instance; stop or switch the model.",
                s.wall,
            )
        elif not bad:
            self.store.resolve_condition("engine_unhealthy", s.wall)

    def _lock(self, s: Snapshot) -> None:
        if s.lock is None:
            return
        stale = s.lock["state"] == "stale" and (s.lock["age_s"] or 0) > STALE_LOCK_S
        if stale:
            self.store.raise_condition(
                "stale_lock",
                "warning",
                "Stale mlx-model lock",
                f"run/mlx.lock held for {int(s.lock['age_s'] // 60)} min with no running"
                " owner. Use Unlock on the Alerts page.",
                s.wall,
            )
        elif s.lock["state"] in ("none", "live"):
            self.store.resolve_condition("stale_lock", s.wall)

    def _memory(self, s: Snapshot) -> None:
        if s.pressure is None:
            return
        critical = self._held("memory_critical", s.pressure == "critical", s.mono)
        normal = self._held("memory_normal", s.pressure == "normal", s.mono)
        if critical > PRESSURE_S:
            self.store.raise_condition(
                "memory_critical",
                "warning",
                "Memory pressure critical",
                "Critical for over 2 minutes. Switch to a smaller model or stop ComfyUI jobs.",
                s.wall,
            )
        elif normal > PRESSURE_S:
            self.store.resolve_condition("memory_critical", s.wall)

    def _litellm(self, s: Snapshot) -> None:
        if s.litellm_ok is None:
            return
        if s.litellm_ok:
            self._litellm_failures = 0
            self.store.resolve_condition("litellm_unreachable", s.wall)
            return
        self._litellm_failures += 1
        if self._litellm_failures >= LITELLM_FAILURES:
            self.store.raise_condition(
                "litellm_unreachable",
                "warning",
                "LiteLLM gateway unreachable",
                "Two checks in a row failed; see Diagnostics for the error.",
                s.wall,
            )

    def request_errors(self, wall: float) -> None:
        rows = self.events.events(kinds=("error",), since=wall - ERRORS_QUIET_S, limit=5000)
        recent: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            recent.setdefault(r["signature"], []).append(r)
        for sig, group in recent.items():
            burst = [r for r in group if r["ts"] >= wall - ERRORS_WINDOW_S]
            key = f"request_errors:{sig}"
            if len(burst) >= ERRORS_THRESHOLD or key in self.store.open_condition_keys(key):
                message = group[0]["message"]
                clients = sorted({r["data"].get("client") for r in group} - {None})
                detail = message
                if hint := error_hint(message):
                    detail += f" — {hint}"
                detail += f" (clients: {', '.join(clients) if clients else 'unknown'})"
                self.store.raise_condition(
                    key, "warning", "Repeated request errors", detail, wall, count=len(group)
                )
        for key in self.store.open_condition_keys("request_errors:"):
            if key.split(":", 1)[1] not in recent:
                self.store.resolve_condition(key, wall)
