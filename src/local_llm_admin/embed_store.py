"""Embedding service history (M8): one row per embeddings or rerank request with counts,
route, width and timings. Input text, queries, documents and vectors are never stored."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .decision_store import PRUNE_BATCH, _percentile
from .jobs import ops_tz
from .storage import Database


class EmbedStore(Database):
    def add_request(
        self,
        *,
        ts: float,
        source: str,
        route: str,
        inputs: int | None,
        chars: int | None,
        tokens: int | None,
        dims: int | None,
        latency_ms: float | None,
        status: str,
        error_code: int | None,
        client_id: str | None = None,
        remote_addr: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO embed_requests (ts, client_id, source, route, inputs, chars, tokens,"
                " dims, latency_ms, status, error_code, remote_addr)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    client_id,
                    source,
                    route,
                    inputs,
                    chars,
                    tokens,
                    dims,
                    latency_ms,
                    status,
                    error_code,
                    remote_addr,
                ),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, client_id, source, route, inputs, chars, tokens, dims, latency_ms,"
                " status, error_code FROM embed_requests ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _rows(self, frm: float, to: float) -> list[Any]:
        with self._conn() as c:
            return c.execute(
                "SELECT ts, source, client_id, route, inputs, chars, latency_ms, status"
                " FROM embed_requests WHERE ts >= ? AND ts < ? ORDER BY ts",
                (frm, to),
            ).fetchall()

    @staticmethod
    def _fold(acc: dict[str, dict[str, Any]], label: str, r: Any) -> None:
        a = acc.setdefault(label, {"requests": 0, "errors": 0, "inputs": 0, "chars": 0, "lat": []})
        a["requests"] += 1
        a["inputs"] += r["inputs"] or 0
        a["chars"] += r["chars"] or 0
        if r["status"] == "ok":
            if r["latency_ms"] is not None:
                a["lat"].append(float(r["latency_ms"]))
        else:
            a["errors"] += 1

    @staticmethod
    def _out(acc: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
        out = []
        for label in sorted(acc):
            a = acc[label]
            lat = sorted(a.pop("lat"))
            out.append(
                {key: label, **a, "p50_ms": _percentile(lat, 0.5), "p95_ms": _percentile(lat, 0.95)}
            )
        return out

    def daily(self, frm: float, to: float, tz_offset_min: int) -> list[dict[str, Any]]:
        tz = ops_tz(tz_offset_min)
        acc: dict[str, dict[str, Any]] = {}
        for r in self._rows(frm, to):
            self._fold(acc, datetime.fromtimestamp(r["ts"], tz).date().isoformat(), r)
        return self._out(acc, "day")

    def usage(
        self, frm: float, to: float, tz_offset_min: int, group: str, names: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Per ops-timezone day or per client; admin-page requests are "(admin page)",
        gateway requests without a valid key "(no key)"."""
        tz = ops_tz(tz_offset_min)
        acc: dict[str, dict[str, Any]] = {}
        for r in self._rows(frm, to):
            if group == "day":
                label = datetime.fromtimestamp(r["ts"], tz).date().isoformat()
            elif r["source"] == "admin-ui":
                label = "(admin page)"
            elif r["client_id"] is None:
                label = "(no key)"
            else:
                label = names.get(r["client_id"], f"client {r['client_id']}")
            self._fold(acc, label, r)
        return self._out(acc, "day" if group == "day" else "client")

    def prune(self, before: float, limit: int = PRUNE_BATCH) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM embed_requests WHERE id IN (SELECT id FROM embed_requests WHERE"
                " ts < ? ORDER BY id LIMIT ?)",
                (before, limit),
            )
            return cur.rowcount
