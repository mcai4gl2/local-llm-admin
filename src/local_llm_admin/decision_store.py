"""Decision sidecar history (M6 Phase 4): one row per read, one availability sample per
interval. The availability table holds samples for every sidecar (M7). Rows hold counts
and question types only; the state and the question text are never stored."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .jobs import ops_tz
from .storage import Database

TYPES = ("noul", "choice", "score")
SAMPLE_STATES = ("ready", "loading", "down", "stopped", "unknown")
PRUNE_BATCH = 5000


def request_summary(body: Any) -> dict[str, Any]:
    """Counts and type names from a request body, whatever its shape; nothing else."""
    out: dict[str, Any] = {"n_questions": None, "types": None, "steps": None, "samples": None}
    if not isinstance(body, dict):
        return out
    qs = body.get("questions")
    if isinstance(qs, dict):
        out["n_questions"] = len(qs)
        types = {q.get("type") for q in qs.values() if isinstance(q, dict)}
        out["types"] = ",".join(sorted(t for t in types if t in TYPES)) or None
    for key in ("steps", "samples"):
        value = body.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


def _percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    i = min(len(sorted_values) - 1, max(0, round(p * (len(sorted_values) - 1))))
    return sorted_values[i]


class DecisionStore(Database):
    def add_request(
        self,
        *,
        ts: float,
        source: str,
        summary: dict[str, Any],
        input_tokens: int | None,
        latency_ms: float | None,
        model_ms: float | None,
        status: str,
        error_code: int | None,
        client_id: str | None = None,
        remote_addr: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO decision_requests (ts, client_id, source, n_questions, types,"
                " steps, samples, input_tokens, latency_ms, model_ms, status, error_code,"
                " remote_addr) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    client_id,
                    source,
                    summary.get("n_questions"),
                    summary.get("types"),
                    summary.get("steps"),
                    summary.get("samples"),
                    input_tokens,
                    latency_ms,
                    model_ms,
                    status,
                    error_code,
                    remote_addr,
                ),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, client_id, source, n_questions, types, steps, samples,"
                " input_tokens, latency_ms, status, error_code FROM decision_requests"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def daily(self, frm: float, to: float, tz_offset_min: int) -> list[dict[str, Any]]:
        """Per ops-timezone day: requests, errors, input tokens, p50/p95 of successful reads."""
        tz = ops_tz(tz_offset_min)
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, status, latency_ms, input_tokens FROM decision_requests"
                " WHERE ts >= ? AND ts < ? ORDER BY ts",
                (frm, to),
            ).fetchall()
        days: dict[str, dict[str, Any]] = {}
        for r in rows:
            day = datetime.fromtimestamp(r["ts"], tz).date().isoformat()
            d = days.setdefault(day, {"requests": 0, "errors": 0, "input_tokens": 0, "lat": []})
            d["requests"] += 1
            d["input_tokens"] += r["input_tokens"] or 0
            if r["status"] == "ok":
                if r["latency_ms"] is not None:
                    d["lat"].append(float(r["latency_ms"]))
            else:
                d["errors"] += 1
        out = []
        for day in sorted(days):
            d = days[day]
            lat = sorted(d.pop("lat"))
            out.append(
                {"day": day, **d, "p50_ms": _percentile(lat, 0.5), "p95_ms": _percentile(lat, 0.95)}
            )
        return out

    def add_sample(self, ts: float, state: str, sidecar: str = "openjev") -> None:
        if state not in SAMPLE_STATES:
            raise ValueError(f"unknown sample state: {state}")
        with self._conn() as c:
            c.execute(
                "INSERT INTO decision_availability (ts, state, sidecar) VALUES (?, ?, ?)",
                (ts, state, sidecar),
            )

    def availability(self, frm: float, to: float, sidecar: str = "openjev") -> dict[str, Any]:
        """Sample counts per state for one sidecar; ratio = ready / (ready + loading + down),
        i.e. over the samples where it was meant to run. Stopped and unknown are excluded."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT state, count(*) AS n FROM decision_availability WHERE ts >= ? AND"
                " ts < ? AND sidecar = ? GROUP BY state",
                (frm, to, sidecar),
            ).fetchall()
        counts = {s: 0 for s in SAMPLE_STATES}
        counts.update({r["state"]: r["n"] for r in rows})
        intended = counts["ready"] + counts["loading"] + counts["down"]
        return {"counts": counts, "ratio": counts["ready"] / intended if intended else None}

    def prune(self, before: float, limit: int = PRUNE_BATCH) -> int:
        removed = 0
        with self._conn() as c:
            for table in ("decision_requests", "decision_availability"):
                cur = c.execute(
                    f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE ts < ?"
                    " ORDER BY id LIMIT ?)",
                    (before, limit),
                )
                removed += cur.rowcount
        return removed

    def usage(
        self, frm: float, to: float, tz_offset_min: int, group: str, names: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Totals per ops-timezone day or per client: requests, errors, questions, samples
        (what drives GPU time), input tokens, and p50/p95 of successful reads. Admin-page
        reads are the client "(admin page)"; gateway requests without a valid key are
        "(no key)"."""
        tz = ops_tz(tz_offset_min)
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, source, client_id, n_questions, samples, input_tokens, latency_ms,"
                " status FROM decision_requests WHERE ts >= ? AND ts < ? ORDER BY ts",
                (frm, to),
            ).fetchall()
        acc: dict[str, dict[str, Any]] = {}
        for r in rows:
            if group == "day":
                label = datetime.fromtimestamp(r["ts"], tz).date().isoformat()
            elif r["source"] == "admin-ui":
                label = "(admin page)"
            elif r["client_id"] is None:
                label = "(no key)"
            else:
                label = names.get(r["client_id"], f"client {r['client_id']}")
            a = acc.setdefault(
                label,
                {
                    "requests": 0,
                    "errors": 0,
                    "questions": 0,
                    "samples": 0,
                    "input_tokens": 0,
                    "lat": [],
                },
            )
            a["requests"] += 1
            a["questions"] += r["n_questions"] or 0
            if r["status"] == "ok":
                a["samples"] += r["samples"] or 1  # reads actually run on the sidecar
            a["input_tokens"] += r["input_tokens"] or 0
            if r["status"] != "ok":
                a["errors"] += 1
            elif r["latency_ms"] is not None:
                a["lat"].append(float(r["latency_ms"]))
        key = "day" if group == "day" else "client"
        out = []
        for label in sorted(acc):
            a = acc[label]
            lat = sorted(a.pop("lat"))
            out.append(
                {key: label, **a, "p50_ms": _percentile(lat, 0.5), "p95_ms": _percentile(lat, 0.95)}
            )
        return out
