"""Bounded, metadata-only persistence for shared gateway requests."""

from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal

from .storage import Database

Outcome = Literal[
    "rejected", "completed", "upstream_error", "timeout", "disconnected", "interrupted",
]
TimestampField = Literal[
    "admitted_at", "dispatched_at", "headers_at", "first_event_at", "first_content_at",
]

FIELDS = (
    "request_id", "started", "route", "admitted_at", "dispatched_at", "headers_at",
    "first_event_at", "first_content_at", "ended_at", "requested_model", "backend",
    "time_to_headers_s", "time_to_first_event_s", "time_to_first_content_s", "elapsed_s",
    "resolved_model", "profile_fingerprint", "generation_fingerprint", "outcome",
    "http_status", "prompt_tokens", "completion_tokens", "usage_source",
    "usage_completeness",
)
BEGIN_FIELDS = ("request_id", "started", "route")
QueueItem = tuple[Literal["begin", "finish", "barrier"], dict[str, Any] | threading.Event]


class GatewayTimeline(Database):
    """One FIFO writer. A full or failed queue cannot delay an inference request."""

    def __init__(self, path: Path, *, queue_size: int = 256) -> None:
        super().__init__(path)
        self._queue: queue.Queue[QueueItem | None] = queue.Queue(maxsize=queue_size)
        self._counter_lock = threading.Lock()
        self._dropped = 0
        self._failed = 0
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO gateway_timeline_meta (key,value) VALUES ('coverage_start',?)",
                (time.time(),),
            )
            self._coverage_start = float(conn.execute(
                "SELECT value FROM gateway_timeline_meta WHERE key='coverage_start'"
            ).fetchone()[0])
            conn.execute(
                "UPDATE gateway_requests SET outcome='interrupted', ended_at=NULL "
                "WHERE outcome='running'"
            )
        self._worker = threading.Thread(target=self._run, name="gateway-timeline", daemon=True)
        self._worker.start()

    def _put(self, item: QueueItem) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._counter_lock:
                self._dropped += 1

    def begin(self, row: dict[str, Any]) -> None:
        self._put(("begin", {key: row[key] for key in BEGIN_FIELDS}))

    def finish(self, row: dict[str, Any]) -> None:
        self._put(("finish", {key: row.get(key) for key in FIELDS}))

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            try:
                kind, value = item
                if kind == "barrier":
                    assert isinstance(value, threading.Event)
                    value.set()
                elif isinstance(value, dict):
                    with self._conn() as conn:
                        if kind == "begin":
                            conn.execute(
                                "INSERT OR IGNORE INTO gateway_requests (request_id,started,route)"
                                " VALUES (?,?,?)",
                                tuple(value[key] for key in BEGIN_FIELDS),
                            )
                        else:
                            columns = ",".join(FIELDS)
                            values = ",".join("?" for _ in FIELDS)
                            updates = ",".join(
                                f"{key}=excluded.{key}" for key in FIELDS
                                if key != "request_id"
                            )
                            conn.execute(
                                f"INSERT INTO gateway_requests ({columns}) VALUES ({values}) "
                                f"ON CONFLICT(request_id) DO UPDATE SET {updates} "
                                "WHERE gateway_requests.outcome='running'",
                                tuple(value[key] for key in FIELDS),
                            )
            except Exception:
                with self._counter_lock:
                    self._failed += 1
            finally:
                self._queue.task_done()
        self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        event = threading.Event()
        try:
            self._queue.put_nowait(("barrier", event))
        except queue.Full:
            return False
        return event.wait(timeout)

    def close(self) -> None:
        self.flush()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
        self._worker.join(timeout=1.0)

    def health(self) -> dict[str, Any]:
        with self._counter_lock:
            dropped, failed = self._dropped, self._failed
        return {"coverage_start": self._coverage_start,
                "population": "shared gateway requests only",
                "dropped_writes": dropped, "failed_writes": failed,
                "pending_writes": self._queue.qsize(), "queue_capacity": self._queue.maxsize,
                "retention_days": None}

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM gateway_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_requests(
        self, start: float, end: float, *, backend: str | None = None,
        outcome: str | None = None, request_id: str | None = None,
        limit: int = 50, before: int | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        clauses = ["started>=?", "started<?"]
        values: list[Any] = [start, end]
        for field, value in (("backend", backend), ("outcome", outcome),
                             ("request_id", request_id)):
            if value is not None:
                clauses.append(f"{field}=?")
                values.append(value)
        if before is not None:
            clauses.append("id<?")
            values.append(before)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM gateway_requests WHERE " + " AND ".join(clauses)
                + " ORDER BY id DESC LIMIT ?", (*values, limit + 1),
            ).fetchall()
        page = [dict(row) for row in rows[:limit]]
        return {"requests": page, "next": page[-1]["id"] if len(rows) > limit else None}

    def prune(self, *, now: float, retention_days: int, batch: int = 500) -> int:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM gateway_requests WHERE started<? AND outcome!='running' "
                "ORDER BY id LIMIT ?", (now - retention_days * 86400, min(batch, 1000)),
            ).fetchall()
            if rows:
                conn.executemany("DELETE FROM gateway_requests WHERE id=?",
                                 [(row["id"],) for row in rows])
        return len(rows)


