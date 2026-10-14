"""M8: admin routes for the embedding service."""

import json

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin import sidecars as sidecars_mod
from local_llm_admin.app import create_app

from .test_control import _start_mlx
from .test_decision import QWEN36


@pytest.fixture
async def client(fake_embed, fake_comfy, fake_mlx, monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    s = fake_embed.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    app = create_app(
        s,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        embed_transport=fake_embed.transport,
    )
    app.state.sampler_pressure = lambda: "normal"
    async with app.router.lifespan_context(app):
        await app.state.backfill_task
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as c:
            await c.post("/login", data={"password": "pw"})
            c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
            c.app = app
            yield c


async def _job(client, r):
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    for _ in range(500):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] != "running":
            return job
        import asyncio

        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


async def test_services_view_and_start_stop(client, fake_mlx):
    _start_mlx(client.app.state.control)
    view = (await client.get("/api/services")).json()["services"][0]
    assert view["name"] == "qwen-embed" and not view["running"] and view["intent"] == "stopped"
    assert view["budget"]["ok"] and view["models"] == ["qwen3-embed", "qwen3-rerank"]
    assert (await _job(client, await client.post("/api/services/qwen-embed/start")))["status"] == "succeeded"
    view = (await client.get("/api/services")).json()["services"][0]
    assert view["running"] and view["ready"] and view["intent"] == "running"
    assert (await client.post("/api/sidecars/qwen-embed/start", json={})).status_code == 404
    assert (await _job(client, await client.post("/api/services/qwen-embed/stop")))["status"] == "succeeded"


async def test_similarity_and_rerank_through_admin(client, fake_embed):
    fake_embed.start()
    r = await client.post("/api/embed/similarity", json={"texts": ["a", "bb", "ccc"], "query_first": True})
    body = r.json()
    assert r.status_code == 200 and body["dims"] == 8 and len(body["matrix"]) == 3
    assert abs(body["matrix"][0][0] - 1.0) < 1e-6
    sent = fake_embed.requests[-1][1]["input"]
    assert sent[0].startswith("Instruct: ") and sent[1] == "bb"
    r = await client.post("/api/embed/rerank", json={"query": "q", "documents": ["a", "abcdef"]})
    assert [x["index"] for x in r.json()["results"]] == [1, 0]
    hist = (await client.get("/api/embed/history?days=1")).json()
    assert hist["recent"][0]["source"] == "admin-ui" and hist["daily"][0]["requests"] == 2


async def test_similarity_refusals(client, fake_embed):
    assert (await client.post("/api/embed/similarity", json={"texts": ["a"]})).status_code == 422
    r = await client.post("/api/embed/similarity", json={"texts": ["a", "b"]})
    assert r.status_code == 503 and "stopped" in r.json()["detail"]


async def test_embed_status_and_page(client, fake_embed):
    st = (await client.get("/api/embed")).json()
    assert st["enabled"] and st["reason"] == "stopped" and st["dims"] == 8
    assert (await client.get("/embed")).status_code == 200
    assert (await client.get("/api/embed/usage?days=7&group=client")).status_code == 200


async def test_disabled_hides_everything(client, fake_embed):
    fake_embed.settings.embed_enabled = False
    assert (await client.get("/api/embed")).status_code == 404
    assert (await client.get("/embed")).status_code == 404
    assert (await client.get("/api/services")).json()["services"] == []


async def test_models_page_has_embed_card(client):
    html = (await client.get("/models")).text
    assert 'id="embed-card"' in html


async def test_nav_page_and_usage_section(client):
    nav = (await client.get("/models")).text
    assert 'href="/embed"' in nav
    page = (await client.get("/embed")).text
    assert 'id="embed-sim-form"' in page and 'id="embed-rr-form"' in page and "/static/embed.js" in page
    usage = (await client.get("/usage")).text
    assert 'id="embed-usage"' in usage and "/static/embed_usage.js" in usage
