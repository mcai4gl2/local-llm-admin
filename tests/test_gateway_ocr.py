"""Authenticated OCR transport contract on the sidecar gateway."""

import asyncio
import json

import httpx
import pytest

from local_llm_admin.config import Settings
from local_llm_admin.decision import DecisionClient
from local_llm_admin.decision_keys import ClientKeys
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.gateway import create_gateway_app
from local_llm_admin.ocr import OcrClient
from local_llm_admin.service_store import ServiceStore

from .ocr_fixtures import chat, png, result


@pytest.fixture
async def gw(tmp_path):
    settings = Settings(ocr_enabled=True, ocr_port=8097, ocr_vlm_port=8098,
                        decision_gateway_require_key=True)
    db = tmp_path / "admin.sqlite"
    history = ServiceStore(db, settings)
    keys = ClientKeys(db)
    key = keys.create("app-a-key")["key"]
    requests = []
    async def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=result())
    ocr = OcrClient(settings, transport=httpx.MockTransport(upstream))
    app = create_gateway_app(
        settings, DecisionClient(settings), keys, DecisionStore(db),
        ocr=ocr, ocr_reason=lambda: None, service_history=history,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw",
                                 headers={"Authorization": f"Bearer {key}"}) as client:
        client.ocr = ocr
        client.reads = requests
        client.history = history
        client.settings = settings
        yield client


async def test_image_chat_completion(gw):
    response = await gw.post("/v1/chat/completions", json=chat())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "local-ocr" and body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Page 1"
    assert "usage" not in body
    row = gw.history.recent("paddleocr-vl")[0]
    assert row["capability"] == "ocr" and row["pages"] == 1 and row["input_bytes"] == len(png())
    assert "Page 1" not in json.dumps(row)


async def test_pdf_chat_and_structured_mode(gw):
    body = {**chat(b"%PDF-1.7\n%%EOF", "application/pdf"), "response_format": {"type": "json_object"}}
    response = await gw.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    content = json.loads(response.json()["choices"][0]["message"]["content"])
    assert content["schema_version"] == 1 and len(content["pages"]) == 1
    assert gw.reads[0]["media_type"] == "application/pdf"


@pytest.mark.parametrize("output,content_type", [("json", "application/json"), ("text", "text/plain"), ("markdown", "text/markdown")])
async def test_native_multipart(gw, output, content_type):
    response = await gw.post("/v1/ocr", files={"document": ("not-stored.png", png(), "image/png")},
                             data={"output": output})
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert "not-stored" not in json.dumps(gw.history.recent("paddleocr-vl"))


async def test_key_required_before_reading_or_forwarding(gw):
    response = await gw.post("/v1/chat/completions", content=b"not JSON", headers={"Authorization": "Bearer bad"})
    assert response.status_code == 401 and gw.reads == []


async def test_ocr_requires_a_key_even_when_legacy_gateway_routes_allow_anonymous(gw):
    gw.settings.decision_gateway_require_key = False
    gw.headers.pop("authorization")
    assert (await gw.post("/v1/chat/completions", content=b"not JSON")).status_code == 401
    assert (await gw.post("/v1/ocr", content=b"not multipart")).status_code == 401
    assert gw.reads == []


async def test_discovery_excludes_a_healthy_endpoint_with_unknown_process_owner(gw, monkeypatch):
    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus
    monkeypatch.setattr(SidecarProcess, "status", lambda _: SidecarStatus(True, None, owned=False))
    monkeypatch.setattr(SidecarProcess, "ready_sync", lambda _: True)
    assert (await gw.get("/v1/models")).status_code == 503


async def test_never_dispatches_main_chat_models_or_unsupported_streams(gw):
    for patch in ({"model": "local-qwen"}, {"stream": True}, {"tools": []}):
        response = await gw.post("/v1/chat/completions", json={**chat(), **patch})
        assert response.status_code == 400
    assert gw.reads == []


