"""OCR admin uploads share authenticated session rules and gateway execution."""

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.app import create_app
from local_llm_admin.gateway import create_gateway_app
from local_llm_admin.sidecars import SidecarProcess, SidecarStatus

from .ocr_fixtures import chat, png, result


@pytest.fixture
async def client(fake_mlx, fake_comfy, monkeypatch):
    settings = fake_mlx.settings
    settings.admin_password_hash = auth.hash_password("pw")
    settings.admin_session_secret = "test"
    settings.ocr_port = 8097
    settings.ocr_vlm_port = 8098
    settings.ocr_root = fake_mlx.root / "ocr"
    settings.ocr_enabled = True
    original = SidecarProcess.status
    monkeypatch.setattr(SidecarProcess, "status", lambda self:
        SidecarStatus(True, 123) if self.spec.name == "paddleocr-vl" else original(self))
    def upstream(request):
        if request.url.path == "/ready":
            return httpx.Response(200, json={"ready": True, "warmup": True, "instance": "test"})
        return httpx.Response(200, json=result())
    app = create_app(settings, start_background=False, comfy_transport=fake_comfy.transport,
                     ocr_transport=httpx.MockTransport(upstream))
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        await app.state.backfill_task
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as client:
            await client.post("/login", data={"password": "pw"})
            client.headers["X-CSRF-Token"] = (await client.get("/api/session")).json()["csrf"]
            client.app = app
            yield client


async def test_ocr_upload_and_metadata_only_history(client):
    response = await client.post("/api/ocr/recognize", files={"document": ("private-name.png", png(), "image/png")})
    assert response.status_code == 200 and response.json()["text"] == "Page 1"
    history = (await client.get("/api/services/paddleocr-vl/history")).json()
    assert history["recent"][0]["source"] == "admin-ui"
    assert "private-name" not in str(history) and "Page 1" not in str(history)
    assert (await client.get("/api/ocr")).json()["admission"]["active"] is False


async def test_ocr_session_csrf_and_gateway_objects(client):
    response = await client.post("/api/ocr/recognize", files={"document": ("a.png", png(), "image/png")},
                                 headers={"X-CSRF-Token": "bad"})
    assert response.status_code == 403
    app = client.app
    gateway = create_gateway_app(
        app.state.control.settings, app.state.decision, app.state.decision_keys,
        app.state.decision_history, registry=app.state.sidecars, ocr=app.state.ocr,
        ocr_reason=app.state.ocr_reason, service_history=app.state.service_history,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gw") as other:
        key = app.state.decision_keys.create("app-a-key")["key"]
        assert (await other.post("/v1/chat/completions", json=chat(),
                                 headers={"Authorization": "Bearer " + key})).status_code == 200
    history = (await client.get("/api/services/paddleocr-vl/history")).json()
    assert len(history["recent"]) == 1 and history["recent"][0]["source"] == "gateway"


async def test_unknown_service_history_and_disabled_ocr(client):
    assert (await client.get("/api/services/unknown/history")).status_code == 404
    client.app.state.control.settings.ocr_enabled = False
    assert (await client.get("/api/ocr")).status_code == 404
    response = await client.post("/api/ocr/recognize", files={"document": ("a.png", png(), "image/png")})
    assert response.status_code == 503
