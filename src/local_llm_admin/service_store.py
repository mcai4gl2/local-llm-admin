"""Metadata-only resident usage, with a read adapter for untouched embedding history."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .decision_store import PRUNE_BATCH, _percentile
from .embed_store import EmbedStore
from .jobs import ops_tz
from .storage import Database


class ServiceStore(Database):
    def __init__(self, path: Path, settings: Settings) -> None:
        super().__init__(path)
        self.settings = settings
        self.embeddings = EmbedStore(path)

    def add_request(
        self, *, ts: float, service: str, model: str, capability: str, source: str,
        latency_ms: float | None, status: str, error_code: int | None,
        pages: int | None, input_bytes: int | None, output_chars: int | None,
        client_id: str | None = None,
    ) -> None:
        if service == "qwen-embed":
            raise ValueError("embedding requests must use the legacy history adapter")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO service_requests (ts,service,model,capability,source,client_id,"
                "latency_ms,status,error_code,pages,input_bytes,output_chars)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, service, model, capability, source, client_id, latency_ms, status,
                 error_code, pages, input_bytes, output_chars),
            )

    def recent(self, service: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        if service == "qwen-embed":
            return [
                {**row, "service": service, "model": (
                    self.settings.embed_model_id if row["route"] == "embed"
                    else self.settings.rerank_model_id
                ), "capability": "embeddings" if row["route"] == "embed" else "rerank",
                 "input_chars": row["chars"], "pages": None, "input_bytes": None,
                 "output_chars": None}
                for row in self.embeddings.recent(limit)
            ]
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts,service,model,capability,source,client_id,latency_ms,status,error_code,"
                "pages,input_bytes,output_chars FROM service_requests WHERE service=?"
                " ORDER BY ts DESC,id DESC LIMIT ?", (service, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def daily(
        self, service: str, start: float, end: float, timezone_offset: int,
    ) -> list[dict[str, Any]]:
        if service == "qwen-embed":
            return [{**row, "pages": None, "input_bytes": None, "output_chars": None}
                    for row in self.embeddings.daily(start, end, timezone_offset)]
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts,status,latency_ms,pages,input_bytes,output_chars FROM service_requests"
                " WHERE service=? AND ts>=? AND ts<? ORDER BY ts", (service, start, end),
            ).fetchall()
        days: dict[str, dict[str, Any]] = {}
        for row in rows:
            day = datetime.fromtimestamp(row["ts"], ops_tz(timezone_offset)).date().isoformat()
            acc = days.setdefault(day, {"day": day, "requests": 0, "errors": 0,
                                       "pages": None, "input_bytes": None,
                                       "output_chars": None, "latencies": []})
            acc["requests"] += 1
            acc["errors"] += int(row["status"] != "ok")
            for key in ("pages", "input_bytes", "output_chars"):
                if row[key] is not None:
                    acc[key] = (acc[key] or 0) + row[key]
            if row["status"] == "ok" and row["latency_ms"] is not None:
                acc["latencies"].append(row["latency_ms"])
        out = []
        for day in sorted(days):
            acc = days[day]
            values = sorted(acc.pop("latencies"))
            out.append({**acc, "p50_ms": _percentile(values, 0.5),
                        "p95_ms": _percentile(values, 0.95)})
        return out

    def prune(self, before: float, limit: int = PRUNE_BATCH) -> int:
        with self._conn() as conn:
            result = conn.execute(
                "DELETE FROM service_requests WHERE id IN (SELECT id FROM service_requests"
                " WHERE ts<? ORDER BY id LIMIT ?)", (before, limit),
            )
            return result.rowcount
