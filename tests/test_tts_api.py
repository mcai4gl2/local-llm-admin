"""M7: the admin's TTS routes (status, speak, history, usage, page)."""

import json

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin import sidecars as sidecars_mod
from local_llm_admin.app import create_app

from .test_decision import QWEN36


@pytest.fixture
async def client(fake_tts, fake_decision, fake_comfy, fake_mlx, monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    s = fake_tts.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    app = create_app(
        s,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        decision_transport=fake_decision.transport,
        tts_transport=fake_tts.transport,
    )
    app.state.sampler_pressure = lambda: "normal"
    async with app.router.lifespan_context(app):
        await app.state.backfill_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
            yield c


async def test_status_and_speak(client, fake_tts):
    st = (await client.get("/api/tts")).json()
    assert st["enabled"] and st["voices"] == ["Vivian", "Ryan"] and st["max_chars"] == 10_000
    fake_tts.start()
    r = await client.post(
        "/api/tts/speak", json={"model": "qwen3-tts", "input": "Hello.", "voice": "Ryan"}
    )
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    hist = (await client.get("/api/tts/history?days=1")).json()
    assert hist["recent"][0]["source"] == "admin-ui" and hist["daily"][0]["requests"] == 1


async def test_speak_while_stopped_is_503(client):
    r = await client.post(
        "/api/tts/speak", json={"model": "qwen3-tts", "input": "Hello.", "voice": "Ryan"}
    )
    assert r.status_code == 503


async def test_disabled_is_404(client, fake_tts):
    fake_tts.settings.tts_enabled = False
    assert (await client.get("/api/tts")).status_code == 404
    assert (await client.get("/tts")).status_code == 404


async def test_page_renders_when_enabled(client):
    assert (await client.get("/tts")).status_code == 200


async def test_usage_groups(client, fake_tts):
    fake_tts.start()
    await client.post("/api/tts/speak", json={"model": "qwen3-tts", "input": "Hi.", "voice": "Ryan"})
    by_client = (await client.get("/api/tts/usage?days=1&group=client")).json()
    assert by_client["rows"][0]["client"] == "(admin page)"
