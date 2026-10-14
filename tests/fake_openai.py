"""A fake OpenAI-compatible streaming server (httpx transport) standing in for :8080."""

import asyncio
import json

import httpx


def sse_chunks(parts, usage=None, reasoning=()):
    out = []
    for r in reasoning:
        out.append({"choices": [{"index": 0, "delta": {"reasoning_content": r}}]})
    for p in parts:
        out.append({"choices": [{"index": 0, "delta": {"content": p}}]})
    out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    if usage:
        out.append({"choices": [], "usage": usage})
    return [f"data: {json.dumps(c)}\n\n".encode() for c in out] + [b"data: [DONE]\n\n"]


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0.0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.sent = 0

    async def __aiter__(self):
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.sent += 1
            yield c

    async def aclose(self):
        self.closed = True


class FakeOpenAI:
    def __init__(self, chunks=None, status=200, delay=0.0):
        self.status = status
        self.stream = TrackedStream(chunks or [], delay)
        self.requests = []

    def handler(self, request):
        self.requests.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, text="busy")
        return httpx.Response(
            200, stream=self.stream, headers={"content-type": "text/event-stream"}
        )

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)
