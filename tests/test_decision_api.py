"""M6 Phase 2: the decision sidecar routes (status, start, stop)."""

import json
import subprocess

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin import sidecars as sidecars_mod
from local_llm_admin.app import create_app

from .test_decision import QWEN36


@pytest.fixture
async def api(fake_decision, fake_comfy, fake_mlx, monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)
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
            yield c, app


async def _wait(c, job_id):
    for _ in range(500):
        job = (await c.get(f"/api/jobs/{job_id}")).json()
        if job["status"] != "running":
            return job
        import asyncio

        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def test_status_start_stop(api, fake_decision):
    c, app = api
    st = (await c.get("/api/decision")).json()
    assert st["running"] is False and st["intent"] is None
    assert st["budget"]["ok"] is True and st["budget"]["lines"][1] == "decision 20.0 GB"
    assert st["weights"] == "complete"
    r = await c.post("/api/decision/start")
    assert r.status_code == 202, r.text
    assert (await _wait(c, r.json()["id"]))["status"] == "succeeded"
    st = (await c.get("/api/decision")).json()
    assert st["running"] and st["ready"] and st["intent"] == "running"
    r = await c.post("/api/decision/stop")
    assert r.status_code == 202
    await _wait(c, r.json()["id"])
    assert (await c.get("/api/decision")).json()["intent"] == "stopped"


async def test_refusal_shows_reasons(api):
    c, app = api
    app.state.sampler_pressure = lambda: "critical"
    r = await c.post("/api/decision/start")
    assert r.status_code == 409 and "pressure is critical" in r.json()["detail"]
    st = (await c.get("/api/decision")).json()
    assert st["budget"]["ok"] is False


async def test_stop_when_stopped_is_409(api):
    c, _ = api
    assert (await c.post("/api/decision/stop")).status_code == 409


async def test_routes_need_csrf_and_session(api):
    c, _ = api
    del c.headers["X-CSRF-Token"]
    assert (await c.post("/api/decision/start")).status_code == 403
    c.cookies.clear()
    assert (await c.get("/api/decision")).status_code == 401


async def test_disabled_is_404(api, fake_decision):
    c, _ = api
    fake_decision.settings.decision_enabled = False
    for method, path in (
        ("GET", "/api/decision"),
        ("POST", "/api/decision/start"),
        ("POST", "/api/decision/stop"),
    ):
        assert (await c.request(method, path)).status_code == 404


async def test_key_never_in_responses(api, fake_decision):
    c, _ = api
    subprocess.run([str(fake_decision.settings.decision_path), "start"], capture_output=True)
    text = (await c.get("/api/decision")).text + (await c.get("/api/diagnostics")).text
    assert "k" * 20 not in text


# --- Phase 3: read endpoint, status summary, page ------------------------------------------

READ = {
    "state": {"ticket": "charged twice"},
    "questions": {
        "department": {"type": "choice", "criteria": {"billing": "money", "tech": "bugs"}}
    },
}


async def test_read_round_trip(api, fake_decision):
    c, _ = api
    fake_decision.start()
    r = await c.post("/api/decision/read", json=READ)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answers"][0]["top"] == "billing" and out["usage"]["input_tokens"] == 166
    assert fake_decision.reads[0]["model"] == "openjev-latest"


@pytest.mark.parametrize(
    ("setup", "body", "code"),
    [
        (None, {**READ, "steps": 99}, 400),
        ("stopped", READ, 503),
        ("timeout", READ, 504),
        ("raw", READ, 502),
        ("busy", READ, 429),
    ],
)
async def test_read_errors(api, fake_decision, setup, body, code):
    c, _ = api
    if setup != "stopped":
        fake_decision.start()
    if setup == "timeout":
        fake_decision.flags["timeout"] = True
    if setup == "raw":
        fake_decision.flags["raw"] = b"<html>"
    if setup == "busy":
        fake_decision.flags.update(status=529, body={"detail": {"error_type": "overloaded"}})
    r = await c.post("/api/decision/read", json=body)
    assert r.status_code == code, r.text
    assert "k" * 20 not in r.text


async def test_read_needs_csrf_and_is_404_when_disabled(api, fake_decision):
    c, _ = api
    fake_decision.start()
    token = c.headers.pop("X-CSRF-Token")
    assert (await c.post("/api/decision/read", json=READ)).status_code == 403
    c.headers["X-CSRF-Token"] = token
    fake_decision.settings.decision_enabled = False
    assert (await c.post("/api/decision/read", json=READ)).status_code == 404


async def test_status_carries_sidecar_summary(api, fake_decision):
    c, _ = api
    assert (await c.get("/api/status")).json()["decision"] == {"enabled": True, "running": False}
    fake_decision.start()
    assert (await c.get("/api/status")).json()["decision"]["running"] is True
    fake_decision.settings.decision_enabled = False
    assert (await c.get("/api/status")).json()["decision"] == {"enabled": False, "running": False}


async def test_page_and_nav(api, fake_decision):
    c, _ = api
    page = await c.get("/decision")
    assert page.status_code == 200 and 'id="read-form"' in page.text
    assert 'href="/decision"' in (await c.get("/models")).text
    assert 'id="sidecar-card"' in (await c.get("/models")).text
    fake_decision.settings.decision_enabled = False
    assert (await c.get("/decision")).status_code == 404
    models = (await c.get("/models")).text
    assert 'href="/decision"' not in models and 'id="sidecar-card"' not in models


