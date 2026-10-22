"""Strata token usage: the request records in ``GET /metrics`` as ``inference_requests``.

Strata keeps a bounded list of finished requests (``requests``, newest first; each
``time`` is the request's start) and counters since the process started (``totals``).
Each poll inserts the records it has not seen as rows keyed ``<since>:<time>``, so
re-reading the same list is a no-op. When more requests finished between two polls than
the list keeps, the shortfall against ``totals`` becomes one ``aggregate`` row, so the
token totals always match Strata's own counters. A new ``since`` is a new process.
``bin/strata stop`` saves the server's last ``/metrics`` before it stops it, so what the
old process did after the last poll is still counted; the cursor is kept per process, so
that reading counts once even after a newer process was polled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .config import Settings
from .engines import StrataClient, strata_pack_of
from .performance_store import PerformanceStore

log = logging.getLogger(__name__)

STRATA_SOURCE = "strata"
STATE_KEY = "strata_usage"  # {repr(since): state} per process, newest MAX_PROCESSES
MAX_PROCESSES = 8
FINAL_PREFIX = "metrics-final"  # run/<prefix>-<pid>.json, written by bin/strata stop
MAX_FINALS = 4  # files read per tick
MAX_FINAL_BYTES = 8 * 1024 * 1024
_COUNTERS = ("requests", "prompt_tokens", "reused", "output_tokens")


def _num(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(d: dict[str, Any], key: str) -> int | None:
    v = _num(d, key)
    return int(v) if v is not None else None


def _row(key: str, started: float, model: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(
        (
            "request_id",
            "finished",
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
            "error",
            "log_identity",
            "log_start",
            "log_end",
        )
    )
    row.update(key=key, started=started, model=model, attribution="generation", requests=1)
    row.update(state="completed", complete=1)
    row.update(fields)
    return row


def _record_row(rec: dict[str, Any], since: float, model: str) -> dict[str, Any]:
    started = float(rec["time"])
    duration = _num(rec, "duration_s")
    prompt, reused = _int(rec, "prompt_tokens"), _int(rec, "reused")
    prompt_ms, decode_ms = _num(rec, "prompt_ms"), _num(rec, "decode_ms")
    read = prompt - (reused or 0) if prompt is not None else None
    finish = rec.get("finish")
    return _row(
        f"{since}:{started}",
        started,
        model,
        finished=started + duration if duration is not None else None,
        prompt_tokens=prompt,
        cached_tokens=reused,
        generated_tokens=_int(rec, "output_tokens"),
        prefill_s=prompt_ms / 1000 if prompt_ms is not None else None,
        prefill_tok_s=read / prompt_ms * 1000 if read is not None and prompt_ms else None,
        ttft_s=prompt_ms / 1000 if prompt_ms is not None else None,  # roughly: prefill time
        decode_s=decode_ms / 1000 if decode_ms is not None else None,
        decode_tok_s=_num(rec, "decode_tok_s"),
        elapsed_s=duration,
        finish_reason=finish if isinstance(finish, str) else None,
    )


def strata_rows(
    metrics: dict[str, Any], model: str, last: dict[str, Any] | None, *, now: float
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The rows one ``/metrics`` reading adds after ``last`` (the state this returned
    for the previous reading, at time ``now``), and the new state.

    Strata appends to its history and its totals together, and lists the history newest
    finished first, so the new records are the first ``totals.requests`` minus the last
    count of them. Start times are not in that order (parallel slots), so they are never
    compared. Every field is read with ``.get``."""
    raw_totals = metrics.get("totals")
    totals: dict[str, Any] = raw_totals if isinstance(raw_totals, dict) else {}
    since = _num(totals, "since")
    if since is None:
        return [], last
    same = last is not None and last.get("since") == since
    counts = {k: _int(totals, k) or 0 for k in _COUNTERS}
    base = (
        {k: int(last.get(k) or 0) for k in _COUNTERS}
        if same and last
        else dict.fromkeys(_COUNTERS, 0)
    )
    delta = {k: counts[k] - base[k] for k in _COUNTERS}
    raw = metrics.get("requests")
    listed = raw if isinstance(raw, list) else []
    fresh = listed[: max(0, delta["requests"])]
    rows = [
        _record_row(r, since, model)
        for r in fresh
        if isinstance(r, dict) and _num(r, "time") is not None
    ]
    added = {
        "requests": len(rows),
        "prompt_tokens": sum(r["prompt_tokens"] or 0 for r in rows),
        "reused": sum(r["cached_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["generated_tokens"] or 0 for r in rows),
    }
    if min(delta.values()) >= 0 and delta["requests"] > added["requests"]:
        gap = {k: max(0, delta[k] - added[k]) for k in _COUNTERS}
        polled = last.get("polled") if same and last else None
        rows.append(
            _row(
                f"{since}:gap:{counts['requests']}",
                float(polled) if isinstance(polled, (int, float)) else since,
                model,
                requests=gap["requests"],
                prompt_tokens=gap["prompt_tokens"],
                cached_tokens=gap["reused"],
                generated_tokens=gap["output_tokens"],
                state="aggregate",
            )
        )
    return rows, {"since": since, **counts, "polled": now}


def _same_counts(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in ("since", *_COUNTERS))


def _model(name: object) -> str:
    """``strata:<pack>`` from the model id Strata reports."""
    n = name if isinstance(name, str) else None
    return f"strata:{strata_pack_of(n) or n or 'unknown'}"


def _remember(states: dict[str, Any]) -> dict[str, Any]:
    """The newest ``MAX_PROCESSES`` per-process states (an old process's final reading
    can arrive after a newer one was polled, so one state per process is kept)."""

    def since(item: tuple[str, Any]) -> float:
        v = item[1].get("since") if isinstance(item[1], dict) else None
        return float(v) if isinstance(v, (int, float)) else 0.0

    return dict(sorted(states.items(), key=since)[-MAX_PROCESSES:])


class StrataUsage:
    """Polls Strata's ``/metrics`` on the supervisor tick while Strata is the engine, and
    reads the last ``/metrics`` that ``bin/strata stop`` saved before each stop."""

    def __init__(self, settings: Settings, strata: StrataClient, perf: PerformanceStore) -> None:
        self.settings = settings
        self.strata = strata
        self.perf = perf

    def record(self, metrics: dict[str, Any], model: str) -> int:
        """Adds one reading's new rows; the per-process cursor commits with them."""
        raw = self.perf.get_kv(STATE_KEY)
        states: dict[str, Any] = raw if isinstance(raw, dict) else {}
        totals = metrics.get("totals")
        since = _num(totals, "since") if isinstance(totals, dict) else None
        if since is None:
            return 0
        key = repr(since)
        prev = states.get(key)
        prev = prev if isinstance(prev, dict) else None
        rows, state = strata_rows(metrics, model, prev, now=time.time())
        if state is None or (prev is not None and _same_counts(state, prev)):
            return 0
        states[key] = state
        return self.perf.insert_rows(STRATA_SOURCE, rows, state=(STATE_KEY, _remember(states)))

    async def poll(self) -> int:
        health = await self.strata.get_json("/health")
        if health is None or health.get("service") != "strata":
            return 0
        # by default /metrics lists only the newest 12 requests; all = up to 500
        metrics = await self.strata.get_json("/metrics?requests=all")
        if metrics is None:
            return 0
        return await asyncio.to_thread(self.record, metrics, _model(health.get("model")))

    def _read_final(self) -> int:
        try:
            paths = sorted(
                (self.settings.strata_root / "run").glob(f"{FINAL_PREFIX}-*.json"),
                key=lambda p: (p.stat().st_mtime, p.name),
            )
        except OSError:
            return 0
        added = 0
        for path in paths[:MAX_FINALS]:
            try:
                data = (
                    json.loads(path.read_bytes())
                    if path.stat().st_size <= MAX_FINAL_BYTES
                    else None
                )
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                engine = data.get("engine")
                added += self.record(
                    data, _model(engine.get("model") if isinstance(engine, dict) else None)
                )
            path.unlink(missing_ok=True)  # after its rows: a crash re-reads it, a no-op
        return added

    async def read_final(self) -> int:
        """The readings ``bin/strata stop`` saved (oldest first, a few per tick)."""
        return await asyncio.to_thread(self._read_final)

    async def on_tick(self, snap: object, observed: dict[str, Any] | None) -> None:
        if not self.settings.strata_enabled:
            return
        try:
            # the files are on the model drive: only while it is known to be mounted
            if getattr(snap, "drive_mounted", None) is True:
                await self.read_final()
            if observed and observed.get("engine") == "strata":
                await self.poll()
        except Exception:  # a bad reading must never stop the supervisor
            log.exception("strata usage poll failed")
