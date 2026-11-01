"""Stable OpenAI entry point for the currently owned engine; never starts an engine."""

from __future__ import annotations

import asyncio
import hmac
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from .admission import Admission, AdmissionRefused
from .backends import BackendRegistry, BackendUnavailable
from .chat import ACTIVE_MODEL_ID
from .config import Settings
from .gateway_timeline import GatewayTimeline, Outcome, RequestTrace

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_INFLIGHT = 2  # compatibility default; configured per backend in Settings


def _error(status: int, code: str, message: str, *, retry_after: bool = False) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "invalid_request_error" if status < 500 else "server_error",
                "code": code,
            }
        },
        status_code=status,
        headers={"Retry-After": "1"} if retry_after else None,
    )


async def _upstream_refusal(
    response: httpx.Response,
    on_document: Callable[[dict[str, Any]], None] | None = None,
) -> JSONResponse:
    message = "Selected engine refused the request"
    body = bytearray()
    try:
        async with asyncio.timeout(0.2):
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 8192:
                    break
                body.extend(chunk)
            else:
                document = json.loads(body)
                if isinstance(document, dict):
                    if on_document is not None:
                        on_document(document)
                    error = document.get("error")
                    reason = error.get("message") if isinstance(error, dict) else None
                    if (
                        response.status_code == 400
                        and isinstance(reason, str)
                        and reason.startswith("this server was started without the vision encoder")
                        and reason.endswith("so it cannot read images")
                    ):
                        message = (
                            "The selected engine was started without a vision encoder and cannot "
                            "process images. Send text-only input or select an engine with vision support."
                        )
    except (httpx.HTTPError, ValueError, TimeoutError):
        pass
    return _error(response.status_code, "upstream_error", message)


def _frame(
    frame: bytes, model: str, on_event: Callable[[dict[str, Any]], None] | None = None,
) -> bytes:
    lines = frame.splitlines()
    data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
    if not data or data == b"[DONE]":
        return frame + b"\n\n"
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("invalid upstream event")
    if on_event is not None:
        on_event(document)
    if "model" in document:
        document["model"] = model
    return (
        b"\n".join(
            [line for line in lines if not line.startswith(b"data:")]
            + [b"data: " + json.dumps(document).encode()]
        )
        + b"\n\n"
    )