async def test_read_non_json_body_is_400(api, fake_decision):
    c, _ = api
    fake_decision.start()
    r = await c.post(
        "/api/decision/read", content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400 and "not JSON" in r.text


# --- Phase 4: history -------------------------------------------------------------------------


async def test_reads_are_recorded_without_content(api, fake_decision):
    c, app = api
    fake_decision.start()
    secret = {**READ, "state": "SECRET-STATE-TEXT"}
    assert (await c.post("/api/decision/read", json=secret)).status_code == 200
    assert (await c.post("/api/decision/read", json={**READ, "steps": 99})).status_code == 400
    h = (await c.get("/api/decision/history")).json()
    ok, bad = h["recent"][1], h["recent"][0]
    assert ok["status"] == "ok" and ok["source"] == "admin-ui" and ok["n_questions"] == 1
    assert ok["types"] == "choice" and ok["input_tokens"] == 166 and ok["latency_ms"] >= 0
    assert bad["status"] == "error" and bad["error_code"] == 400 and bad["steps"] == 99
    (day,) = h["daily"]
    assert day["requests"] == 2 and day["errors"] == 1 and day["p50_ms"] is not None
    assert set(h["availability"]) == {"counts", "ratio"}
    db = (fake_decision.settings.admin_data_dir / "admin.sqlite").read_bytes()
    assert b"SECRET-STATE-TEXT" not in db


async def test_history_days_bounded_and_404_when_disabled(api, fake_decision):
    c, _ = api
    assert (await c.get("/api/decision/history?days=0")).status_code == 422
    assert (await c.get("/api/decision/history?days=91")).status_code == 422
    assert (await c.get("/api/decision/history?days=7")).status_code == 200
    fake_decision.settings.decision_enabled = False
    assert (await c.get("/api/decision/history")).status_code == 404


# --- Phase 5: gateway clients and usage -----------------------------------------------------


async def test_client_lifecycle(api, fake_decision):
    c, app = api
    r = await c.post("/api/decision/clients", json={"name": "laptop", "note": "dev"})
    assert r.status_code == 201
    created = r.json()
    assert created["key"].startswith("dsk_")
    listed = (await c.get("/api/decision/clients")).json()["clients"]
    assert [x["name"] for x in listed] == ["laptop"] and "key" not in listed[0]
    assert created["key"] not in (await c.get("/api/decision/clients")).text
    cid = created["id"]
    assert (
        await c.post(f"/api/decision/clients/{cid}/rename", json={"name": "lap2"})
    ).status_code == 200
    assert (await c.post(f"/api/decision/clients/{cid}/revoke")).status_code == 200
    listed = (await c.get("/api/decision/clients")).json()["clients"]
    assert listed[0]["name"] == "lap2" and listed[0]["revoked_at"] is not None
    assert app.state.decision_keys.verify(f"Bearer {created['key']}") is None


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        ("/api/decision/clients", {"name": "bad name"}, 400),
        ("/api/decision/clients/999/revoke", None, 404),
        ("/api/decision/clients/999/rename", {"name": "x"}, 404),
    ],
)
async def test_client_errors(api, path, body, code):
    c, _ = api
    assert (await c.post(path, json=body)).status_code == code


async def test_duplicate_client_and_csrf(api):
    c, _ = api
    assert (await c.post("/api/decision/clients", json={"name": "a"})).status_code == 201
    assert (await c.post("/api/decision/clients", json={"name": "a"})).status_code == 409
    del c.headers["X-CSRF-Token"]
    assert (await c.post("/api/decision/clients", json={"name": "b"})).status_code == 403


async def test_usage_by_day_and_client(api, fake_decision):
    c, app = api
    fake_decision.start()
    alice = app.state.decision_keys.create("alice")
    hist = app.state.decision_history
    summary = {"n_questions": 3, "types": "choice", "steps": None, "samples": 2}
    import time as _t

    for status, code, lat in (("ok", None, 100.0), ("ok", None, 300.0), ("error", 429, 1.0)):
        hist.add_request(
            ts=_t.time(),
            source="gateway",
            summary=summary,
            input_tokens=50,
            latency_ms=lat,
            model_ms=None,
            status=status,
            error_code=code,
            client_id=str(alice["id"]),
            remote_addr="192.0.2.50",
        )
    assert (await c.post("/api/decision/read", json=READ)).status_code == 200
    by_client = (await c.get("/api/decision/usage?days=7&group=client")).json()["rows"]
    rows = {r["client"]: r for r in by_client}
    assert set(rows) == {"alice", "(admin page)"}
    a = rows["alice"]
    assert a["requests"] == 3 and a["errors"] == 1 and a["questions"] == 9
    assert a["input_tokens"] == 150 and a["p50_ms"] == 100.0 and a["p95_ms"] == 300.0
    by_day = (await c.get("/api/decision/usage?days=7&group=day")).json()["rows"]
    assert len(by_day) == 1 and by_day[0]["requests"] == 4
    assert (await c.get("/api/decision/usage?group=nope")).status_code == 422
    fake_decision.settings.decision_enabled = False
    assert (await c.get("/api/decision/usage")).status_code == 404


async def test_every_page_renders_with_the_sidecar_enabled_and_disabled(api, fake_decision):
    c, _ = api
    from local_llm_admin.app import PAGES

    for enabled in (True, False):
        fake_decision.settings.decision_enabled = enabled
        for route, page in PAGES.items():
            r = await c.get(route)
            off = (
                (page == "decision" and not enabled)
                or (page == "tts" and not fake_decision.settings.tts_enabled)
                or (page == "embed" and not fake_decision.settings.embed_enabled)
            )
            expected = 404 if off else 200
            assert r.status_code == expected, (route, enabled, r.text[:200])
        usage = (await c.get("/usage")).text
        assert ('id="decision-usage"' in usage) is enabled
