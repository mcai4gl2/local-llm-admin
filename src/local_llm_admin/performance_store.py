"""Durable request history (M4 §1): the ingestion sink, first-run seeding and queries.

``PerfLogs`` reads ``mlx-server.log`` through its own bounded ``Ingestor`` source
(``mlx-perf``), separate from M1's error source so the watchdog's evidence cursor is
untouched. Parsed lines are not stored as events: the sink correlates them and writes
finished requests plus the correlation state in the cursor's transaction.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

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
    "requests",
)
_UPDATABLE = ("elapsed_s", "stream", "generated_tokens")
# a request that started inside a quality or eval run (edges included, an open run has
# no end yet) is the admin's own traffic: the run holds the lifecycle slot
_ORIGIN = (
    "CASE WHEN EXISTS (SELECT 1 FROM quality_runs WHERE started <= ? AND"
    " (finished IS NULL OR finished >= ?)) THEN 'quality' ELSE 'client' END"
)
_INSERT = (
    f"INSERT OR IGNORE INTO inference_requests (source, {', '.join(_ROW_COLS)}, origin)"
    f" VALUES (?, {', '.join('?' * len(_ROW_COLS))}, {_ORIGIN})"
)
TokenGroup = Literal["day", "model", "day_model"]
_COUNTS = (
    "requests",
    "measured",
    "cached_unknown",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
)
_MEASURED = "prompt_tokens IS NOT NULL AND generated_tokens IS NOT NULL"
# token sums over request rows; a missing count adds nothing and is never estimated
_LIVE_SUMS = (
    "SUM(requests) AS requests,"
    f" SUM(CASE WHEN {_MEASURED} THEN requests ELSE 0 END) AS measured,"
    f" SUM(CASE WHEN {_MEASURED} AND cached_tokens IS NULL THEN requests ELSE 0 END)"
    " AS cached_unknown, COALESCE(SUM(prompt_tokens), 0) AS input_tokens,"
    " COALESCE(SUM(cached_tokens), 0) AS cached_tokens,"
    " COALESCE(SUM(generated_tokens), 0) AS output_tokens,"
    " MIN(started) AS first_started, MAX(started) AS last_started"
)
_FOLDED_SUMS = (
    ", ".join(f"SUM({k}) AS {k}" for k in _COUNTS)
    + ", MIN(first_started) AS first_started, MAX(last_started) AS last_started"
)
_DAY = "date(started - ?, 'unixepoch')"  # the ops day; ? = tz_offset_min * 60
_FOLD = (
    "INSERT INTO token_usage_daily (date, source, model, origin, requests, measured,"
    " cached_unknown, input_tokens, cached_tokens, output_tokens, first_started,"
    f" last_started) SELECT {_DAY}, source, COALESCE(model, ''), origin, {_LIVE_SUMS}"
    " FROM inference_requests WHERE id IN ({ids}) GROUP BY 1, 2, 3, 4"
    " ON CONFLICT(date, source, model, origin) DO UPDATE SET"
    + ",".join(f" {k} = {k} + excluded.{k}" for k in _COUNTS)
    + ", first_started = MIN(first_started, excluded.first_started),"
    " last_started = MAX(last_started, excluded.last_started)"
)
_PRUNE_IDS = "SELECT id FROM inference_requests WHERE started < ? ORDER BY id LIMIT ?"


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
            values = (r.get(c, 1 if c == "requests" else None) for c in _ROW_COLS)
            n += conn.execute(_INSERT, (source, *values, r["started"], r["started"])).rowcount
        return n

    def insert_rows(
        self,
        source: str,
        rows: list[dict[str, Any]],
        *,
        state: tuple[str, Any] | None = None,
    ) -> int:
        """Inserts new rows; ``state`` (a kv key and value) is saved in the same
        transaction, so a poller's cursor and its rows never disagree."""
        with self._conn() as c:
            n = self._insert(c, source, rows)
            if state is not None:
                c.execute(
                    "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) ON CONFLICT(key)"
                    " DO UPDATE SET value = excluded.value, updated = excluded.updated",
                    (state[0], json.dumps(state[1]), time.time()),
                )
            return n

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
        if state is None:
            sql += " AND state != 'aggregate'"  # a Strata gap row is not one request
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
            "SELECT started, model, state, complete, ttft_s, decode_tok_s, prompt_tokens,"
            " cached_tokens, generated_tokens FROM inference_requests WHERE started >= ?"
            " AND started < ? AND state != 'aggregate'"
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
            "input_tokens": sum(r["prompt_tokens"] or 0 for r in rows),
            "cached_tokens": sum(r["cached_tokens"] or 0 for r in rows),
            "output_tokens": sum(r["generated_tokens"] or 0 for r in rows),
        }

    def tokens(
        self,
        frm: float,
        to: float,
        *,
        tz_offset_min: int,
        group: TokenGroup,
        model: str | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """Input, cached and output tokens per ops day and/or model: live rows plus the
        totals the retention pass folded into ``token_usage_daily``. A row is either live
        or folded, never both, so the two add up without overlap. Unknown model is ``''``."""
        if group not in ("day", "model", "day_model"):
            raise ValueError(f"unknown token grouping: {group!r}")
        live = (
            f"SELECT {_DAY} AS date, COALESCE(model, '') AS model, {_LIVE_SUMS}"
            " FROM inference_requests WHERE started >= ? AND started < ?"
        )
        live_args: list[Any] = [tz_offset_min * 60, frm, to]
        tz = ops_tz(tz_offset_min)
        last = datetime.fromtimestamp(max(frm, to - 0.001), tz).date().isoformat()
        folded = f"SELECT date, model, {_FOLDED_SUMS} FROM token_usage_daily WHERE date >= ? AND date <= ?"
        folded_args: list[Any] = [datetime.fromtimestamp(frm, tz).date().isoformat(), last]
        for col, value in (("model", model), ("origin", origin)):
            if value is not None:
                live += f" AND COALESCE({col}, '') = ?"
                folded += f" AND {col} = ?"
                live_args.append(value)
                folded_args.append(value)
        cells: dict[tuple[str, str], dict[str, Any]] = {}
        with self._conn() as c:
            for sql, args in ((live, live_args), (folded, folded_args)):
                for r in c.execute(sql + " GROUP BY 1, 2", args).fetchall():
                    cell = cells.setdefault((r["date"], r["model"]), dict.fromkeys(_COUNTS, 0))
                    for k in _COUNTS:
                        cell[k] += r[k]
        rows: dict[tuple[str, ...], dict[str, Any]] = {}
        for (day, name), cell in sorted(cells.items()):
            key = {"day": (day,), "model": (name,), "day_model": (day, name)}[group]
            row = rows.get(key)
            if row is None:
                row = rows[key] = dict.fromkeys(_COUNTS, 0)
                if group != "model":
                    row["date"] = day
                if group != "day":
                    row["model"] = name
                if group == "model":
                    row["first"] = day
            for k in _COUNTS:
                row[k] += cell[k]
            if group == "model":
                row["last"] = day
        totals = dict.fromkeys(_COUNTS, 0)
        for cell in cells.values():
            for k in _COUNTS:
                totals[k] += cell[k]
        out = [_with_unmeasured(rows[k]) for k in sorted(rows)]
        return {"rows": out, "totals": _with_unmeasured(totals)}

    def last_ingest(self, source: str) -> float | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT updated FROM log_cursors WHERE source = ?", (source,)
            ).fetchone()
        return float(row["updated"]) if row else None

    def prune_bounded(
        self, before: float, batch: int = 5000, batches: int = 10, *, tz_offset_min: int = 0
    ) -> int:
        """The daily retention pass: at most ``batches`` bounded deletes."""
        total = 0
        for _ in range(batches):
            n = self.prune(before, batch, tz_offset_min=tz_offset_min)
            total += n
            if n < batch:
                break
        return total

    def prune(self, before: float, limit: int = 5000, *, tz_offset_min: int = 0) -> int:
        """Deletes the oldest rows, first adding their token counts to
        ``token_usage_daily`` (per ops day) in the same transaction: a crash leaves the
        rows either live or folded, never both."""
        with self._conn() as c:
            c.execute(_FOLD.format(ids=_PRUNE_IDS), (tz_offset_min * 60, before, limit))
            cur = c.execute(
                f"DELETE FROM inference_requests WHERE id IN ({_PRUNE_IDS})", (before, limit)
            )
        return cur.rowcount


def _with_unmeasured(row: dict[str, Any]) -> dict[str, Any]:
    row["unmeasured"] = row["requests"] - row["measured"]
    return row


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
