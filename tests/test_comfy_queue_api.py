import copy
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import comfy_queue as cq
from local_llm_admin.app import create_app

FIX = Path(__file__).parent / "fixtures" / "comfyui"
RUNNING = "2b1f0c4e-9a53-4d8e-a1a4-3e2d6f7a8b90"
P1 = "5c8d2e11-4a3b-4c2d-8e7f-9a0b1c2d3e4f"
P2 = "7d9e1a22-5b6c-4f3a-9e8d-0a1b2c3d4e5f"
UNKNOWN = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
async def q(fake_comfy):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.backfill_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
            yield c, fake_comfy, app


def _comfy(fake, action):
    subprocess.run([str(fake.settings.comfyui_path), action], capture_output=True, check=False)


def _load(fake):
    fake.queue.clear()
    fake.queue.update(copy.deepcopy(json.loads((FIX / "queue.json").read_text())))


def _ids(fake, key):
    return [i[1] for i in fake.queue[key]]


def _posts(fake):
    return [(p, b) for m, p, b in fake.flags.get("requests", []) if m == "POST"]


async def _session(c):
    snap = (await c.get("/api/comfyui/queue")).json()
    assert snap["state"] == "ok", snap
    return snap["session"]


async def _history(c):
    return [h for h in (await c.get("/api/history")).json()["history"] if h["source"] == "admin"]


async def test_snapshot_states(q):
    c, fake, _app = q
    snap = (await c.get("/api/comfyui/queue")).json()
    assert snap["state"] == "stopped" and snap["session"] is None
    _comfy(fake, "start")
    _load(fake)
    snap = (await c.get("/api/comfyui/queue")).json()
    assert snap["state"] == "ok" and snap["capabilities"] == {"per_job_cancel": True}
    assert snap["running_count"] == 1 and [p["id"] for p in snap["pending"]] == [P1, P2]
    assert snap["session"].startswith(open(fake.root / "run/comfyui.pid").read().strip())
    assert snap["job_running"] is False and snap["truncated"] is False
    assert "KSampler" not in json.dumps(snap)


