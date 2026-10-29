"""Stable OpenAI entry point for the currently owned engine; never starts an engine."""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator, Callable

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .backends import BackendRegistry, BackendUnavailable
from .chat import ACTIVE_MODEL_ID
from .config import Settings

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_INFLIGHT = 2


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "invalid_request_error" if status < 500 else "server_error",
                "code": code,
            }
        },
        status_code=status,
    )


def _frame(frame: bytes, model: str) -> bytes:
    lines = frame.splitlines()
    data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
    if not data or data == b"[DONE]":
        return frame + b"\n\n"
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("invalid upstream event")
    if "model" in document:
        document["model"] = model
    return (
        b"\n".join(
            [line for line in lines if not line.startswith(b"data:")]
            + [b"data: " + json.dumps(document).encode()]
        )
        + b"\n\n"
    )


async def _events(response: httpx.Response, model: str) -> AsyncIterator[bytes]:
    pending = bytearray()
    lines: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        pending.extend(chunk)
        while (end := pending.find(b"\n")) >= 0:
            line = bytes(pending[:end]).removesuffix(b"\r")
            del pending[: end + 1]
            size += len(line) + 1
            if size > MAX_EVENT_BYTES:
                raise ValueError("upstream event too large")
            if line:
                lines.append(line)
            else:
                yield _frame(b"\n".join(lines), model)
                lines, size = [], 0
        if size + len(pending) > MAX_EVENT_BYTES:
            raise ValueError("upstream event too large")
    if pending or lines:
        yield _frame(b"\n".join([*lines, bytes(pending)]), model)


def create_inference_app(
    settings: Settings,
    registry: BackendRegistry,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    busy: Callable[[], bool] = lambda: False,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    inflight = 0

    def authorized(request: Request) -> Response | None:
        key = settings.inference_api_key.get_secret_value()
        if not key:
            return _error(503, "disabled", "Shared inference is not configured")
        expected = ("Bearer " + key).encode()
        actual = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(actual, expected):
            return _error(401, "invalid_api_key", "Invalid or missing API key")
        return None

    async def unavailable() -> bool:
        return await asyncio.to_thread(busy)

    @app.get("/models")
    async def models(request: Request) -> Response:
        if (refusal := authorized(request)) is not None:
            return refusal
        if await unavailable():
            return _error(503, "unavailable", "An engine operation is in progress")
        try:
            descriptor = await registry.resolve_inference()
        except (BackendUnavailable, ValueError, OSError):
            return _error(503, "unavailable", "No verified ready engine is available")
        ids = dict.fromkeys([settings.inference_model_id, ACTIVE_MODEL_ID, descriptor.target.model])
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": name, "object": "model", "owned_by": "local"} for name in ids],
            }
        )

    @app.post("/chat/completions")
    @app.post("/completions")
    async def completion(request: Request) -> Response:
        nonlocal inflight
        if (refusal := authorized(request)) is not None:
            return refusal
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return _error(413, "request_too_large", "Request body is too large")
            body.extend(chunk)
        try:
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("model"), str)
                or not payload["model"]
                or type(payload.get("stream", False)) is not bool
            ):
                raise ValueError("invalid body")
        except (ValueError, UnicodeError):
            return _error(400, "invalid_request", "Expected an OpenAI request with a model ID")
        if await unavailable():
            return _error(503, "unavailable", "An engine operation is in progress")
        if inflight >= MAX_INFLIGHT:
            return _error(429, "busy", "Shared inference is busy")
        inflight += 1
        client: httpx.AsyncClient | None = None
        response: httpx.Response | None = None
        released = False

        async def close() -> None:
            nonlocal inflight, released
            if released:
                return
            released = True
            # Disconnect cancellation must still close the native generation stream.
            with anyio.CancelScope(shield=True):
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    try:
                        if client is not None:
                            await client.aclose()
                    finally:
                        inflight -= 1

        streaming = False
        try:
            try:
                descriptor = await registry.resolve_inference()
            except (BackendUnavailable, ValueError, OSError):
                return _error(503, "unavailable", "No verified ready engine is available")
            model = payload["model"]
            if model not in {settings.inference_model_id, ACTIVE_MODEL_ID, descriptor.target.model}:
                return _error(404, "model_not_found", "Model is not served by the selected engine")
            # Never pass a caller-selected model to MLX: that could load foreign weights.
            payload["model"] = descriptor.target.model
            headers = (
                {"Authorization": "Bearer " + descriptor.target.api_key}
                if descriptor.target.api_key
                else {}
            )
            client = httpx.AsyncClient(
                base_url=descriptor.target.base_url,
                headers=headers,
                timeout=httpx.Timeout(300.0, connect=5.0, pool=5.0),
                transport=transport,
                trust_env=False,
            )
            if await unavailable():
                return _error(503, "unavailable", "An engine operation is in progress")
            upstream_path = (
                "/v1/chat/completions"
                if request.url.path.endswith("/chat/completions")
                else "/v1/completions"
            )
            response = await client.send(
                client.build_request("POST", upstream_path, json=payload), stream=True
            )
            if response.status_code != 200:
                return _error(
                    response.status_code, "upstream_error", "Selected engine refused the request"
                )
            if payload.get("stream", False):
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    return _error(
                        502, "upstream_error", "Selected engine returned an invalid stream"
                    )

                async def stream() -> AsyncIterator[bytes]:
                    assert response is not None
                    try:
                        async for event in _events(response, model):
                            yield event
                    except (httpx.HTTPError, ValueError):
                        yield (
                            b"data: "
                            + json.dumps(
                                {
                                    "error": {
                                        "message": "Selected engine stream failed",
                                        "type": "server_error",
                                        "code": "upstream_error",
                                    }
                                }
                            ).encode()
                            + b"\n\ndata: [DONE]\n\n"
                        )
                    finally:
                        await close()

                streaming = True
                return StreamingResponse(
                    stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                    background=BackgroundTask(close),
                )
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_BODY_BYTES:
                    return _error(502, "upstream_error", "Selected engine response is too large")
                content.extend(chunk)
            document = json.loads(content)
            if not isinstance(document, dict):
                raise ValueError("invalid upstream response")
            if "model" in document:
                document["model"] = model
            return JSONResponse(document)
        except (httpx.HTTPError, ValueError):
            return _error(502, "upstream_error", "Selected engine is unavailable")
        finally:
            if not streaming:
                await close()

    return app
