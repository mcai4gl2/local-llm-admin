"""M2 §5: quality run API, chat reservation and pages."""

import asyncio
import json
import subprocess

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin.app import create_app
from local_llm_admin.quality_store import QualityStore

from .fake_mlx_server import FakeMlxServer
from .fake_openai import FakeOpenAI, sse_chunks


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


@pytest.fixture
def server():
    return FakeMlxServer()


@pytest.fixture
def settings(fake_comfy, fake_mlx):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "test-secret"
    s.quality_ready_timeout_s = 1
    s.quality_rss_interval_s = 0.01
    (fake_mlx.state / "track_env").write_text("")
    gemma = fake_mlx.root / "models/Gemma"
    gemma.mkdir()
    (gemma / "config.json").write_text(json.dumps({"model_type": "gemma4"}))
    return s


@pytest.fixture
async def app(settings, fake_comfy, server):
    chat = FakeOpenAI(sse_chunks(["Hi"], usage={"prompt_tokens": 1, "completion_tokens": 1}))
    app = create_app(
        settings,
        start_background=False,
        chat_transport=chat.transport,
        comfy_transport=fake_comfy.transport,
        quality_transport=server.transport,
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
        yield c


@pytest.fixture
def mlx_running(settings):
    subprocess.run(
        [str(settings.mlx_model_path), "start", "qwen"], capture_output=True, check=False
    )


async def _wait_run(client, run_id):
    for _ in range(500):
        run = (await client.get(f"/api/quality/runs/{run_id}")).json()
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.02)
    raise AssertionError("run stuck")


async def test_quality_endpoints_require_login_and_csrf(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.get("/api/quality/runs")).status_code == 401
        await anon.post("/login", data={"password": "pw"})
        r = await anon.post("/api/quality/runs", json={"kind": "benchmark", "scope": "current"})
        assert r.status_code == 403


async def test_benchmark_run_lifecycle(client, mlx_running):
    r = await client.post("/api/quality/runs", json={"kind": "benchmark", "scope": "current"})
    assert r.status_code == 202, r.text
    run = r.json()
    listing = (await client.get("/api/quality/runs")).json()
    assert listing["runs"][0]["id"] == run["id"]
    done = await _wait_run(client, run["id"])
    assert done["status"] == "succeeded" and done["restoration"]["outcome"] == "not_needed"
    case = done["cases"][0]
    assert case["measurements"] and case["summary"]["medians"]["backend_decode_tok_s"] == 40.0
    job = (await client.get(f"/api/jobs/{run['job_id']}")).json()
    assert job["action"] == "quality" and job["status"] == "succeeded"
    assert (await client.get("/api/quality/runs")).json()["active"] is None


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"kind": "nope", "scope": "current"}, 400),
        ({"kind": "benchmark", "scope": "nope"}, 400),
        ({"kind": "benchmark", "scope": "model", "model": "missing"}, 404),
    ],
)
async def test_invalid_run_requests(client, mlx_running, body, code):
    assert (await client.post("/api/quality/runs", json=body)).status_code == code


async def test_off_needs_confirmation(client):
    r = await client.post("/api/quality/runs", json={"kind": "benchmark", "scope": "current"})
    assert r.status_code == 409 and "confirm" in r.json()["detail"]
    r = await client.post(
        "/api/quality/runs", json={"kind": "benchmark", "scope": "current", "confirm_off": True}
    )
    assert r.status_code == 202
    done = await _wait_run(client, r.json()["id"])
    assert done["status"] == "succeeded" and done["restoration"]["outcome"] == "restored"


async def test_cancel_and_busy_while_running(client, mlx_running, server):
    server.stream_parts = ["x"] * 200
    server.stream_delay = 0.02
    run = (
        await client.post("/api/quality/runs", json={"kind": "benchmark", "scope": "current"})
    ).json()
    for _ in range(100):
        if server.requests:
            break
        await asyncio.sleep(0.01)
    assert (await client.get("/api/quality/runs")).json()["active"]["run_id"] == run["id"]
    chat = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert chat.status_code == 409 and "quality run" in chat.json()["detail"]
    stop = await client.post("/api/models/stop")
    assert stop.status_code == 409 and "quality run" in stop.json()["detail"]
    r = await client.post(f"/api/quality/runs/{run['id']}/cancel")
    assert r.status_code == 202
    done = await _wait_run(client, run["id"])
    assert done["status"] == "cancelled"
    assert (await client.post(f"/api/quality/runs/{run['id']}/cancel")).status_code == 409
    assert (await client.post("/api/quality/runs/nope/cancel")).status_code == 404


async def test_unknown_run_is_404(client):
    assert (await client.get("/api/quality/runs/nope")).status_code == 404


async def test_latest_compatibility(client, mlx_running):
    run = (
        await client.post("/api/quality/runs", json={"kind": "compatibility", "scope": "current"})
    ).json()
    await _wait_run(client, run["id"])
    latest = (await client.get("/api/quality/latest")).json()
    assert latest["compatibility"]["qwen"]["plain"]["status"] == "pass"
    assert [c["key"] for c in latest["benchmark_cases"]][:3] == ["p128", "p2k", "p8k"]
    assert latest["settings"]["temperature"] == 0 and latest["repeats"] == 3
    assert latest["warmup"] == 1 and "tools" in latest["compatibility_cases"]


async def test_quality_page_and_nav(client):
    r = await client.get("/quality")
    assert r.status_code == 200 and 'id="quality-start"' in r.text
    assert 'href="/quality"' in (await client.get("/models")).text


async def test_startup_reconciles_interrupted_runs(settings, fake_comfy, server):
    store = QualityStore(settings.admin_data_dir / "admin.sqlite")
    store.create_run({"id": "old", "kind": "benchmark", "scope": "all", "started": 1.0})
    app = create_app(
        settings,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        quality_transport=server.transport,
    )
    async with app.router.lifespan_context(app):
        assert store.get_run("old")["status"] == "interrupted"
