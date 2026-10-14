"""LAN gateway for the sidecars (M6 Phase 5, M7, M8): OpenJev reads, TTS speech, and embeddings/rerank (M8).

A separate small app on ``decision_gateway_port`` (default 8082): per-client keys
(optional unless ``decision_gateway_require_key``), OpenJev's request and response bodies unchanged, in-flight limits, and one usage row per
request (counts and types only; never the TTS text). It has no session middleware, templates or admin
routes, so nothing here can reach the admin's control endpoints. It never starts the
sidecar.
"""

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

from . import embed as embed_mod
from . import tts as tts_mod
from .config import Settings
from .decision import MAX_BODY_BYTES, DecisionClient, DecisionError, validate_request
from .decision_keys import ClientKeys
from .decision_store import DecisionStore, request_summary
from .embed import EmbedClient, EmbedError, encode_base64, texts_of, validate_embed, validate_rerank
from .embed_store import EmbedStore
from .sidecars import SidecarRegistry
from .tts import TtsClient, TtsError, text_weight, validate_speech
from .tts_store import TtsStore

log = logging.getLogger("local_llm_admin")


def _error(
    code: int,
    error_type: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Response:
    """OpenJev's error shape, so Jev clients parse gateway refusals like upstream ones."""
    return JSONResponse(
        {"detail": {"error_type": error_type, "message": message, **(extra or {})}},
        status_code=code,
        headers=headers,
    )


def create_gateway_app(
    settings: Settings,
    decision: DecisionClient,
    keys: ClientKeys,
    history: DecisionStore,
    *,
    registry: SidecarRegistry | None = None,
    tts: TtsClient | None = None,
    tts_history: TtsStore | None = None,
    embed: EmbedClient | None = None,
    embed_history: EmbedStore | None = None,
    embed_reason: Callable[[], str | None] | None = None,
) -> FastAPI:
    registry = registry or SidecarRegistry(settings)
    app = FastAPI(title="sidecar gateway", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.inflight = 0
    app.state.per_client = {}

    async def record(
        t0: float,
        client: dict[str, Any] | None,
        summary: dict[str, Any],
        remote: str | None,
        status: str,
        code: int | None,
        tokens: int | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                history.add_request,
                ts=time.time(),
                source="gateway",
                summary=summary,
                input_tokens=tokens,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                model_ms=None,
                status=status,
                error_code=code,
                client_id=str(client["id"]) if client else None,
                remote_addr=remote,
            )
        except Exception:  # a failed usage row must not fail the read
            log.exception("decision gateway: recording usage failed")

    async def authenticate(request: Request) -> dict[str, Any] | None:
        client: dict[str, Any] | None = await asyncio.to_thread(
            keys.verify, request.headers.get("authorization")
        )
        return client

    def refused(client: dict[str, Any] | None) -> bool:
        """Without a valid key, a request is refused only when keys are required."""
        return client is None and settings.decision_gateway_require_key

    async def running_name() -> str | None:
        running = await asyncio.to_thread(registry.running)
        return running[0].name if running else None

    async def stopped(what: str) -> Response:
        return _error(
            503, "unavailable", f"{what} is stopped", extra={"running": await running_name()}
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def embed_up() -> bool:
        return embed is not None and embed_reason is not None and (
            await asyncio.to_thread(embed_reason)
        ) is None

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        if refused(await authenticate(request)):
            return _error(401, "authentication_error", "invalid or missing API key")
        name = await running_name()
        extra = (
            [
                {"id": settings.embed_model_id, "object": "model", "owned_by": "local"},
                {"id": settings.rerank_model_id, "object": "model", "owned_by": "local"},
            ]
            if await embed_up()
            else []
        )
        if name is None:
            if extra:
                return JSONResponse({"object": "list", "data": extra})
            return _error(503, "unavailable", "no sidecar is running", extra={"running": None})
        if name == "qwen-tts":
            tts_entry = {"id": settings.tts_model_id, "object": "model", "owned_by": "local"}
            return JSONResponse({"object": "list", "data": [tts_entry, *extra]})
        # OpenJev's own response passes through unchanged so Jev clients keep parsing it
        try:
            resp = await decision.models()
        except DecisionError as exc:
            return _error(exc.code, "unavailable", exc.message)
        return Response(resp.content, resp.status_code, media_type="application/json")

    @app.post("/v1/systemone")
    async def systemone(request: Request) -> Response:
        t0 = time.perf_counter()
        remote = request.client.host if request.client else None
        empty = request_summary(None)
        client = await authenticate(request)
        if refused(client):
            await record(t0, None, empty, remote, "error", 401)
            return _error(401, "authentication_error", "invalid or missing API key")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await record(t0, client, empty, remote, "error", 413)
            return _error(413, "api_usage_error", f"request is larger than {MAX_BODY_BYTES} bytes")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            await record(t0, client, empty, remote, "error", 413)
            return _error(413, "api_usage_error", f"request is larger than {MAX_BODY_BYTES} bytes")
        try:
            body = json.loads(raw)
        except ValueError:
            await record(t0, client, empty, remote, "error", 400)
            return _error(400, "api_usage_error", "body is not JSON")
        summary = request_summary(body)
        try:
            payload = validate_request(body)
        except DecisionError as exc:
            await record(t0, client, summary, remote, "error", 400)
            return _error(400, "api_usage_error", exc.message)
        if not (await asyncio.to_thread(decision.running)).running:
            await record(t0, client, summary, remote, "error", 503)
            return await stopped("decision sidecar")
        # keyless requests share an in-flight slot count per remote address
        cid = client["id"] if client else f"addr:{remote}"
        per = app.state.per_client
        if (
            app.state.inflight >= settings.decision_gateway_max_inflight
            or per.get(cid, 0) >= settings.decision_gateway_client_inflight
        ):
            await record(t0, client, summary, remote, "error", 429)
            return _error(
                429, "rate_limit_error", "too many requests in flight", {"Retry-After": "1"}
            )
        app.state.inflight += 1
        per[cid] = per.get(cid, 0) + 1
        try:
            resp = await decision.forward(payload)
        except DecisionError as exc:
            await record(t0, client, summary, remote, "error", exc.code)
            kind = "timeout" if exc.code == 504 else "unavailable"
            return _error(exc.code, kind, exc.message)
        finally:
            app.state.inflight -= 1
            per[cid] -= 1
            if per[cid] <= 0:
                del per[cid]
        try:
            data = resp.json()
        except ValueError:
            await record(t0, client, summary, remote, "error", 502)
            return _error(502, "bad_gateway", "the decision sidecar returned a malformed response")
        ok = resp.status_code == 200
        usage = data.get("usage") if isinstance(data, dict) else None
        tokens = usage.get("input_tokens") if ok and isinstance(usage, dict) else None
        await record(
            t0,
            client,
            summary,
            remote,
            "ok" if ok else "error",
            None if ok else resp.status_code,
            tokens if isinstance(tokens, int) else None,
        )
        headers = {}
        if "server-timing" in resp.headers:
            headers["Server-Timing"] = resp.headers["server-timing"]
        return JSONResponse(data, status_code=resp.status_code, headers=headers)

    if tts is not None:
        _speech_route(app, settings, tts, tts_history, authenticate, refused, running_name, stopped)

    if embed is not None and embed_reason is not None:
        _embed_routes(app, settings, embed, embed_history, authenticate, refused, embed_reason)
    return app


_ERROR_KINDS = {504: "timeout", 502: "bad_gateway", 429: "rate_limit_error", 400: "api_usage_error"}


def _speech_route(
    app: FastAPI,
    settings: Settings,
    tts: TtsClient,
    tts_history: TtsStore | None,
    authenticate: Callable[[Request], Awaitable[dict[str, Any] | None]],
    refused: Callable[[dict[str, Any] | None], bool],
    running_name: Callable[[], Awaitable[str | None]],
    stopped: Callable[[str], Awaitable[Response]],
) -> None:
    """``POST /v1/audio/speech`` (M7): OpenAI's speech route, served only while the TTS
    sidecar runs. It never starts or swaps sidecars; one request at a time (shared with
    the admin page), and one usage row per request without the text."""

    async def record_tts(
        t0: float,
        client: dict[str, Any] | None,
        remote: str | None,
        status: str,
        code: int | None,
        req: dict[str, Any] | None = None,
        chunks: int | None = None,
        audio_s: float | None = None,
    ) -> None:
        if tts_history is None:
            return
        try:
            await asyncio.to_thread(
                tts_history.add_request,
                ts=time.time(),
                source="gateway",
                chars=len(req["input"]) if req else None,
                chunks=chunks,
                voice=req.get("voice") if req else None,
                fmt=req.get("response_format") if req else None,
                audio_s=audio_s,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                status=status,
                error_code=code,
                client_id=str(client["id"]) if client else None,
                remote_addr=remote,
            )
        except Exception:  # a failed usage row must not fail the request
            log.exception("gateway: recording TTS usage failed")

    too_big = f"request is larger than {tts_mod.MAX_BODY_BYTES} bytes"

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        t0 = time.perf_counter()
        remote = request.client.host if request.client else None
        client = await authenticate(request)
        if refused(client):
            await record_tts(t0, None, remote, "error", 401)
            return _error(401, "authentication_error", "invalid or missing API key")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > tts_mod.MAX_BODY_BYTES:
            await record_tts(t0, client, remote, "error", 413)
            return _error(413, "api_usage_error", too_big)
        raw = await request.body()
        if len(raw) > tts_mod.MAX_BODY_BYTES:
            await record_tts(t0, client, remote, "error", 413)
            return _error(413, "api_usage_error", too_big)
        try:
            body = json.loads(raw)
        except ValueError:
            await record_tts(t0, client, remote, "error", 400)
            return _error(400, "api_usage_error", "body is not JSON")
        cap = settings.tts_max_input_chars
        if settings.tts_client_max_input_chars is not None:
            cap = min(cap, settings.tts_client_max_input_chars)
        try:
            req = validate_speech(body, settings, cap)
        except TtsError as exc:
            await record_tts(t0, client, remote, "error", 400)
            return _error(400, "api_usage_error", exc.message)
        if await running_name() != "qwen-tts":
            await record_tts(t0, client, remote, "error", 503, req)
            return await stopped("TTS sidecar")
        if not tts.try_acquire(text_weight(req["input"])):
            await record_tts(t0, client, remote, "error", 429, req)
            return _error(
                429,
                "rate_limit_error",
                "the TTS sidecar is busy",
                {"Retry-After": str(tts.retry_after_s())},
            )
        try:
            res = await tts.speak(req)
        except TtsError as exc:
            await record_tts(t0, client, remote, "error", exc.code, req)
            return _error(exc.code, _ERROR_KINDS.get(exc.code, "unavailable"), exc.message)
        finally:
            tts.release()
        await record_tts(t0, client, remote, "ok", None, req, res.chunks, res.audio_s)
        return Response(
            res.audio,
            media_type=res.media_type,
            headers={
                "X-Audio-Seconds": f"{res.audio_s:.2f}",
                "Server-Timing": f"total;dur={res.latency_ms}",
            },
        )


def _embed_routes(
    app: FastAPI,
    settings: Settings,
    embed: EmbedClient,
    history: EmbedStore | None,
    authenticate: Callable[[Request], Awaitable[dict[str, Any] | None]],
    refused: Callable[[dict[str, Any] | None], bool],
    embed_reason: Callable[[], str | None],
) -> None:
    """``POST /v1/embeddings`` (OpenAI) and ``POST /v1/rerank`` (Cohere/Jina), served only
    while the always-on service runs (M8). They never start it; one in-flight pool for
    both; one usage row per request without the text."""

    async def record(
        t0: float,
        client: dict[str, Any] | None,
        remote: str | None,
        route: str,
        status: str,
        code: int | None,
        inputs: int | None = None,
        chars: int | None = None,
        tokens: int | None = None,
        dims: int | None = None,
    ) -> None:
        if history is None:
            return
        try:
            await asyncio.to_thread(
                history.add_request,
                ts=time.time(),
                source="gateway",
                route=route,
                inputs=inputs,
                chars=chars,
                tokens=tokens,
                dims=dims,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                status=status,
                error_code=code,
                client_id=str(client["id"]) if client else None,
                remote_addr=remote,
            )
        except Exception:  # a failed usage row must not fail the request
            log.exception("gateway: recording embed usage failed")

    too_big = f"request is larger than {embed_mod.MAX_BODY_BYTES} bytes"

    async def read_body(request: Request) -> tuple[Any, Response | None, int | None]:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > embed_mod.MAX_BODY_BYTES:
            return None, _error(413, "api_usage_error", too_big), 413
        raw = await request.body()
        if len(raw) > embed_mod.MAX_BODY_BYTES:
            return None, _error(413, "api_usage_error", too_big), 413
        try:
            return json.loads(raw), None, None
        except ValueError:
            return None, _error(400, "api_usage_error", "body is not JSON"), 400

    async def unavailable() -> Response | None:
        reason = await asyncio.to_thread(embed_reason)
        if reason is None:
            return None
        return _error(
            503, "unavailable", f"the embedding service is {reason.replace('_', ' ')}",
            extra={"reason": reason},
        )

    def busy() -> Response:
        return _error(
            429, "rate_limit_error", "the embedding service is busy", {"Retry-After": "1"}
        )

    def failed(exc: EmbedError) -> Response:
        if exc.code == 503:
            return _error(503, "unavailable", exc.message, extra={"reason": "down"})
        return _error(exc.code, _ERROR_KINDS.get(exc.code, "unavailable"), exc.message)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        t0 = time.perf_counter()
        remote = request.client.host if request.client else None
        client = await authenticate(request)
        if refused(client):
            await record(t0, None, remote, "embed", "error", 401)
            return _error(401, "authentication_error", "invalid or missing API key")
        body, err, code = await read_body(request)
        if err is not None:
            await record(t0, client, remote, "embed", "error", code)
            return err
        try:
            req = validate_embed(body, settings)
        except EmbedError as exc:
            await record(t0, client, remote, "embed", "error", 400)
            return _error(400, "api_usage_error", exc.message)
        texts = texts_of(req)
        counts = {"inputs": len(texts), "chars": sum(len(t) for t in texts)}
        if (down := await unavailable()) is not None:
            await record(t0, client, remote, "embed", "error", 503, **counts)
            return down
        if not embed.try_acquire():
            await record(t0, client, remote, "embed", "error", 429, **counts)
            return busy()
        try:
            res = await embed.embed(req)
        except EmbedError as exc:
            await record(t0, client, remote, "embed", "error", exc.code, **counts)
            return failed(exc)
        finally:
            embed.release()
        await record(t0, client, remote, "embed", "ok", None, **counts, tokens=res.tokens, dims=res.dims)
        b64 = req.encoding_format == "base64"
        data = [
            {"object": "embedding", "index": i, "embedding": encode_base64(v) if b64 else v}
            for i, v in enumerate(res.vectors)
        ]
        tokens = res.tokens or 0
        return JSONResponse(
            {
                "object": "list",
                "data": data,
                "model": settings.embed_model_id,
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            },
            headers={"Server-Timing": f"total;dur={res.latency_ms}"},
        )

    @app.post("/v1/rerank")
    async def rerank(request: Request) -> Response:
        t0 = time.perf_counter()
        remote = request.client.host if request.client else None
        client = await authenticate(request)
        if refused(client):
            await record(t0, None, remote, "rerank", "error", 401)
            return _error(401, "authentication_error", "invalid or missing API key")
        body, err, code = await read_body(request)
        if err is not None:
            await record(t0, client, remote, "rerank", "error", code)
            return err
        try:
            req = validate_rerank(body, settings)
        except EmbedError as exc:
            await record(t0, client, remote, "rerank", "error", 400)
            return _error(400, "api_usage_error", exc.message)
        counts = {
            "inputs": len(req.documents),
            "chars": len(req.query) + sum(len(d) for d in req.documents),
        }
        if (down := await unavailable()) is not None:
            await record(t0, client, remote, "rerank", "error", 503, **counts)
            return down
        if not embed.try_acquire():
            await record(t0, client, remote, "rerank", "error", 429, **counts)
            return busy()
        try:
            res = await embed.rerank(req)
        except EmbedError as exc:
            await record(t0, client, remote, "rerank", "error", exc.code, **counts)
            return failed(exc)
        finally:
            embed.release()
        await record(t0, client, remote, "rerank", "ok", None, **counts)
        return JSONResponse(
            {"id": uuid.uuid4().hex, "results": res.results, "meta": {}},
            headers={"Server-Timing": f"total;dur={res.latency_ms}"},
        )
