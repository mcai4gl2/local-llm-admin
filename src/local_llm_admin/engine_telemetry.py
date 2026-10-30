"""Durable cumulative native readings, intentionally separate from request accounting."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from typing import Any

from .splash_telemetry import source_coverage
from .storage import Database

STATE_KEY = "splash_telemetry"
MAX_EPOCHS = 128
MAX_GAPS = 32


def _save(conn: sqlite3.Connection, state: dict[str, Any], now: float) -> None:
    conn.execute(
        "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) ON CONFLICT(key)"
        " DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (STATE_KEY, json.dumps(state), now),
    )


def _gap(state: dict[str, Any], code: str, now: float) -> None:
    gaps = state.get("gaps", [])
    if not gaps or gaps[-1]["reason"] != code:
        gaps.append({"from": state.get("last_poll"), "observed": now, "reason": code})
    state["gaps"] = gaps[-MAX_GAPS:]


class EngineTelemetryStore(Database):
    @staticmethod
    def _state(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (STATE_KEY,)).fetchone()
        return json.loads(row[0]) if row else {}

    def record(self, reading: dict[str, Any], *, now: float, gap_after_s: float = 60) -> int:
        """Insert changed values and cursor atomically; reject late or regressed polls."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            state = self._state(c)
            if now <= state.get("last_attempt", -1):
                return 0
            if state.get("last_poll") is not None and now - state["last_poll"] > gap_after_s:
                _gap(state, "observation_gap", now)
            epochs = state.get("epochs", {})
            epoch, counts = reading["epoch"], reading["counters"]
            previous = epochs.get(epoch)
            if previous and any(
                type(v) is int and type(previous.get(k)) is int and v < previous[k]
                for k, v in counts.items()
            ):
                _gap(state, "counter_regression", now)
                state.update(last_attempt=now, error="counter_regression")
                _save(c, state, now)
                return 0
            if previous is None:
                _gap(state, "initial_baseline" if not epochs else "generation_changed", now)
            encoded = json.dumps(counts, sort_keys=True)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            added = c.execute(
                "INSERT OR IGNORE INTO engine_readings (source, epoch, observed, profile,"
                " fingerprint, model, identity, counters, digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reading["source"],
                    epoch,
                    now,
                    reading["profile"],
                    reading["fingerprint"],
                    reading["model"],
                    json.dumps(reading["identity"]),
                    encoded,
                    digest,
                ),
            ).rowcount
            epochs.pop(epoch, None)
            epochs[epoch] = {
                **(previous or {}),
                **{k: v for k, v in counts.items() if v is not None},
            }
            epochs = dict(list(epochs.items())[-MAX_EPOCHS:])
            state.update(
                epochs=epochs,
                last_poll=now,
                last_attempt=now,
                error=None,
                epoch=epoch,
                profile=reading["profile"],
                fingerprint=reading["fingerprint"],
            )
            if added:
                state.update(last_ingest=now, last_new_data=now)
            _save(c, state, now)
            return added

    def failure(self, code: str, *, now: float) -> None:
        # Only fixed public codes; no exception messages, credentials or response bodies.
        if code not in {"unavailable", "unattributed", "disabled", "absent", "storage_error"}:
            code = "unavailable"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            state = self._state(c)
            if now <= state.get("last_attempt", -1):
                return
            _gap(state, code, now)
            state.update(last_attempt=now, error=code)
            _save(c, state, now)

    def coverage(self, enabled: bool, *, now: float, stale_s: float = 60) -> dict[str, Any]:
        state = self.get_kv(STATE_KEY) or {}
        result = source_coverage(enabled)
        last = state.get("last_poll")
        if enabled and last is not None:
            result.update(
                coverage="aggregate_snapshots",
                reason="Native cumulative execution snapshots only; request history and billed token coverage are unavailable.",
            )
        result.update(
            {
                k: state.get(k)
                for k in (
                    "last_poll",
                    "last_attempt",
                    "last_ingest",
                    "last_new_data",
                    "error",
                    "epoch",
                    "profile",
                    "fingerprint",
                )
            }
        )
        result.update(
            stale=last is None or now < last or now - last > stale_s,
            gaps=state.get("gaps", []),
            counter_qualification="unverified",
            final_capture="unsupported",
            scope="native_execution",
        )
        return result

    def readings(
        self,
        frm: float,
        to: float,
        *,
        profile: str | None = None,
        fingerprint: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["source='splash'", "observed>=?", "observed<?"]
        args: list[Any] = [frm, to]
        for column, value in (("profile", profile), ("fingerprint", fingerprint)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM engine_readings WHERE "
                + " AND ".join(where)
                + " ORDER BY observed DESC, id DESC LIMIT ?",
                (*args, min(500, max(1, limit))),
            ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["counters"] = json.loads(row["counters"])
            row["identity"] = json.loads(row["identity"])
            row.pop("digest")
        return result

    def page(
        self,
        frm: float,
        to: float,
        *,
        profile: str | None = None,
        fingerprint: str | None = None,
        epoch: str | None = None,
        limit: int = 20,
        before: str | None = None,
    ) -> dict[str, Any]:
        """A stable (observed, id) cursor; catalogs cover the range, never just a page."""
        where = ["source='splash'", "observed>=?", "observed<?"]
        args: list[Any] = [frm, to]
        if before is not None:
            try:
                raw_ts, raw_id = before.split(":")
                ts, row_id = float(raw_ts), int(raw_id)
                if not math.isfinite(ts) or row_id < 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("invalid snapshot cursor") from None
            where.append("(observed<? OR (observed=? AND id<?))")
            args.extend((ts, ts, row_id))
        for column, value in (("profile", profile), ("fingerprint", fingerprint), ("epoch", epoch)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        limit = min(500, max(1, limit))
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM engine_readings WHERE " + " AND ".join(where)
                + " ORDER BY observed DESC, id DESC LIMIT ?", (*args, limit + 1)
            ).fetchall()
            profiles = c.execute(
                "SELECT DISTINCT profile, fingerprint FROM engine_readings"
                " WHERE source='splash' AND observed>=? AND observed<? ORDER BY profile, fingerprint",
                (frm, to),
            ).fetchall()
            epochs = c.execute(
                "SELECT epoch, profile, fingerprint, MAX(observed) AS observed FROM engine_readings"
                " WHERE source='splash' AND observed>=? AND observed<?"
                + (" AND profile=?" if profile is not None else "")
                + " GROUP BY epoch, profile, fingerprint ORDER BY observed DESC, epoch",
                (frm, to, profile) if profile is not None else (frm, to),
            ).fetchall()
        more = len(rows) > limit
        result = [dict(row) for row in rows[:limit]]
        for row in result:
            row["counters"] = json.loads(row["counters"])
            row["identity"] = json.loads(row["identity"])
            row.pop("digest")
        return {
            "readings": result,
            "next": f"{result[-1]['observed']}:{result[-1]['id']}" if more else None,
            "limit": limit,
            "profiles": [dict(row) for row in profiles],
            "epochs": [dict(row) for row in epochs],
        }

    def prune(self, before: float, limit: int = 5000) -> int:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM engine_readings WHERE id IN (SELECT id FROM engine_readings"
                " WHERE observed<? ORDER BY id LIMIT ?)",
                (before, limit),
            ).rowcount