class RequestTrace:
    """One request's small in-memory snapshot; no body or arbitrary upstream fields."""

    def __init__(self, timeline: GatewayTimeline | None, request_id: str, route: str) -> None:
        self.timeline = timeline
        self.row: dict[str, Any] = {"request_id": request_id, "started": time.time(),
                                    "route": route}
        self._started_mono = time.monotonic()
        self._dispatched_mono: float | None = None
        self.outcome: Outcome | None = None
        self.status: int | None = None
        self.done = False
        self.stream_error = False
        if timeline is not None:
            timeline.begin(self.row)

    def mark(self, field: TimestampField) -> None:
        if field in self.row:
            return
        self.row[field] = time.time()
        now = time.monotonic()
        if field == "dispatched_at":
            self._dispatched_mono = now
        elif self._dispatched_mono is not None:
            duration_field = {
                "headers_at": "time_to_headers_s",
                "first_event_at": "time_to_first_event_s",
                "first_content_at": "time_to_first_content_s",
            }.get(field)
            if duration_field is not None:
                self.row[duration_field] = max(0.0, now - self._dispatched_mono)

    def identity(self, descriptor: Any, requested_model: str | None) -> None:
        identity = descriptor.identity
        backend = identity.get("engine")
        self.row["backend"] = backend if backend in {"mlx", "strata", "splash"} else None
        resolved = identity.get("model") or descriptor.target.model.rsplit("/", 1)[-1]
        if isinstance(resolved, str) and resolved.startswith("/"):
            resolved = resolved.rsplit("/", 1)[-1]
        self.row["resolved_model"] = resolved[:200] if isinstance(resolved, str) else None
        if requested_model is not None:
            requested = (
                requested_model.rsplit("/", 1)[-1]
                if requested_model.startswith("/") else requested_model
            )
            self.row["requested_model"] = requested[:200]
        for key in ("profile_fingerprint", "generation_fingerprint"):
            value = identity.get(key)
            lengths = (20, 64) if key == "profile_fingerprint" else (64,)
            self.row[key] = (
                value if isinstance(value, str) and len(value) in lengths
                and re.fullmatch(r"[a-f0-9]+", value) else None
            )

    def observe_document(self, document: dict[str, Any]) -> None:
        self.mark("first_event_at")
        if isinstance(document.get("error"), dict):
            self.stream_error = True
        choices = document.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                content = delta.get("content") if isinstance(delta, dict) else choice.get("text")
                if isinstance(content, str) and content:
                    self.mark("first_content_at")
                    break
        self.observe_usage(document)

    def observe_usage(self, document: dict[str, Any]) -> None:
        usage = document.get("usage")
        if isinstance(usage, dict):
            found = False
            for key in ("prompt_tokens", "completion_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    self.row[key] = value
                    found = True
            if found:
                self.row["usage_source"] = "upstream_response"
                self.row["usage_completeness"] = (
                    "complete" if all(self.row.get(k) is not None for k in
                                      ("prompt_tokens", "completion_tokens")) else "partial"
                )

    def terminal(self, outcome: Outcome, status: int | None) -> None:
        if self.outcome is None:
            self.outcome, self.status = outcome, status

    def finalize(self) -> None:
        if self.done:
            return
        self.done = True
        self.row["ended_at"] = time.time()
        self.row["elapsed_s"] = max(0.0, time.monotonic() - self._started_mono)
        self.row["outcome"] = self.outcome or "interrupted"
        self.row["http_status"] = self.status
        if self.timeline is not None:
            self.timeline.finish(self.row)
