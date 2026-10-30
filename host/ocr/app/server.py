#!/usr/bin/python3
"""Loopback-only OCR API and authenticated region proxy; no native OCR imports."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import contextlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def load_local(name: str) -> Any:
    path = Path(__file__).with_name(name + ".py")
    key = "isolated_ocr_" + name
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ValueError("missing OCR runtime module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


runtime_module = load_local("runtime")


async def bounded_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (len(declared) > 20 or not declared.isdigit() or int(declared) > limit):
        raise runtime_module.RuntimeError_(413, "request exceeds the byte limit")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise runtime_module.RuntimeError_(413, "request exceeds the byte limit")
        raw.extend(chunk)
    return bytes(raw)


def cleanup_abandoned(root: Path) -> None:
    temporary = root / "tmp"
    temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
    for entry in temporary.iterdir():
        if re.fullmatch(r"request-[a-f0-9]{32}-[a-zA-Z0-9_-]+", entry.name):
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)


def create_app(
    root: Path, *, config: dict[str, Any] | None = None, runtime: Any = None,
    transport: httpx.AsyncBaseTransport | None = None,
    verify_vlm: Callable[[], bool] | None = None,
) -> FastAPI:
    config = dict(config or load_local("config").load_config(root))
    config["pipeline_token"] = secrets.token_urlsafe(32)
    owned = load_local("owned")

    async def stop_vlm() -> bool:
        return bool(await asyncio.to_thread(owned.stop_record, root / "run/vlm.json"))

    def vlm_identity() -> bool:
        try:
            expected = json.loads((root / "run/vlm.json").read_text())
            current = owned.identity(expected["pid"])
            return current is not None and all(
                current[key] == expected[key] for key in ("pid", "pgid", "started", "command")
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    verify_vlm = verify_vlm or vlm_identity
    runtime = runtime or runtime_module.Runtime(root, config, stop_vlm=stop_vlm)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cleanup_abandoned(root)
        app.state.warming = True
        async def start() -> None:
            try:
                await runtime.start()
            except Exception:
                pass  # readiness reports the failure; never print native document diagnostics
            finally:
                app.state.warming = False
        app.state.start_task = asyncio.create_task(start())
        try:
            yield
        finally:
            if not app.state.start_task.done():
                app.state.start_task.cancel()
                await asyncio.gather(app.state.start_task, return_exceptions=True)
            await runtime.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pipeline_token = config["pipeline_token"]
    app.state.warming = False
    app.state.failures = {}
    app.state.vlm_transport = transport
    app.state.runtime = runtime
    app.state.region_clock = time.monotonic
    app.state.region_sleep = asyncio.sleep
    app.state.next_region_at = 0.0
    instance = uuid.uuid4().hex
    proxy_lock = asyncio.Lock()

    async def health() -> dict[str, Any]:
        vlm_ready = False
        if await asyncio.to_thread(verify_vlm):
            try:
                async with httpx.AsyncClient(
                    transport=app.state.vlm_transport, timeout=0.75,
                ) as client:
                    response = await client.get(f"http://127.0.0.1:{config['vlm_port']}/health")
                    vlm_ready = response.status_code == 200
            except httpx.HTTPError:
                pass
        worker_running = runtime.worker is not None and runtime.worker.returncode is None
        ready = runtime.ready and worker_running and vlm_ready
        return {
            "instance": instance, "ready": ready, "warmup": runtime.ready,
            "state": "ready" if ready else "loading" if app.state.warming else "failed",
            "components": {"pipeline": worker_running, "vlm": vlm_ready},
            "busy": runtime.busy,
        }

    @app.get("/health")
    async def liveness() -> JSONResponse:
        return JSONResponse(await health())

    @app.get("/ready")
    async def readiness() -> JSONResponse:
        body = await health()
        return JSONResponse(body, status_code=200 if body["ready"] else 503)

    @app.post("/requests/{request_id}/cancel")
    async def cancel(request_id: str) -> JSONResponse:
        if not re.fullmatch("[a-f0-9]{32}", request_id):
            return JSONResponse({"error": "invalid request id"}, status_code=400)
        stopped = await runtime.cancel(request_id)
        return JSONResponse({"request_id": request_id, "stopped": stopped},
                            status_code=200 if stopped else 503)

    async def run_document(request: Request, request_id: str, path: Path, media: str) -> dict[str, Any]:
        async def disconnected() -> None:
            while (await request.receive())["type"] != "http.disconnect":
                pass
        work = asyncio.create_task(runtime.recognize(request_id, path, media))
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            if work not in done:
                await runtime.cancel(request_id)
                work.cancel()
                raise runtime_module.RuntimeError_(499, "OCR client disconnected")
            return dict(await work)
        finally:
            watcher.cancel()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, watcher, return_exceptions=True)

    @app.post("/recognize")
    async def recognize(request: Request) -> JSONResponse:
        request_id = None
        directory = None
        try:
            raw = await bounded_body(request, ((config["limits"]["max_bytes"] + 2) // 3) * 4 + 65536)
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"request_id", "media_type", "data"}:
                raise ValueError("invalid envelope")
            request_id = body["request_id"]
            media = body["media_type"]
            if not isinstance(request_id, str) or not re.fullmatch("[a-f0-9]{32}", request_id):
                raise ValueError("invalid id")
            if media not in ("image/png", "image/jpeg", "application/pdf") or not isinstance(body["data"], str):
                raise ValueError("invalid media")
            if len(body["data"]) > ((config["limits"]["max_bytes"] + 2) // 3) * 4:
                raise runtime_module.RuntimeError_(413, "document exceeds the byte limit")
            data = base64.b64decode(body["data"], validate=True)
            if not data or len(data) > config["limits"]["max_bytes"]:
                raise runtime_module.RuntimeError_(413, "document exceeds the byte limit")
            directory = Path(tempfile.mkdtemp(prefix="request-" + request_id + "-", dir=root / "tmp"))
            path = directory / "input"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            result = await run_document(request, request_id, path, media)
            if app.state.failures.get(request_id):
                await runtime.abort()
                raise runtime_module.RuntimeError_(502, "OCR region recognition was incomplete")
            if (result.get("complete") is not True or result.get("truncated") is not False
                    or result.get("model") != config["model_id"] or result.get("revision") != config["revision"]
                    or result.get("precision") != config["precision"]):
                raise runtime_module.RuntimeError_(502, "invalid OCR result identity")
            if len(json.dumps(result, ensure_ascii=False).encode()) > config["limits"]["max_output_bytes"]:
                raise runtime_module.RuntimeError_(502, "OCR output exceeds the byte limit")
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except runtime_module.RuntimeError_ as exc:
            return JSONResponse({"error": {"code": exc.code, "message": "OCR document processing failed"}},
                                status_code=exc.code)
        except (ValueError, TypeError, binascii.Error):
            return JSONResponse({"error": {"code": 400, "message": "invalid document input"}}, status_code=400)
        finally:
            if isinstance(request_id, str):
                app.state.failures.pop(request_id, None)
            if directory is not None:
                shutil.rmtree(directory)

    @app.post("/vlm/v1/chat/completions")
    async def region(request: Request) -> JSONResponse:
        expected = "Bearer " + app.state.pipeline_token
        if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse({"error": "invalid pipeline authorization"}, status_code=401)
        async with proxy_lock:
            document = runtime.active or ("warmup" if app.state.warming else None)
            if document is None or app.state.failures.get(document):
                return JSONResponse({"error": "no admitted OCR document"}, status_code=503)
            try:
                body = json.loads(await bounded_body(request, 64 * 1024 * 1024))
                if not isinstance(body, dict) or body.get("model") != config["vlm_model_path"] or body.get("stream", False) is not False:
                    app.state.failures[document] = True
                    return JSONResponse({"error": "invalid OCR recognition model"}, status_code=400)
                async with asyncio.timeout(config["limits"]["page_timeout_s"]), httpx.AsyncClient(
                    transport=app.state.vlm_transport, timeout=config["limits"]["page_timeout_s"],
                ) as client:
                    # Yield between serial GPU regions, including across documents. Idle
                    # for the prior call's duration, capped at one second; page/document
                    # deadlines include the wait. Recheck admission after every wait.
                    delay = app.state.next_region_at - app.state.region_clock()
                    if delay > 0:
                        await app.state.region_sleep(delay)
                    current = runtime.active or ("warmup" if app.state.warming else None)
                    if current != document or app.state.failures.get(document):
                        return JSONResponse({"error": "no admitted OCR document"}, status_code=503)
                    dispatched = app.state.region_clock()
                    try:
                        async with client.stream(
                            "POST", f"http://127.0.0.1:{config['vlm_port']}/v1/chat/completions", json=body,
                        ) as response:
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(raw) + len(chunk) > config["limits"]["max_output_bytes"]:
                                    raise ValueError("region output limit")
                                raw.extend(chunk)
                    finally:
                        now = app.state.region_clock()
                        app.state.next_region_at = now + min(1.0, max(0.0, now - dispatched))
                    payload = json.loads(raw)
                    choices = payload.get("choices") if isinstance(payload, dict) else None
                    if response.status_code != 200 or not isinstance(choices, list) or len(choices) != 1:
                        raise ValueError("invalid region response")
                    choice = choices[0]
                    if (choice.get("finish_reason") != "stop" or not isinstance(
                        choice.get("message", {}).get("content"), str,
                    )):
                        raise ValueError("incomplete region")
                    return JSONResponse(payload)
            except Exception:
                app.state.failures[document] = True
                return JSONResponse({"error": "OCR region failed or was truncated"}, status_code=502)

    return app


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    config = load_local("config").load_config(args.root)
    uvicorn.run(create_app(args.root, config=config), host="127.0.0.1", port=config["api_port"],
                access_log=False, log_level="error")


if __name__ == "__main__":
    main()
