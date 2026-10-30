"""Shared OCR transports; authentication stays at the gateway/session boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from .config import Settings
from .ocr import (
    OcrClient,
    OcrDocument,
    OcrError,
    OcrResult,
    document_from_bytes,
    parse_chat,
    read_limited_body,
)
from .service_store import ServiceStore

log = logging.getLogger("local_llm_admin")
Authenticate = Callable[[Request], Awaitable[dict[str, Any] | None]]


async def _native(request: Request, ocr: OcrClient) -> tuple[OcrDocument, str]:
    if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
        raise OcrError(415, "native OCR input must be multipart/form-data")
    raw = await read_limited_body(request, ocr.limits.max_bytes + 64 * 1024)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    bounded = Request(request.scope, receive)
    try:
        async with bounded.form(max_files=1, max_fields=1) as form:
            if set(form) - {"document", "output"} or len(form.getlist("document")) != 1 or len(form.getlist("output")) > 1:
                raise OcrError(400, "provide one document and an optional output mode")
            upload = form.get("document")
            mode = form.get("output", "json")
            if not isinstance(upload, UploadFile) or mode not in ("text", "markdown", "json"):
                raise OcrError(400, "output must be text, markdown or json")
            document = document_from_bytes(
                await upload.read(ocr.limits.max_bytes + 1), upload.content_type or "", ocr.limits,
            )
            return document, str(mode)
    except HTTPException:
        raise OcrError(400, "invalid multipart document") from None


async def _recognize(request: Request, ocr: OcrClient, document: OcrDocument) -> OcrResult:
    async def disconnect() -> None:
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return

    work = asyncio.create_task(ocr.recognize(document))
    watcher = asyncio.create_task(disconnect())
    try:
        done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
        if work not in done:
            work.cancel()
            raise OcrError(499, "OCR client disconnected")
        return await work
    finally:
        watcher.cancel()
        if not work.done():
            work.cancel()
        await asyncio.gather(watcher, work, return_exceptions=True)


def install_ocr_routes(
    app: FastAPI, settings: Settings, ocr: OcrClient,
    reason: Callable[[], str | None], history: ServiceStore | None, *,
    native_path: str, chat_path: str | None = None, source: str,
    authenticate: Authenticate | None = None,
    refused: Callable[[dict[str, Any] | None], bool] | None = None,
) -> None:
    async def record(
        started: float, client: dict[str, Any] | None, code: int | None,
        document: OcrDocument | None, result: OcrResult | None,
    ) -> None:
        if history is None:
            return
        try:
            await asyncio.to_thread(
                history.add_request, ts=time.time(), service="paddleocr-vl",
                model=settings.ocr_model_id, capability="ocr", source=source,
                client_id=str(client["id"]) if client else None,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                status="error" if code else "ok", error_code=code,
                pages=len(result.pages) if result else None,
                input_bytes=len(document.data) if document else None,
                output_chars=len(result.text) if result else None,
            )
        except Exception:
            log.error("recording OCR request metadata failed")

    async def handle(request: Request, chat: bool) -> Response:
        started = time.perf_counter()
        document = None
        result = None
        client = None
        try:
            client = await authenticate(request) if authenticate else None
            if refused and refused(client):
                raise OcrError(401, "invalid or missing API key")
            if chat:
                raw = await read_limited_body(request, ocr.limits.max_body_bytes)
                try:
                    body = json.loads(raw)
                except ValueError:
                    raise OcrError(400, "body is not JSON") from None
                document, mode = parse_chat(body, settings, ocr.limits)
            else:
                document, mode = await _native(request, ocr)
            unavailable = await asyncio.to_thread(reason)
            if unavailable:
                raise OcrError(503, f"OCR service is {unavailable.replace('_', ' ')}")
            result = await _recognize(request, ocr, document)
        except asyncio.CancelledError:
            await record(started, client, 499, document, None)
            raise
        except OcrError as exc:
            await record(started, client, exc.code, document, None)
            kind = {401: "authentication_error", 429: "rate_limit_error",
                    503: "unavailable", 504: "timeout", 502: "bad_gateway"}.get(exc.code, "api_usage_error")
            return JSONResponse(
                {"detail": {"error_type": kind, "message": exc.message}}, status_code=exc.code,
                headers={"Retry-After": "1"} if exc.code == 429 else None,
            )
        await record(started, client, None, document, result)
        headers = {"Cache-Control": "no-store"}
        if chat:
            content = result.model_dump_json() if mode == "json" else result.markdown
            return JSONResponse({
                "id": "ocr-" + uuid.uuid4().hex, "object": "chat.completion",
                "created": int(time.time()), "model": settings.ocr_model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
            }, headers=headers)
        if mode == "json":
            return JSONResponse(result.model_dump(mode="json"), headers=headers)
        return Response(result.text if mode == "text" else result.markdown,
                        media_type="text/plain" if mode == "text" else "text/markdown",
                        headers=headers)

    async def native(request: Request) -> Response:
        return await handle(request, False)

    async def completion(request: Request) -> Response:
        return await handle(request, True)

    app.add_api_route(native_path, native, methods=["POST"])
    if chat_path:
        app.add_api_route(chat_path, completion, methods=["POST"])
