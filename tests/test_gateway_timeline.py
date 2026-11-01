"""The gateway ledger records bounded metadata, never request or response bodies."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from queue import Full
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import ClientDisconnect

from local_llm_admin.backend_types import InferenceDescriptor
from local_llm_admin.chat import ChatTarget
from local_llm_admin.config import Settings
from local_llm_admin.gateway_timeline import GatewayTimeline, RequestTrace
from local_llm_admin.inference_gateway import create_inference_app

AUTH = {"Authorization": "Bearer test-gateway-key"}


def _gateway(path: Path, handler: Any, *, descriptor: InferenceDescriptor | None = None
             ) -> tuple[Any, GatewayTimeline, Any]:
    timeline = GatewayTimeline(path)
    registry = SimpleNamespace(resolve_inference=AsyncMock(return_value=descriptor or
        InferenceDescriptor(ChatTarget("http://native.test", "native-model"),
                            identity={"engine": "mlx", "model": "native-model"})))
    app = create_inference_app(Settings(inference_api_key="test-gateway-key"), registry,
                               transport=httpx.MockTransport(handler), timeline=timeline)
    return app, timeline, registry


async def test_nonstream_completion_has_stable_identity_and_diagnostic_usage(tmp_path: Path) -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "native-model", "choices": [],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    app, timeline, registry = _gateway(tmp_path / "timeline.sqlite", serve)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/chat/completions", headers={**AUTH,
            "X-Request-ID": "caller-supplied-private-id"}, json={"model": "local-model",
            "messages": [{"role": "user", "content": "private prompt"}]})
    registry.resolve_inference.return_value = InferenceDescriptor(
        ChatTarget("http://other.test", "different-model"), identity={"engine": "strata"})
    assert timeline.flush()
    rows = timeline.list_requests(0, 9999999999)["requests"]
    assert response.status_code == 200 and len(rows) == 1
    row = rows[0]
    assert response.headers["X-Request-ID"] == row["request_id"]
    assert row["request_id"] != "caller-supplied-private-id"
    assert row["outcome"] == "completed" and row["backend"] == "mlx"
    assert row["requested_model"] == "local-model" and row["resolved_model"] == "native-model"
    assert row["prompt_tokens"] == 3 and row["completion_tokens"] == 2
    assert row["usage_source"] == "upstream_response" and row["usage_completeness"] == "complete"
    assert row["headers_at"] is not None and row["first_content_at"] is None
    assert row["time_to_headers_s"] is not None and row["elapsed_s"] >= 0
    assert "private prompt" not in str(row) and "caller-supplied-private-id" not in str(row)
    timeline.close()


async def test_native_path_is_sanitized_and_profile_fingerprint_is_retained(tmp_path: Path) -> None:
    descriptor = InferenceDescriptor(
        ChatTarget("http://native.test", "/Users/you/model"),
        identity={"engine": "splash", "model": "/Users/you/model",
                  "profile_fingerprint": "a" * 20,
                  "generation_fingerprint": "b" * 64},
    )
    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(
        200, json={"model": "/Users/you/model", "choices": []}), descriptor=descriptor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "/Users/you/model"})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 200
    assert row["requested_model"] == "model" and row["resolved_model"] == "model"
    assert row["profile_fingerprint"] == "a" * 20
    assert row["generation_fingerprint"] == "b" * 64
    assert "/Users/you" not in str(row)
    timeline.close()


async def test_rejection_before_resolution_has_no_invented_backend(tmp_path: Path) -> None:
    app, timeline, registry = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model", "stream": "bad"})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 400 and response.headers["X-Request-ID"] == row["request_id"]
    assert row["outcome"] == "rejected" and row["backend"] is None
    assert row["requested_model"] is None and row["admitted_at"] is None
    registry.resolve_inference.assert_not_awaited()
    timeline.close()


async def test_unknown_public_model_keeps_resolved_backend_but_not_caller_text(
    tmp_path: Path,
) -> None:
    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite",
                                lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "private-unserved-model"})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 404 and row["outcome"] == "rejected"
    assert row["backend"] == "mlx" and row["resolved_model"] == "native-model"
    assert row["requested_model"] is None and "private-unserved" not in str(row)
    timeline.close()


async def test_unauthorized_request_has_safe_rejection_record(tmp_path: Path) -> None:
    app, timeline, registry = _gateway(tmp_path / "timeline.sqlite",
                                       lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers={
            "Authorization": "Bearer private-invalid-key", "X-Request-ID": "private-hint"},
            content=b"private body")
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 401 and response.headers["X-Request-ID"] == row["request_id"]
    assert row["outcome"] == "rejected" and row["backend"] is None
    assert "private" not in str(row)
    registry.resolve_inference.assert_not_awaited()
    timeline.close()


async def test_upload_disconnect_is_terminal_before_admission(tmp_path: Path) -> None:
    app, timeline, registry = _gateway(tmp_path / "timeline.sqlite",
                                       lambda r: httpx.Response(500))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/completions",
             "raw_path": b"/completions", "query_string": b"",
             "headers": [(b"authorization", b"Bearer test-gateway-key")],
             "server": ("gateway.test", 80), "client": ("10.0.0.9", 1)}
    messages = iter([{"type": "http.request", "body": b"{", "more_body": True},
                     {"type": "http.disconnect"}])

    async def receive() -> Any:
        return next(messages)

    async def send(_: Any) -> None:
        pass

    with pytest.raises(ClientDisconnect):
        await app(scope, receive, send)
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert row["outcome"] == "disconnected" and row["admitted_at"] is None
    registry.resolve_inference.assert_not_awaited()
    timeline.close()


async def test_stream_distinguishes_reasoning_and_tool_deltas_from_content(tmp_path: Path) -> None:
    frames = [
        {"choices": [{"delta": {"reasoning_content": "thought"}}]},
        {"choices": [{"delta": {"tool_calls": [{"id": "private-tool-id"}]}}]},
        {"choices": [{"delta": {"content": ""}}]},
        {"choices": [{"delta": {"content": "answer"}}]},
        {"choices": [], "usage": {"completion_tokens": 4}},
    ]
    body = b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
    body += b"data: [DONE]\n\n"
    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model", "stream": True})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 200 and row["outcome"] == "completed"
    assert row["first_event_at"] is not None and row["first_content_at"] is not None
    assert row["first_event_at"] <= row["first_content_at"]
    assert row["time_to_first_event_s"] <= row["time_to_first_content_s"]
    assert row["prompt_tokens"] is None and row["usage_completeness"] == "partial"
    assert "private-tool-id" not in str(row) and "thought" not in str(row)
    timeline.close()


async def test_upstream_failure_with_usage_remains_error(tmp_path: Path) -> None:
    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(
        503, json={"error": {"message": "secret error"},
                   "usage": {"prompt_tokens": 5, "completion_tokens": 0}}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model"})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 503 and row["outcome"] == "upstream_error"
    assert row["prompt_tokens"] == 5 and "secret error" not in str(row)
    timeline.close()


async def test_hung_error_body_cannot_delay_gateway_refusal(tmp_path: Path) -> None:
    class Hung(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            await asyncio.Event().wait()
            yield b"never"

        async def aclose(self) -> None:
            pass

    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(
        503, stream=Hung(), headers={"content-length": "50"}))
    started = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model"})
    assert time.perf_counter() - started < 1.0
    assert response.status_code == 503
    assert timeline.flush()
    assert timeline.list_requests(0, 9999999999)["requests"][0]["outcome"] == "upstream_error"
    timeline.close()


async def test_connect_timeout_records_timeout_without_headers(tmp_path: Path) -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("private network detail")

    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", timeout)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model"})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 504 and row["outcome"] == "timeout"
    assert row["dispatched_at"] is not None and row["headers_at"] is None
    assert "private network" not in str(row)
    timeline.close()


def test_store_cursor_retention_and_reconciliation(tmp_path: Path) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")
    for i in range(3):
        timeline.begin({"request_id": f"test-{i}", "started": float(i + 1),
                        "route": "completions"})
        timeline.finish({"request_id": f"test-{i}", "started": float(i + 1),
                         "route": "completions", "outcome": "completed", "ended_at": float(i + 2)})
    timeline.begin({"request_id": "unfinished", "started": 4.0, "route": "completions"})
    assert timeline.flush()
    page = timeline.list_requests(0, 10, limit=2)
    timeline.begin({"request_id": "later", "started": 5.0, "route": "completions"})
    timeline.finish({"request_id": "later", "started": 5.0, "route": "completions",
                     "outcome": "completed", "ended_at": 6.0})
    assert timeline.flush()
    older = timeline.list_requests(0, 10, limit=10, before=page["next"])
    assert "later" not in {r["request_id"] for r in older["requests"]}
    timeline.close()
    reopened = GatewayTimeline(tmp_path / "timeline.sqlite")
    row = reopened.get("unfinished")
    assert row is not None and row["outcome"] == "interrupted"
    assert reopened.prune(now=100 * 86400, retention_days=90, batch=2) == 2
    assert reopened.get("unfinished") is not None
    reopened.close()


@pytest.mark.parametrize("mode,expected", [("timeout", "timeout"),
                                             ("malformed", "upstream_error")])
async def test_stream_terminal_failure_is_not_recorded_as_success(
    tmp_path: Path, mode: str, expected: str,
) -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield b'data: {"choices": [{"delta": {"reasoning_content": "private"}}]}\n\n'
            if mode == "timeout":
                raise httpx.ReadTimeout("private upstream timeout")
            yield b"data: {invalid}\n\n"

        async def aclose(self) -> None:
            pass

    app, timeline, _ = _gateway(tmp_path / "timeline.sqlite", lambda r: httpx.Response(
        200, stream=Broken(), headers={"content-type": "text/event-stream"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                 base_url="http://gateway.test") as client:
        response = await client.post("/completions", headers=AUTH,
                                     json={"model": "local-model", "stream": True})
    assert timeline.flush()
    row = timeline.list_requests(0, 9999999999)["requests"][0]
    assert response.status_code == 200 and row["outcome"] == expected
    assert row["first_event_at"] is not None and row["first_content_at"] is None
    assert "private" not in str(row)
    timeline.close()


def test_full_writer_queue_reports_drops_without_waiting(tmp_path: Path,
                                                          monkeypatch: Any) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")

    def full(_: Any) -> None:
        raise Full

    monkeypatch.setattr(timeline._queue, "put_nowait", full)
    timeline.begin({"request_id": "dropped", "started": 1.0, "route": "completions"})
    timeline.finish({"request_id": "dropped", "started": 1.0,
                     "route": "completions", "outcome": "completed"})
    assert timeline.health()["dropped_writes"] == 2
    monkeypatch.undo()
    timeline.close()


def test_retention_never_deletes_active_rows(tmp_path: Path) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")
    timeline.begin({"request_id": "active", "started": 1.0, "route": "completions"})
    assert timeline.flush()
    assert timeline.prune(now=100 * 86400, retention_days=90) == 0
    assert timeline.get("active") is not None
    timeline.close()


async def test_locked_database_does_not_delay_inference(tmp_path: Path) -> None:
    path = tmp_path / "timeline.sqlite"
    app, timeline, _ = _gateway(path, lambda r: httpx.Response(
        200, json={"model": "native-model", "choices": []}))
    lock = sqlite3.connect(path)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        started = time.perf_counter()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://gateway.test") as client:
            response = await client.post("/completions", headers=AUTH,
                                         json={"model": "local-model"})
        elapsed = time.perf_counter() - started
        assert response.status_code == 200 and elapsed < 1.0
        health = timeline.health()
        assert health["pending_writes"] >= 1 and health["failed_writes"] == 0
    finally:
        lock.rollback()
        lock.close()
    assert timeline.flush()
    assert timeline.list_requests(0, 9999999999)["requests"][0]["outcome"] == "completed"
    timeline.close()


def test_failed_writer_is_counted_without_exposing_data(tmp_path: Path) -> None:
    path = tmp_path / "timeline.sqlite"
    timeline = GatewayTimeline(path)
    timeline.path = tmp_path / "missing" / "timeline.sqlite"
    timeline.begin({"request_id": "failed", "started": 1.0, "route": "completions"})
    assert timeline.flush()
    timeline.path = path
    assert timeline.health()["failed_writes"] == 1
    timeline.close()


def test_trace_ignores_untrusted_usage_and_finalizes_only_once(tmp_path: Path) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")
    trace = RequestTrace(timeline, "safe-id", "completions")
    trace.observe_document({"error": {"message": "private failure"}, "choices": [
        None, {"text": "answer"}], "usage": {"prompt_tokens": True,
                                            "completion_tokens": -3}})
    trace.terminal("upstream_error", 503)
    trace.terminal("completed", 200)
    trace.finalize()
    trace.finalize()
    assert timeline.flush()
    row = timeline.get("safe-id")
    assert row is not None and row["outcome"] == "upstream_error" and row["http_status"] == 503
    assert row["first_content_at"] is not None and row["prompt_tokens"] is None
    assert row["usage_source"] is None and "private failure" not in str(row)
    timeline.close()


def test_exact_store_filters_and_no_duplicate_terminal_update(tmp_path: Path) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")
    row = {"request_id": "one", "started": 1.0, "route": "completions",
           "backend": "mlx", "outcome": "completed", "ended_at": 2.0}
    timeline.begin(row)
    timeline.finish(row)
    timeline.finish({**row, "backend": "strata", "outcome": "upstream_error"})
    assert timeline.flush()
    stored = timeline.get("one")
    assert stored is not None and stored["backend"] == "mlx"
    for filters in ({"backend": "mlx"}, {"outcome": "completed"},
                    {"request_id": "one"}):
        assert len(timeline.list_requests(0, 10, **filters)["requests"]) == 1
    for filters in ({"backend": "strata"}, {"outcome": "upstream_error"},
                    {"request_id": "other"}):
        assert timeline.list_requests(0, 10, **filters)["requests"] == []
    timeline.close()


def test_full_queue_makes_flush_fail_without_blocking(tmp_path: Path, monkeypatch: Any) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")

    def full(_: Any) -> None:
        raise Full

    monkeypatch.setattr(timeline._queue, "put_nowait", full)
    assert timeline.flush() is False
    monkeypatch.undo()
    timeline.close()


def test_close_does_not_wait_for_a_full_writer_queue(tmp_path: Path, monkeypatch: Any) -> None:
    timeline = GatewayTimeline(tmp_path / "timeline.sqlite")
    monkeypatch.setattr(timeline, "flush", lambda: True)

    def full(_: Any) -> None:
        raise Full

    monkeypatch.setattr(timeline._queue, "put_nowait", full)
    timeline.close()
    monkeypatch.undo()
    timeline.close()