async def test_invalid_native_fields_and_documents_are_rejected(gw):
    for fields in ({"output": "html"}, {"output": "json", "path": "/tmp/input.png"}):
        response = await gw.post("/v1/ocr", files={"document": ("a.png", png(), "image/png")}, data=fields)
        assert response.status_code == 400
    response = await gw.post("/v1/ocr", files={"document": ("a.png", b"corrupt", "image/png")})
    assert response.status_code == 400 and gw.reads == []


async def test_gateway_and_admin_share_one_admission_pool(gw):
    # Reserving every shared slot blocks native and chat equally.
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=result())
    gw.ocr.transport = httpx.MockTransport(slow)
    from local_llm_admin.ocr import OcrDocument
    tasks = [asyncio.create_task(gw.ocr.recognize(OcrDocument(png(), "image/png"))) for _ in range(3)]
    await entered.wait()
    response = await gw.post("/v1/chat/completions", json=chat())
    assert response.status_code == 429 and response.headers["retry-after"] == "1"
    response = await gw.post("/v1/ocr", files={"document": ("a.png", png(), "image/png")})
    assert response.status_code == 429
    release.set()
    await asyncio.gather(*tasks)


async def test_ocr_disabled_gateway_does_not_gain_chat_route(tmp_path):
    settings = Settings()
    db = tmp_path / "admin.sqlite"
    app = create_gateway_app(settings, DecisionClient(settings), ClientKeys(db), DecisionStore(db))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        assert (await client.post("/v1/chat/completions", json=chat())).status_code == 404


async def test_bad_json_content_type_and_native_unknown_field(gw):
    assert (await gw.post("/v1/chat/completions", content=b"not JSON")).status_code == 400
    assert (await gw.post("/v1/ocr", json={})).status_code == 415
    response = await gw.post("/v1/ocr", files={"document": ("a.png", png(), "image/png")}, data={"path": "/tmp/file"})
    assert response.status_code == 400


async def test_history_failure_does_not_break_recognition_or_log_document(gw, monkeypatch, caplog):
    def unavailable(**kwargs):
        raise RuntimeError("private filename and extracted text")
    monkeypatch.setattr(gw.history, "add_request", unavailable)
    response = await gw.post("/v1/chat/completions", json=chat())
    assert response.status_code == 200
    assert "metadata failed" in caplog.text and "private filename" not in caplog.text


async def test_ocr_works_without_optional_history(tmp_path):
    from fastapi import FastAPI

    from local_llm_admin.ocr_routes import install_ocr_routes
    settings = Settings(ocr_port=8097)
    ocr = OcrClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result())))
    app = FastAPI()
    install_ocr_routes(app, settings, ocr, lambda: None, None,
                       native_path="/ocr", chat_path="/chat", source="admin-ui")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as client:
        assert (await client.post("/chat", json=chat())).status_code == 200


async def test_disconnected_native_request_cancels_shared_work_and_confirms_cleanup():
    from starlette.requests import Request

    from local_llm_admin.ocr import OcrDocument, OcrError
    from local_llm_admin.ocr_routes import _recognize
    entered = asyncio.Event()
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"request_id": request.url.path.split("/")[-2], "stopped": True})
        entered.set()
        await asyncio.Event().wait()
    async def receive():
        await entered.wait()
        return {"type": "http.disconnect"}
    ocr = OcrClient(Settings(ocr_port=8097), transport=httpx.MockTransport(handler))
    request = Request({"type": "http", "headers": []}, receive)
    with pytest.raises(OcrError) as exc:
        await _recognize(request, ocr, OcrDocument(png(), "image/png"))
    assert exc.value.code == 499
    await ocr.drain()
    assert not ocr.busy


async def test_cancelled_http_request_records_only_cancellation_metadata(gw):
    entered = asyncio.Event()
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"request_id": request.url.path.split("/")[-2], "stopped": True})
        entered.set()
        await asyncio.Event().wait()
    gw.ocr.transport = httpx.MockTransport(handler)
    request = asyncio.create_task(gw.post("/v1/chat/completions", json=chat()))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await gw.ocr.drain()
    assert gw.history.recent("paddleocr-vl")[0]["error_code"] == 499