async def _events(
    response: httpx.Response, model: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[bytes]:
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
                yield _frame(b"\n".join(lines), model, on_event)
                lines, size = [], 0
        if size + len(pending) > MAX_EVENT_BYTES:
            raise ValueError("upstream event too large")
    if pending or lines:
        yield _frame(b"\n".join([*lines, bytes(pending)]), model, on_event)


def create_inference_app(
    settings: Settings,
    registry: BackendRegistry,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    busy: Callable[[], bool] = lambda: False,
    admission: Admission | None = None,
    timeline: GatewayTimeline | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = admission or Admission({
        "mlx": settings.inference_max_inflight_mlx,
        "strata": settings.inference_max_inflight_strata,
        "splash": settings.inference_max_inflight_splash,
    })

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
        if gate.snapshot()["state"] != "accepting":
            return _error(503, "unavailable", "An engine operation is in progress", retry_after=True)
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
        request_id = uuid.uuid4().hex
        route = "chat_completions" if request.url.path.endswith("/chat/completions") else "completions"
        trace = RequestTrace(timeline, request_id, route)

        def reply(response: Response, outcome: Outcome | None = None) -> Response:
            if outcome is not None:
                trace.terminal(outcome, response.status_code)
            response.headers["X-Request-ID"] = request_id
            return response

        if (refusal := authorized(request)) is not None:
            result = reply(refusal, "rejected")
            trace.finalize()
            return result

        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    result = reply(_error(413, "request_too_large", "Request body is too large"),
                                   "rejected")
                    trace.finalize()
                    return result
                body.extend(chunk)
        except (ClientDisconnect, asyncio.CancelledError):
            trace.terminal("disconnected", None)
            trace.finalize()
            raise
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
            result = reply(_error(400, "invalid_request", "Expected an OpenAI request with a model ID"),
                           "rejected")
            trace.finalize()
            return result
        try:
            lease = gate.acquire(busy=await unavailable(), request_id=request_id)
            trace.mark("admitted_at")
        except AdmissionRefused as exc:
            result = reply(_error(exc.status, "busy" if exc.status == 429 else "unavailable",
                                  exc.message, retry_after=exc.status == 503), "rejected")
            trace.finalize()
            return result
        client: httpx.AsyncClient | None = None
        response: httpx.Response | None = None
        released = False

        async def close() -> None:
            nonlocal released
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
                        gate.release(lease)
                        trace.finalize()

        streaming = False
        try:
            try:
                descriptor = await registry.resolve_inference()
            except (BackendUnavailable, ValueError, OSError):
                return reply(_error(503, "unavailable", "No verified ready engine is available"),
                             "rejected")
            try:
                gate.bind(lease, str(descriptor.identity.get("engine", "mlx")))
            except AdmissionRefused as exc:
                return reply(_error(exc.status, "busy", exc.message), "rejected")
            model = payload["model"]
            accepted = model in {settings.inference_model_id, ACTIVE_MODEL_ID,
                                 descriptor.target.model}
            trace.identity(descriptor, model if accepted else None)
            if not accepted:
                return reply(_error(404, "model_not_found", "Model is not served by the selected engine"),
                             "rejected")
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
                return reply(_error(503, "unavailable", "An engine operation is in progress"),
                             "rejected")
            upstream_path = (
                "/v1/chat/completions"
                if request.url.path.endswith("/chat/completions")
                else "/v1/completions"
            )
            trace.mark("dispatched_at")
            response = await client.send(
                client.build_request("POST", upstream_path, json=payload), stream=True
            )
            trace.mark("headers_at")
            if response.status_code != 200:
                return reply(await _upstream_refusal(response, trace.observe_usage),
                             "upstream_error")
            if payload.get("stream", False):
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    return reply(_error(502, "upstream_error", "Selected engine returned an invalid stream"),
                                 "upstream_error")

                async def stream() -> AsyncIterator[bytes]:
                    assert response is not None
                    try:
                        async for event in _events(response, model, trace.observe_document):
                            yield event
                        trace.terminal("upstream_error" if trace.stream_error else "completed", 200)
                    except (asyncio.CancelledError, GeneratorExit):
                        gate.mark_native_cleanup_unverified()
                        trace.terminal("disconnected", 200)
                        raise
                    except httpx.TimeoutException:
                        gate.mark_native_cleanup_unverified()
                        trace.terminal("timeout", 200)
                        yield (
                            b'data: {"error":{"message":"Selected engine stream timed out",'
                            b'"type":"server_error","code":"upstream_timeout"}}\n\n'
                            b"data: [DONE]\n\n"
                        )
                    except (httpx.HTTPError, ValueError):
                        gate.mark_native_cleanup_unverified()
                        trace.terminal("upstream_error", 200)
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
                return reply(StreamingResponse(
                    stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                    background=BackgroundTask(close),
                ))
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_BODY_BYTES:
                    return reply(_error(502, "upstream_error", "Selected engine response is too large"),
                                 "upstream_error")
                content.extend(chunk)
            document = json.loads(content)
            if not isinstance(document, dict):
                raise ValueError("invalid upstream response")
            if "model" in document:
                document["model"] = model
            trace.observe_usage(document)
            return reply(JSONResponse(document), "completed")
        except asyncio.CancelledError:
            gate.mark_native_cleanup_unverified()
            trace.terminal("disconnected", None)
            raise
        except httpx.TimeoutException:
            return reply(_error(504, "upstream_timeout", "Selected engine timed out"), "timeout")
        except (httpx.HTTPError, ValueError):
            return reply(_error(502, "upstream_error", "Selected engine is unavailable"),
                         "upstream_error")
        finally:
            if not streaming:
                await close()

    return app
