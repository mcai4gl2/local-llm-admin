"""OCR loopback API, private temp cleanup and VLM completion qualification."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from .ocr_fixtures import png, result
from .test_host_ocr_pipeline import load


class FakeRuntime:
    def __init__(self):
        self.ready = True
        self.busy = False
        self.active = None
        self.worker = SimpleNamespace(pid=123, returncode=None)
        self.seen = []
    async def start(self):
        pass
    async def close(self):
        self.ready = False
    async def recognize(self, request_id, path, media_type):
        self.seen.append((path.read_bytes(), path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777))
        return result()
    async def cancel(self, request_id):
        self.ready = False
        return True
    async def abort(self):
        self.ready = False
        return True


@pytest.fixture
async def server(tmp_path):
    module = load("server")
    config = {"root": str(tmp_path), "api_port": 8097, "vlm_port": 8098,
              "model_id": "local-ocr", "revision": result()["revision"], "precision": "bf16",
              "vlm_model_path": str(tmp_path / "models/pinned"), "limits": {
                  "max_bytes": 1000, "max_output_bytes": 10000,
                  "document_timeout_s": 240.0, "page_timeout_s": 45.0,
              }}
    runtime = FakeRuntime()
    reads = []
    async def upstream(request):
        reads.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "OCR"}}]})
    app = module.create_app(tmp_path, config=config, runtime=runtime,
                            transport=httpx.MockTransport(upstream), verify_vlm=lambda: True)
    async with app.router.lifespan_context(app):
        await app.state.start_task
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ocr") as client:
            yield client, app, runtime, reads, config


async def test_native_pipeline_api_cleans_private_files(server):
    client, app, runtime, _, config = server
    response = await client.post("/recognize", json={
        "request_id": "a" * 32, "media_type": "image/png", "data": base64.b64encode(png()).decode(),
    })
    assert response.status_code == 200 and response.json()["complete"] is True
    assert runtime.seen == [(png(), 0o600, 0o700)]
    assert list((Path(config["root"]) / "tmp").iterdir()) == []
    health = await client.get("/ready")
    assert health.status_code == 200 and health.json()["warmup"] is True
    assert "pipeline_token" not in health.text


async def test_cancel_reports_stopped_and_lost_readiness(server):
    client, app, runtime, _, _ = server
    response = await client.post("/requests/" + "a" * 32 + "/cancel")
    assert response.json() == {"request_id": "a" * 32, "stopped": True}
    assert (await client.get("/ready")).status_code == 503
    assert (await client.get("/health")).status_code == 200


async def test_native_api_rejects_paths_and_unbounded_input(server):
    client, app, runtime, _, _ = server
    body = {"request_id": "a" * 32, "media_type": "image/png", "data": base64.b64encode(png()).decode()}
    app.state.failures["other-request"] = True
    for patch in ({"path": "/tmp/file"}, {"data": "!!!"}, {"request_id": "../file"}, {"request_id": []}):
        response = await client.post("/recognize", json={**body, **patch})
        assert response.status_code == 400
    response = await client.post("/recognize", content=b"x" * 70000)
    assert response.status_code == 413 and runtime.seen == []


async def test_vlm_proxy_cannot_bypass_document_admission_or_select_another_model(server):
    client, app, runtime, reads, config = server
    payload = {"model": config["vlm_model_path"], "messages": []}
    assert (await client.post("/vlm/v1/chat/completions", json=payload)).status_code == 401
    headers = {"Authorization": "Bearer " + app.state.pipeline_token}
    assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 503
    runtime.active = "a" * 32
    assert (await client.post("/vlm/v1/chat/completions", json={**payload, "model": "local-qwen"}, headers=headers)).status_code == 400
    assert app.state.failures["a" * 32]
    assert not [request for request in reads if request.url.path != "/health"]


async def test_vlm_length_finish_is_a_document_failure_and_never_retried(server):
    client, app, runtime, reads, config = server
    called = []
    async def truncated(request):
        called.append(True)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]})
    app.state.vlm_transport = httpx.MockTransport(truncated)
    runtime.active = "b" * 32
    payload = {"model": config["vlm_model_path"], "messages": [], "stream": False}
    headers = {"Authorization": "Bearer " + app.state.pipeline_token}
    assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 502
    assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 503
    assert called == [True] and app.state.failures["b" * 32]


async def test_disconnecting_loopback_request_cancels_native_work_before_cleanup(server):
    import asyncio
    import json

    from starlette.requests import Request
    client, app, runtime, _, _ = server
    entered = asyncio.Event()
    stopped = asyncio.Event()
    async def slow(request_id, path, media):
        runtime.active = request_id
        entered.set()
        await stopped.wait()
        raise ValueError("stopped")
    async def cancel(request_id):
        runtime.ready = False
        stopped.set()
        return True
    runtime.recognize = slow
    runtime.cancel = cancel
    body = json.dumps({"request_id": "a" * 32, "media_type": "image/png",
                       "data": base64.b64encode(png()).decode()}).encode()
    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await entered.wait()
        return {"type": "http.disconnect"}
    endpoint = next(r.endpoint for r in app.routes if r.path == "/recognize")
    response = await endpoint(Request({"type": "http", "headers": []}, receive))
    assert response.status_code == 499 and stopped.is_set()


@pytest.mark.parametrize("duration,idle", [(0.2, 0.2), (2.0, 1.0)])
async def test_region_pacing_carries_idle_time_across_document_boundaries(server, duration, idle):
    client, app, runtime, _, config = server
    clock = [0.0]
    dispatched = []
    delays = []
    app.state.region_clock = lambda: clock[0]
    async def pause(delay):
        delays.append(delay)
        clock[0] += delay
    app.state.region_sleep = pause
    async def upstream(request):
        dispatched.append(clock[0])
        clock[0] += duration
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "OCR"}}]})
    app.state.vlm_transport = httpx.MockTransport(upstream)
    payload = {"model": config["vlm_model_path"], "messages": []}
    headers = {"Authorization": "Bearer " + app.state.pipeline_token}
    for name in ("a", "b"):
        runtime.active = name * 32
        assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 200
    assert dispatched == pytest.approx([0, duration + idle])
    assert delays == pytest.approx([idle])


async def test_cancelled_document_never_starts_a_region_after_pacing_wait(server):
    client, app, runtime, _, config = server
    clock = [0.0]
    app.state.region_clock = lambda: clock[0]
    calls = []
    async def pause(delay):
        clock[0] += delay
        runtime.active = None
    app.state.region_sleep = pause
    async def upstream(request):
        calls.append(True)
        clock[0] += 0.2
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "OCR"}}]})
    app.state.vlm_transport = httpx.MockTransport(upstream)
    payload = {"model": config["vlm_model_path"], "messages": []}
    headers = {"Authorization": "Bearer " + app.state.pipeline_token}
    runtime.active = "a" * 32
    assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 200
    runtime.active = "b" * 32
    assert (await client.post("/vlm/v1/chat/completions", json=payload, headers=headers)).status_code == 503
    assert calls == [True]
