"""A fake mlx_vlm.server (httpx transport): health, metrics, settings, cache stats and
chat completions, streaming or not. Shapes follow tests/fixtures/quality/*.json and
the M2 capability survey (pre-split design notes, private)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIX = Path(__file__).parent / "fixtures" / "quality"

USAGE = {
    "prompt_tokens": 131,
    "completion_tokens": 8,
    "total_tokens": 139,
    "prompt_tokens_details": {"cached_tokens": 0},
}
TIMINGS = {
    "prompt_n": 131,
    "cache_n": 0,
    "predicted_n": 8,
    "prompt_ms": 400.0,
    "prompt_per_second": 327.5,
    "predicted_ms": 200.0,
    "predicted_per_second": 40.0,
    "peak_memory": 17.5,
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIX / f"{name}.json").read_text())


class SlowStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float, server: FakeMlxServer) -> None:
        self.chunks = chunks
        self.delay = delay
        self.server = server

    async def __aiter__(self):  # type: ignore[override]
        try:
            for c in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield c
        finally:
            self.server.in_flight = max(0, self.server.in_flight - 1)

    async def aclose(self) -> None:
        self.server.closed_streams += 1


class FakeMlxServer:
    """``reply(payload) -> dict | httpx.Response`` decides a non-streaming answer;
    ``stream_parts`` the streamed content. Every chat request counts in /metrics."""

    def __init__(self) -> None:
        self.health: dict[str, Any] | None = _load("health")
        self.settings = _load("settings")
        self.cache = _load("cache_stats")
        self.version = "0.7.3"
        self.requests: list[dict[str, Any]] = []
        self.requests_started = 0
        self.in_flight = 0
        self.external_per_request = 0  # simulated other clients per chat request
        self.metrics_ok = True
        self.openapi_ok = True
        self.stream_parts = ["Hello", " there"]
        self.stream_delay = 0.0
        self.usage: dict[str, Any] | None = dict(USAGE)
        self.timings: dict[str, Any] | None = dict(TIMINGS)
        self.chat_status = 200
        self.unreachable = False
        self.closed_streams = 0
        self.reply: Callable[[dict[str, Any]], Any] = lambda p: {
            "choices": [{"message": {"role": "assistant", "content": "ready"}}]
        }
        self.on_chat: Callable[[dict[str, Any]], None] | None = None

    def _stream(self) -> list[bytes]:
        out = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}]
        for p in self.stream_parts:
            out.append({"choices": [{"index": 0, "delta": {"content": p}}]})
        out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if self.usage is not None:
            tail: dict[str, Any] = {"choices": [], "usage": self.usage}
            if self.timings is not None:
                tail["timings"] = self.timings
            out.append(tail)
        return [f"data: {json.dumps(c)}\n\n".encode() for c in out] + [b"data: [DONE]\n\n"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("refused")
        path = request.url.path
        if path == "/health":
            if self.health is None:
                raise httpx.ConnectError("not listening")
            return httpx.Response(200, json=self.health)
        if path == "/metrics":
            if not self.metrics_ok:
                return httpx.Response(500)
            summary = {"requests_started": self.requests_started, "in_flight": self.in_flight}
            return httpx.Response(200, json={"summary": summary})
        if path == "/v1/settings":
            return httpx.Response(200, json=self.settings)
        if path == "/v1/cache/stats":
            return httpx.Response(200, json=self.cache)
        if path == "/openapi.json":
            if not self.openapi_ok:
                return httpx.Response(404)
            return httpx.Response(200, json={"info": {"version": self.version}})
        if path == "/v1/chat/completions":
            payload = json.loads(request.content)
            self.requests.append(payload)
            self.requests_started += 1 + self.external_per_request
            if self.on_chat:
                self.on_chat(payload)
            if payload.get("stream"):
                if self.chat_status != 200:
                    return httpx.Response(self.chat_status, text="busy")
                self.in_flight += 1
                return httpx.Response(
                    200,
                    stream=SlowStream(self._stream(), self.stream_delay, self),
                    headers={"content-type": "text/event-stream"},
                )
            answer = self.reply(payload)
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
