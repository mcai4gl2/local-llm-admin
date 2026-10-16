"""Observed availability (M4 §2): one sample a minute from the monitor's snapshot.

Samples reuse the Supervisor's per-tick observation (no extra drive probes). A sample
covers the time until the next one, but never more than two intervals: a longer gap
(admin down, probes hung) is unknown, and unknown time never counts as uptime.

Service availability = serving time / observed time while MLX was the intended engine.
Deliberate off and ComfyUI time is reported separately, with observation coverage.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .alerts import Snapshot
from .config import Settings
from .jobs import ops_tz
from .storage import Database

log = logging.getLogger("local_llm_admin")

STATES = ("serving", "loading", "down", "conflict", "comfyui", "strata", "off", "unknown")
PRUNE_BATCH = 5000
PRUNE_BATCHES = 10


def _intended(intent: str | None) -> int | None:
    if intent and intent.startswith("mlx:"):
        return 1
    if intent in ("off", "comfyui", "strata"):
        return 0
    return None  # unknown intent: excluded from the availability denominator


def classify(
    snap: Snapshot, generation: dict[str, Any] | None, *, load_grace_s: float
) -> tuple[str, int | None]:
    """(state, mlx_intended) for one observation."""
    intended = _intended(snap.intent)
    if snap.drive_mounted is not True or snap.engine == "unknown":
        return "unknown", intended
    if snap.engine == "conflict":
        return "conflict", intended
    if snap.engine == "mlx":
        if snap.mlx_serving is True:
            return "serving", intended
        young = generation is not None and snap.wall - generation["started"] < load_grace_s
        return ("loading" if snap.job_running or young else "down"), intended
    if snap.engine in ("comfyui", "strata"):
        return snap.engine, intended
    if intended == 1:
        return ("loading" if snap.job_running else "down"), intended
    return ("off" if intended == 0 else "unknown"), intended


def _day_edges(frm: float, to: float, tz_offset_min: int) -> list[tuple[str, float, float]]:
    tz = ops_tz(tz_offset_min)
    day = datetime.fromtimestamp(frm, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while day.timestamp() < to:
        nxt = day + timedelta(days=1)
        out.append((day.date().isoformat(), max(frm, day.timestamp()), min(to, nxt.timestamp())))
        day = nxt
    return out


def _intervals(
    samples: list[dict[str, Any]], interval_s: float, now: float
) -> list[tuple[float, float, dict[str, Any]]]:
    ordered = sorted(samples, key=lambda s: s["ts"])
    out = []
    for i, s in enumerate(ordered):
        if i + 1 < len(ordered):
            gap = ordered[i + 1]["ts"] - s["ts"]
            end = s["ts"] + (gap if gap <= 2 * interval_s else interval_s)
        else:
            end = min(s["ts"] + interval_s, max(now, s["ts"]))
        out.append((s["ts"], end, s))
    return out


def _empty() -> dict[str, Any]:
    return {"seconds": dict.fromkeys(STATES, 0.0), "intended_s": 0.0, "serving_s": 0.0}


def _finish(acc: dict[str, Any], span: float) -> dict[str, Any]:
    secs = acc["seconds"]
    observed = sum(v for k, v in secs.items() if k != "unknown")
    return {
        "seconds": secs,
        "observed_s": observed,
        "unknown_s": max(0.0, span - observed),
        "intended_s": acc["intended_s"],
        "availability": acc["serving_s"] / acc["intended_s"] if acc["intended_s"] > 0 else None,
        "coverage": observed / span if span > 0 else 0.0,
    }


def summarize(
    samples: list[dict[str, Any]],
    frm: float,
    to: float,
    *,
    interval_s: float,
    tz_offset_min: int,
    now: float,
) -> dict[str, Any]:
    """Per ops day and total: seconds by state, availability, coverage, and a strip."""
    end = min(to, now)  # the future is neither observed nor unknown
    days = _day_edges(frm, end, tz_offset_min) if end > frm else []
    accs = [_empty() for _ in days]
    total = _empty()
    segments: list[dict[str, Any]] = []
    for a, b, s in _intervals(samples, interval_s, now):
        a, b = max(a, frm), min(b, end)
        if b <= a:
            continue
        state = s["state"] if s["state"] in STATES else "unknown"
        if segments and segments[-1]["state"] == state and segments[-1]["end"] == a:
            segments[-1]["end"] = b
        else:
            segments.append({"start": a, "end": b, "state": state})
        for acc, (_, lo, hi) in [(total, ("", frm, end)), *zip(accs, days, strict=True)]:
            overlap = min(b, hi) - max(a, lo)
            if overlap <= 0:
                continue
            acc["seconds"][state] += overlap
            if state != "unknown" and s["mlx_intended"] == 1:
                acc["intended_s"] += overlap
                if state == "serving":
                    acc["serving_s"] += overlap
    return {
        "days": [
            {"date": d, **_finish(acc, hi - lo)}
            for acc, (d, lo, hi) in zip(accs, days, strict=True)
        ],
        "totals": _finish(total, max(0.0, end - frm)),
        "segments": segments,
        "interval_s": interval_s,
    }


class AvailabilityStore(Database):
    def add(
        self,
        ts: float,
        state: str,
        mlx_intended: int | None,
        generation: str | None,
        model: str | None,
        pressure: str | None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO availability_samples (ts, state, mlx_intended, generation, model,"
                " pressure) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, state, mlx_intended, generation, model, pressure),
            )

    def samples(self, frm: float, to: float, lookback_s: float = 0.0) -> list[dict[str, Any]]:
        """Samples in [frm, to), plus the ones in ``lookback_s`` before ``frm`` that may
        cover its start."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, state, mlx_intended, generation, model, pressure FROM"
                " availability_samples WHERE ts >= ? AND ts < ? ORDER BY ts",
                (frm - lookback_s, to),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, before: float, limit: int = PRUNE_BATCH) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM availability_samples WHERE id IN (SELECT id FROM"
                " availability_samples WHERE ts < ? ORDER BY id LIMIT ?)",
                (before, limit),
            )
        return cur.rowcount


class AvailabilityRecorder:
    """Supervisor tick listener: a sample per interval and a bounded daily prune."""

    def __init__(
        self,
        store: AvailabilityStore,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
        prune_extra: Callable[[float], object] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.clock = clock
        self.prune_extra = prune_extra
        self._last: float | None = None
        self._last_prune: float | None = None

    def on_tick(self, snap: Snapshot, observed: dict[str, Any] | None) -> None:
        ts = snap.wall
        interval = self.settings.availability_interval_s
        # a clock that went back must not silence sampling until it catches up
        if self._last is None or ts - self._last >= interval or ts < self._last:
            self._last = ts
            gen = observed.get("generation") if observed else None
            state, intended = classify(snap, gen, load_grace_s=self.settings.admin_job_timeout_s)
            mlx = observed is not None and observed.get("engine") == "mlx"
            self.store.add(
                ts,
                state,
                intended,
                f"{gen['pid']}@{gen['started']}" if gen else None,
                observed.get("mlx_name") if mlx and observed else None,
                snap.pressure,
            )
        self._prune(self.clock())

    def _prune(self, now: float) -> None:
        if self._last_prune is not None and 0 <= now - self._last_prune < 86400:
            return
        self._last_prune = now
        before = now - self.settings.observability_retention_days * 86400
        for _ in range(PRUNE_BATCHES):
            if self.store.prune(before, PRUNE_BATCH) < PRUNE_BATCH:
                break
        if self.prune_extra is not None:
            self.prune_extra(before)
