"""Durable request history (M4 §1): the ingestion sink, first-run seeding and queries.

``PerfLogs`` reads ``mlx-server.log`` through its own bounded ``Ingestor`` source
(``mlx-perf``), separate from M1's error source so the watchdog's evidence cursor is
untouched. Parsed lines are not stored as events: the sink correlates them and writes
finished requests plus the correlation state in the cursor's transaction.
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from .config import Settings
from .ingestion import (
    BACKFILL_BYTES,
    BATCH_BYTES,
    Ingestor,
    IngestStore,
    PollResult,
    Sink,
    archive_paths,
    head_matches,
    redact,
)
from .jobs import ops_tz
from .performance import Correlator, PerfState, parse_perf_line
from .storage import Database

PERF_SOURCE = "mlx-perf"
MLX_LOG = "mlx-server.log"
PARSER = "mlx-vlm 0.7.3 log lines"  # the runtime whose line shapes the parser was built from
MAX_SEED_SCAN = 64 * 1024 * 1024  # backwards from the backfill window; beyond: unknown
_SEED_BLOCK = 1024 * 1024
_ROW_COLS = (
    "key",
    "request_id",
    "started",
    "finished",
    "model",
    "attribution",
    "stream",
    "images",
    "prompt_tokens",
    "cached_tokens",
    "generated_tokens",
    "prefill_s",
    "prefill_tok_s",
    "ttft_s",
    "decode_s",
    "decode_tok_s",
    "elapsed_s",
    "finish_reason",
    "state",
    "error",
    "complete",
    "log_identity",
    "log_start",
    "log_end",
)
_UPDATABLE = ("elapsed_s", "stream", "generated_tokens")
_INSERT = (
    f"INSERT OR IGNORE INTO inference_requests (source, {', '.join(_ROW_COLS)})"
    f" VALUES (?, {', '.join('?' * len(_ROW_COLS))})"
)


def _last_generation(fh: BinaryIO, end: int) -> dict[str, Any] | None:
    """Scan backwards from ``end`` for the newest start marker. Process starts seen
    between it and ``end``: none → the marker's process is still starting (fresh), one →
    it is that process, more → a marker-less restart followed, so unknown."""
    starts = 0
    pos, stop = end, max(0, end - MAX_SEED_SCAN)
    rest = b""  # the partial first line of the block read after this one
    while pos > stop:
        step = min(_SEED_BLOCK, pos - stop)
        pos -= step
        fh.seek(pos)
        data = fh.read(step) + rest
        lines = data.split(b"\n")
        rest = lines.pop(0) if pos > 0 else b""  # complete only at the start of the file
        for raw in reversed(lines):
            if not raw.startswith((b"=====", b"INFO:")):
                continue
            ev = parse_perf_line(raw.decode("utf-8", "replace").rstrip("\r"))
            if ev is None:
                continue
            if ev["kind"] == "server_start":
                starts += 1
                continue
            if starts > 1:
                return None
            return {"name": ev["data"]["name"], "ts": ev["ts"], "fresh": starts == 0}
    return None


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None without values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _state_key(source: str) -> str:
    return f"perf_state:{source}"


class PerformanceStore(Database):
    # --- ingestion side --------------------------------------------------------------
    def sink(self, source: str) -> Sink:
        key = _state_key(source)

        def apply(conn: sqlite3.Connection, events: list[dict[str, Any]], gap: bool) -> None:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            corr = Correlator(PerfState.from_json(row[0] if row else None))
            out = corr.apply(events, gap)
            self._insert(conn, source, out.rows)
            for upd in out.updates:
                fields = {k: v for k, v in upd["fields"].items() if k in _UPDATABLE}
                sets = ", ".join(f"{k} = ?" for k in fields)
                conn.execute(
                    f"UPDATE inference_requests SET {sets} WHERE source = ? AND key = ?",
                    (*fields.values(), source, upd["key"]),
                )
            conn.execute(
                "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) ON CONFLICT(key)"
                " DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (key, corr.state.to_json(), time.time()),
            )

        return apply

    @staticmethod
    def _insert(conn: sqlite3.Connection, source: str, rows: list[dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            n += conn.execute(_INSERT, (source, *(r[c] for c in _ROW_COLS))).rowcount
        return n

    def insert_rows(self, source: str, rows: list[dict[str, Any]]) -> int:
        with self._conn() as c:
            return self._insert(c, source, rows)

    def state(self, source: str) -> PerfState:
        with self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key = ?", (_state_key(source),)).fetchone()
        return PerfState.from_json(row["value"] if row else None)

    def seed(self, source: str, path: Path, backfill_bytes: int) -> None:
        """First sight: the generation marker before the backfill window, if any.

        The file is continuous from that marker to the window, so the weights it names
        are still the ones serving unless a process started without a marker since.
        """
        try:
            with path.open("rb") as fh:
                generation = _last_generation(fh, max(0, fh.seek(0, 2) - backfill_bytes))
        except OSError:
            generation = None
        self.set_kv(_state_key(source), PerfState(generation=generation).__dict__)

    # --- queries -------------------------------------------------------------------
    def requests(
        self,
        frm: float,
        to: float,
        *,
        model: str | None = None,
        state: str | None = None,
        min_ttft_s: float | None = None,
        limit: int = 50,
        before: int | None = None,
    ) -> dict[str, Any]:
        sql = "SELECT * FROM inference_requests WHERE started >= ? AND started < ?"
        args: list[Any] = [frm, to]
        for clause, value in (
            (" AND model = ?", model),
            (" AND state = ?", state),
            (" AND ttft_s >= ?", min_ttft_s),
            (" AND id < ?", before),
        ):
            if value is not None:
                sql += clause
                args.append(value)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit + 1)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"requests": rows, "next": rows[-1]["id"] if more else None}

    def models(self, frm: float, to: float) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT model FROM inference_requests WHERE started >= ? AND started < ?"
                " AND model IS NOT NULL ORDER BY model",
                (frm, to),
            ).fetchall()
        return [r["model"] for r in rows]

    def get(self, row_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM inference_requests WHERE id = ?", (row_id,)).fetchone()
        return dict(row) if row else None

    def summary(
        self,
        frm: float,
        to: float,
        *,
        tz_offset_min: int,
        slow_ttft_s: float = 10.0,
        model: str | None = None,
    ) -> dict[str, Any]:
        sql = (
            "SELECT started, model, state, complete, ttft_s, decode_tok_s FROM"
            " inference_requests WHERE started >= ? AND started < ?"
        )
        args: list[Any] = [frm, to]
        if model is not None:
            sql += " AND model = ?"
            args.append(model)
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY started", args).fetchall()
        tz = ops_tz(tz_offset_min)
        days: dict[str, list[Any]] = {}
        for r in rows:
            days.setdefault(datetime.fromtimestamp(r["started"], tz).date().isoformat(), []).append(
                r
            )
        return {
            "days": [{"date": d, **self._stats(rs, slow_ttft_s)} for d, rs in days.items()],
            "totals": self._stats(rows, slow_ttft_s),
            "slow_ttft_s": slow_ttft_s,
        }

    @staticmethod
    def _stats(rows: list[Any], slow_ttft_s: float) -> dict[str, Any]:
        ttft = [r["ttft_s"] for r in rows if r["ttft_s"] is not None]
        decode = [r["decode_tok_s"] for r in rows if r["decode_tok_s"] is not None]
        return {
            "requests": len(rows),
            "errors": sum(r["state"] == "error" for r in rows),
            "cancelled": sum(r["state"] == "cancelled" for r in rows),
            "incomplete": sum(r["state"] == "incomplete" for r in rows),
            "partial": sum(not r["complete"] for r in rows),
            "slow": sum(t >= slow_ttft_s for t in ttft),
            "ttft_p50": percentile(ttft, 0.5),
            "ttft_p90": percentile(ttft, 0.9),
            "decode_p50": percentile(decode, 0.5),
            "decode_p90": percentile(decode, 0.9),
            "models": sorted({r["model"] for r in rows if r["model"]}),
        }

    def last_ingest(self, source: str) -> float | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT updated FROM log_cursors WHERE source = ?", (source,)
            ).fetchone()
        return float(row["updated"]) if row else None

    def prune_bounded(self, before: float, batch: int = 5000, batches: int = 10) -> int:
        """The daily retention pass: at most ``batches`` bounded deletes."""
        total = 0
        for _ in range(batches):
            n = self.prune(before, batch)
            total += n
            if n < batch:
                break
        return total

    def prune(self, before: float, limit: int = 5000) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM inference_requests WHERE id IN (SELECT id FROM inference_requests"
                " WHERE started < ? ORDER BY id LIMIT ?)",
                (before, limit),
            )
        return cur.rowcount


def request_context(
    log_dir: Path,
    row: dict[str, Any],
    cursor: dict[str, Any] | None,
    *,
    max_bytes: int = 64 * 1024,
    max_lines: int = 200,
) -> dict[str, Any]:
    """The log lines of one request, read from the file (live or rotated) whose identity
    the row recorded. Bounded, redacted, and never from a path the client chose."""
    try:
        dev, ino, epoch = (row["log_identity"] or "").split(":")
        want = (int(dev), int(ino))
    except ValueError:
        want, epoch = (-1, -1), ""
    live = log_dir / MLX_LOG
    for path in (live, *archive_paths(log_dir, MLX_LOG)):
        try:
            fh = path.open("rb")
        except OSError:
            continue
        with fh:
            st = os.fstat(fh.fileno())
            if st.st_ino != want[1]:
                continue
            if st.st_dev != want[0] and not (
                # remounted: rows keep the identity recorded before, and the live file is
                # recognised by the cursor's fingerprint (archives have none: unavailable)
                path == live
                and cursor
                and (cursor["dev"], cursor["inode"]) == want
                and head_matches(fh.fileno(), st.st_size, cursor.get("head"))
            ):
                continue
            # a truncated live file reuses offsets: its content epoch must still match
            if path == live and cursor and (cursor["dev"], cursor["inode"]) == want:
                if f"{cursor['gap_at'] or 0}" != epoch:
                    break
            start, end = int(row["log_start"]), int(row["log_end"])
            if start >= st.st_size or end < start:
                break
            fh.seek(start)
            data = fh.read(min(max_bytes, end - start + 4096))
        nl = data.find(b"\n", end - start)
        if nl >= 0:
            data = data[:nl]
        lines = data.decode("utf-8", errors="replace").splitlines()[:max_lines]
        return {"available": True, "lines": [redact(ln) for ln in lines]}
    return {
        "available": False,
        "lines": [],
        "reason": "the log file that held this request was replaced or pruned",
    }


class PerfLogs:
    """The ``mlx-perf`` poller; bounded like every other ``Ingestor``."""

    def __init__(
        self,
        settings: Settings,
        ingest: IngestStore,
        perf: PerformanceStore,
        now: Callable[[], float] = time.time,
        *,
        batch_bytes: int = BATCH_BYTES,
        backfill_bytes: int = BACKFILL_BYTES,
    ) -> None:
        log_dir = settings.mlx_log_dir
        self.path = log_dir / MLX_LOG
        self.ingest = ingest
        self.perf = perf
        self.backfill_bytes = backfill_bytes
        self.ingestor = Ingestor(
            ingest,
            PERF_SOURCE,
            self.path,
            parse_perf_line,
            batch_bytes=batch_bytes,
            backfill_bytes=backfill_bytes,
            now=now,
            sink=perf.sink(PERF_SOURCE),
            store_events=False,
            archives=lambda: archive_paths(log_dir, MLX_LOG),
        )

    def poll(self) -> PollResult:
        if self.ingest.cursor(PERF_SOURCE) is None:
            self.perf.seed(PERF_SOURCE, self.path, self.backfill_bytes)
        return self.ingestor.poll()