async def test_delete_pending_is_targeted_and_recorded(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post(
        "/api/comfyui/queue/delete", json={"ids": [P1, RUNNING, UNKNOWN], "session": session}
    )
    assert r.status_code == 200
    assert r.json()["results"] == {P1: "removed", RUNNING: "running", UNKNOWN: "not_pending"}
    assert _ids(fake, "queue_pending") == [P2] and _ids(fake, "queue_running") == [RUNNING]
    assert _posts(fake) == [("/queue", {"delete": [P1]})]  # never clear, never interrupt
    (h,) = await _history(c)
    assert h["action"] == "comfyui-queue" and h["result"] == "succeeded"
    assert "removed=1" in h["output_tail"] and "KSampler" not in h["output_tail"]


async def test_delete_finished_id_is_idempotent(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    fake.queue["queue_pending"] = fake.queue["queue_pending"][:1]  # P2 finished meanwhile
    r = await c.post("/api/comfyui/queue/delete", json={"ids": [P1], "session": session})
    assert r.json()["results"] == {P1: "not_pending"}
    assert _posts(fake) == []


@pytest.mark.parametrize(
    "ids", [[], ["../x"], [RUNNING.upper()], [1], "x", [UNKNOWN] * (cq.MAX_IDS + 1)]
)
async def test_delete_malformed_ids(q, ids):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post("/api/comfyui/queue/delete", json={"ids": ids, "session": session})
    assert r.status_code in (400, 422)
    assert _posts(fake) == []


async def test_stale_session_after_engine_restart_is_refused(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    old = await _session(c)
    _comfy(fake, "stop")
    _comfy(fake, "start")  # a new ComfyUI process: the browser's view is stale
    _load(fake)
    for path, body in (
        ("/api/comfyui/queue/delete", {"ids": [P1]}),
        ("/api/comfyui/queue/clear", {}),
        ("/api/comfyui/queue/cancel", {"id": RUNNING}),
        ("/api/comfyui/interrupt", {"expected_running": [RUNNING]}),
    ):
        for session in (old, None):
            r = await c.post(path, json={**body, "session": session})
            assert r.status_code == 409 and "refresh" in r.json()["detail"], path
    assert _posts(fake) == []
    assert _ids(fake, "queue_pending") == [P2, P1]


async def test_mutation_refused_when_comfy_stopped(q):
    c, fake, _app = q
    r = await c.post("/api/comfyui/queue/clear", json={"session": "1-2"})
    assert r.status_code == 409 and "not running" in r.json()["detail"]


async def test_unknown_queue_is_not_empty_and_blocks_mutation(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    (fake.state / "comfy_ready").unlink()  # API stops answering
    snap = (await c.get("/api/comfyui/queue")).json()
    assert snap["state"] == "unknown" and "running" not in snap
    r = await c.post("/api/comfyui/queue/clear", json={"session": session})
    assert r.status_code == 503 and "nothing was changed" in r.json()["detail"]


async def test_clear_removes_only_pending_seen_at_recheck(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post("/api/comfyui/queue/clear", json={"session": session})
    assert r.status_code == 200 and r.json()["results"] == {P2: "removed", P1: "removed"}
    assert fake.queue["queue_pending"] == [] and _ids(fake, "queue_running") == [RUNNING]
    assert _posts(fake) == [("/queue", {"delete": [P1, P2]})]  # not upstream {"clear": true}


async def test_clear_reports_unconfirmed_when_reread_fails(q, monkeypatch):
    c, fake, app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    comfy = app.state.control.comfy
    real = comfy.queue_raw
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        return await real() if calls["n"] == 1 else None

    monkeypatch.setattr(comfy, "queue_raw", flaky)
    r = await c.post("/api/comfyui/queue/clear", json={"session": session})
    assert r.json()["results"] == {P1: "unconfirmed", P2: "unconfirmed"}


async def test_cancel_running_uses_atomic_per_job_cancel(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post("/api/comfyui/queue/cancel", json={"id": RUNNING, "session": session})
    assert r.json() == {"result": "cancelled", "atomic": True}
    assert _posts(fake) == [(f"/api/jobs/{RUNNING}/cancel", {})]
    assert fake.queue["queue_running"] == [] and len(fake.queue["queue_pending"]) == 2


async def test_cancel_running_falls_back_on_older_api(q):
    c, fake, _app = q
    fake.flags["no_jobs_api"] = True
    _comfy(fake, "start")
    _load(fake)
    snap = (await c.get("/api/comfyui/queue")).json()
    assert snap["capabilities"] == {"per_job_cancel": False}
    r = await c.post("/api/comfyui/queue/cancel", json={"id": RUNNING, "session": snap["session"]})
    assert r.json() == {"result": "interrupt_requested", "atomic": False}
    assert _posts(fake) == [("/interrupt", {"prompt_id": RUNNING})]


async def test_cancel_not_running_is_a_no_op(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    for job in (P1, UNKNOWN):
        r = await c.post("/api/comfyui/queue/cancel", json={"id": job, "session": session})
        assert r.json() == {"result": "not_running"}
    r = await c.post("/api/comfyui/queue/cancel", json={"id": "bad", "session": session})
    assert r.status_code == 400
    assert _posts(fake) == []


async def test_global_interrupt_checks_expected_running(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post("/api/comfyui/interrupt", json={"expected_running": [], "session": session})
    assert r.status_code == 409 and "changed" in r.json()["detail"]
    r = await c.post("/api/comfyui/interrupt", json={"expected_running": ["x"], "session": session})
    assert r.status_code == 400
    assert _posts(fake) == []
    r = await c.post(
        "/api/comfyui/interrupt", json={"expected_running": [RUNNING], "session": session}
    )
    assert r.json() == {"result": "interrupted", "global": True}
    assert _posts(fake) == [("/interrupt", {})]
    assert fake.queue["queue_running"] == [] and len(fake.queue["queue_pending"]) == 2
    r = await c.post("/api/comfyui/interrupt", json={"expected_running": [], "session": session})
    assert r.json() == {"result": "nothing_running"}


async def test_upstream_failure_is_reported_and_recorded(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    fake.flags["fail_post"] = True
    r = await c.post("/api/comfyui/queue/delete", json={"ids": [P1], "session": session})
    assert r.status_code == 502 and "may or may not" in r.json()["detail"]
    (h,) = await _history(c)
    assert h["result"] == "unknown" and "HTTPStatusError" in h["output_tail"]


async def test_upstream_timeout(q, monkeypatch):
    c, fake, app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)

    async def timeout(*_a, **_k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(app.state.control.comfy, "post", timeout)
    r = await c.post(
        "/api/comfyui/interrupt", json={"expected_running": [RUNNING], "session": session}
    )
    assert r.status_code == 502


async def test_mutation_waits_for_engine_jobs(q):
    """Queue mutations take the same slot as engine transitions."""
    c, fake, app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    runner = app.state.control.runner
    claim = runner.claim()
    try:
        r = await c.post("/api/comfyui/queue/clear", json={"session": session})
        assert r.status_code == 409
        r = await c.post("/api/engine", json={"target": "off", "force": True})
        assert r.status_code == 409  # and the other way round
    finally:
        runner.release(claim)
    assert _posts(fake) == []


async def test_forced_engine_switch_still_cancels_queue(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    r = await c.post("/api/engine", json={"target": "off"})
    assert r.status_code == 409 and "3 queued or running" in r.json()["detail"]
    r = await c.post("/api/engine", json={"target": "off", "force": True})
    assert r.status_code == 202


class _StubComfy:
    """A pending item starts executing between the re-read and the delete."""

    def __init__(self):
        self.queue = {
            "queue_running": [],
            "queue_pending": [[1, P1, {}, {}, []], [2, P2, {}, {}, []]],
        }

    async def queue_raw(self):
        return copy.deepcopy(self.queue)

    async def post(self, path, body):
        assert path == "/queue"
        started = self.queue["queue_pending"].pop(0)  # P1 started first
        self.queue["queue_running"].append(started)
        self.queue["queue_pending"] = [
            i for i in self.queue["queue_pending"] if i[1] not in body["delete"]
        ]


async def test_delete_reports_item_that_started_meanwhile():
    stub = _StubComfy()
    svc = cq.ComfyQueue(stub, None, None)  # type: ignore[arg-type]
    snap = cq.parse_queue(await stub.queue_raw())
    assert await svc._delete(snap, [P1, P2]) == {P1: "started", P2: "removed"}


async def test_history_write_failure_does_not_fail_the_action(q, monkeypatch):
    c, fake, app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)

    def broken(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(app.state.control.store, "add_history", broken)
    r = await c.post("/api/comfyui/queue/delete", json={"ids": [P1], "session": session})
    assert r.status_code == 200 and r.json()["results"] == {P1: "removed"}


async def test_cancel_with_unreadable_upstream_reply(q, monkeypatch):
    c, fake, app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)

    async def text_reply(path, body):
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(app.state.control.comfy, "post", text_reply)
    r = await c.post("/api/comfyui/queue/cancel", json={"id": RUNNING, "session": session})
    assert r.json() == {"result": "not_running", "atomic": True}


async def test_interrupt_expected_list_is_bounded(q):
    c, fake, _app = q
    _comfy(fake, "start")
    _load(fake)
    session = await _session(c)
    r = await c.post(
        "/api/comfyui/interrupt",
        json={"expected_running": [UNKNOWN] * (cq.MAX_IDS + 1), "session": session},
    )
    assert r.status_code == 400 and _posts(fake) == []
