"""M7: the generic sidecar routes."""

import json

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin import sidecars as sidecars_mod
from local_llm_admin.app import create_app

from .test_decision import QWEN36


@pytest.fixture(autouse=True)
def ram(monkeypatch):
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)


@pytest.fixture
async def client(fake_decision, fake_comfy, fake_mlx, monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    s = fake_decision.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    app = create_app(
        s,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        decision_transport=fake_decision.transport,
    )
    app.state.sampler_pressure = lambda: "normal"
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        await app.state.backfill_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
            yield c


async def test_list_start_stop(client, fake_decision):
    r = await client.get("/api/sidecars")
    body = r.json()
    assert r.status_code == 200 and [s["name"] for s in body["sidecars"]] == ["openjev"]
    view = body["sidecars"][0]
    assert view["title"] == "Decision sidecar" and view["running"] is False
    assert view["budget"]["ok"] in (True, False) and "lines" in view["budget"]
    r = await client.post("/api/sidecars/openjev/start", json={"confirm": False})
    assert r.status_code == 202 and r.json()["action"] == "sidecar"  # MLX off: only 20 GB


async def test_unknown_sidecar_is_404(client):
    assert (await client.post("/api/sidecars/nope/start", json={})).status_code == 404
    assert (await client.post("/api/sidecars/nope/stop")).status_code == 404


async def test_status_names_the_running_sidecar(client, fake_decision):
    fake_decision.start()
    st = (await client.get("/api/status")).json()
    assert st["sidecar"] == {"running": "openjev"} and st["decision"]["running"] is True
