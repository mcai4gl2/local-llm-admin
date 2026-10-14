import asyncio
import os
import subprocess

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin.app import create_app

HEALTHY = {"status": "healthy", "loaded_model": "/Volumes/Models/models/active"}


@pytest.fixture
async def eng(fake_comfy):
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
            yield c, fake_comfy


def _cli(fake, *args):
    env = {**os.environ}
    return subprocess.run(
        [str(a) for a in args], env=env, capture_output=True, text=True, check=False
    )


def _start_mlx(fake):
    _cli(fake, fake.settings.mlx_model_path, "start", "qwen")


def _start_comfy(fake):
    _cli(fake, fake.settings.comfyui_path, "start")


@pytest.fixture
def mlx_health():
    import respx

    with respx.mock(assert_all_called=False) as r:
        r.route(host="mlx.test").mock(return_value=httpx.Response(200, json=HEALTHY))
        yield r


async def test_status_mlx_active(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    st = (await c.get("/api/status")).json()
    assert st["engine"]["active"] == "mlx" and st["engine"]["comfyui"]["running"] is False
    assert st["state"] == "serving"


async def test_status_comfyui_active(eng, mlx_health):
    c, fake = eng
    _start_comfy(fake)
    st = (await c.get("/api/status")).json()
    assert st["engine"]["active"] == "comfyui" and st["state"] == "comfyui"
    assert st["engine"]["comfyui"]["version"] == "0.37.0"


async def test_status_conflict(eng, mlx_health):
    c, fake = eng
    _start_comfy(fake)
    _cli(
        fake,
        "sh",
        "-c",
        f"sleep 60 >/dev/null 2>&1 & echo $! > {fake.settings.mlx_root}/run/mlx-server.pid",
    )
    st = (await c.get("/api/status")).json()
    assert st["engine"]["active"] == "conflict" and st["state"] == "conflict"


async def test_comfyui_card(eng):
    c, fake = eng
    (fake.root / "models/checkpoints/x.safetensors").write_bytes(b"x" * 10)
    (fake.root / "output/a.png").write_bytes(b"x" * 5)
    body = (await c.get("/api/comfyui")).json()
    assert body["running"] is False and body["public_url"] == fake.settings.comfyui_public_url
    assert body["weights"][0]["file"] == "x.safetensors" and body["output"] == {
        "files": 1,
        "bytes": 5,
    }


async def test_comfyui_log_source(eng):
    c, _ = eng
    ids = [s["id"] for s in (await c.get("/api/logs")).json()["sources"]]
    assert "comfyui" in ids
    r = await c.get("/api/logs/comfyui", params={"lines": 5})
    assert r.json()["lines"][0] == "Starting server"


async def test_chat_refused_while_comfyui_owns_gpu(eng, mlx_health):
    c, fake = eng
    _start_comfy(fake)
    r = await c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 409 and "ComfyUI" in r.json()["detail"]


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.02)


async def _job(c, job_id):
    for _ in range(300):
        j = (await c.get(f"/api/jobs/{job_id}")).json()
        if j["status"] != "running":
            return j
        await asyncio.sleep(0.02)
    raise AssertionError("stuck")


def _alive(pidfile):
    try:
        os.kill(int(pidfile.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


async def test_switch_mlx_to_comfyui_and_back(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    r = await c.post("/api/engine", json={"target": "comfyui"})
    assert r.status_code == 202
    j = await _job(c, r.json()["id"])
    assert j["status"] == "succeeded", j["output"]
    assert not _alive(fake.settings.mlx_root / "run/mlx-server.pid")
    assert _alive(fake.root / "run/comfyui.pid")
    r = await c.post("/api/engine", json={"target": "mlx:qwen"})
    j = await _job(c, r.json()["id"])
    assert j["status"] == "succeeded", j["output"]
    assert _alive(fake.settings.mlx_root / "run/mlx-server.pid") and not _alive(
        fake.root / "run/comfyui.pid"
    )
    hist = (await c.get("/api/history")).json()["history"]
    assert [(h["action"], h["from_name"], h["to_name"]) for h in hist[:2]] == [
        ("engine", "comfyui", "qwen"),
        ("engine", "qwen", "comfyui"),
    ]


async def test_comfyui_start_failure_rolls_back_to_mlx(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    (fake.state / "comfy_start_exit").write_text("1")
    j = await _job(c, (await c.post("/api/engine", json={"target": "comfyui"})).json()["id"])
    assert j["status"] == "failed" and j["rolled_back"] is True
    assert _alive(fake.settings.mlx_root / "run/mlx-server.pid")


async def test_comfyui_never_ready_rolls_back(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    (fake.state / "comfy_never_ready").write_text("")
    j = await _job(c, (await c.post("/api/engine", json={"target": "comfyui"})).json()["id"])
    assert j["status"] == "timeout" and j["rolled_back"] is True
    assert _alive(fake.settings.mlx_root / "run/mlx-server.pid") and not _alive(
        fake.root / "run/comfyui.pid"
    )


async def test_engine_switch_stop_failure_leaves_mlx_running(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    (fake.state / "stop_fails").write_text("")
    j = await _job(c, (await c.post("/api/engine", json={"target": "comfyui"})).json()["id"])
    assert j["status"] == "failed" and j["rolled_back"] is None
    assert _alive(fake.settings.mlx_root / "run/mlx-server.pid")
    assert not [x for x in fake.calls() if x == "comfyui start"]


async def test_queue_refusal_and_forced_switch(eng, mlx_health):
    c, fake = eng
    _start_comfy(fake)
    fake.queue["queue_running"], fake.queue["queue_pending"] = [[1]], [[2]]
    r = await c.post("/api/engine", json={"target": "mlx:qwen"})
    assert r.status_code == 409 and "2 queued or running" in r.json()["detail"]
    r = await c.post("/api/engine", json={"target": "mlx:qwen", "force": True})
    j = await _job(c, r.json()["id"])
    assert j["status"] == "succeeded" and j["steps"][0] == "cancel ComfyUI queue"
    assert fake.queue == {"queue_running": [], "queue_pending": []}


async def test_engine_switch_refused_when_queue_unreadable(eng, mlx_health):
    """Unknown queue state is never read as empty: an ordinary switch is refused, a
    confirmed one records the unknown state and rechecks before stopping."""
    c, fake = eng
    _start_comfy(fake)
    (fake.state / "comfy_ready").unlink()  # running but API not answering yet
    r = await c.post("/api/engine", json={"target": "off"})
    assert r.status_code == 409 and "queue state is unknown" in r.json()["detail"]
    assert "comfyui stop" not in fake.calls()
    r = await c.post("/api/engine", json={"target": "off", "force": True})
    j = await _job(c, r.json()["id"])
    assert j["status"] == "succeeded"
    assert j["steps"][0] == "cancel ComfyUI queue (queue state was unknown)"
    assert "optional, continuing" in j["output"]  # the API still did not answer


async def test_engine_switch_single_flight(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    first = await c.post("/api/engine", json={"target": "comfyui"})
    second = await c.post("/api/engine", json={"target": "off"})
    assert first.status_code == 202 and second.status_code == 409
    await _job(c, first.json()["id"])


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"target": "nonsense"}, 400),
        ({"target": "mlx:a;b"}, 400),
        ({"target": "mlx:nosuch"}, 404),
        ({"target": "off"}, 409),
    ],
)
async def test_engine_refusals(eng, body, code):
    c, _ = eng
    assert (await c.post("/api/engine", json=body)).status_code == code


async def test_engine_refused_when_mlx_lock_held(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    (fake.settings.mlx_root / "run/mlx.lock").mkdir()
    r = await c.post("/api/engine", json={"target": "comfyui"})
    assert r.status_code == 409 and "mlx.lock" in r.json()["detail"]


async def test_comfy_only_transition_ignores_mlx_lock(eng):
    c, fake = eng
    (fake.settings.mlx_root / "run/mlx.lock").mkdir()
    r = await c.post("/api/engine", json={"target": "comfyui"})
    assert r.status_code == 202
    assert (await _job(c, r.json()["id"]))["status"] == "succeeded"


async def test_mlx_actions_refused_while_comfyui_runs(eng):
    c, fake = eng
    _start_comfy(fake)
    for path, body in (("/api/models/switch", {"name": "qwen"}), ("/api/models/start", {})):
        r = await c.post(path, json=body)
        assert r.status_code == 409 and "Engine control" in r.json()["detail"]
    assert (await c.post("/api/models/stop")).status_code == 202  # stopping MLX is always allowed


async def test_engine_requires_csrf(eng):
    c, _ = eng
    token = c.headers.pop("X-CSRF-Token")
    assert (await c.post("/api/engine", json={"target": "off"})).status_code == 403
    c.headers["X-CSRF-Token"] = token


async def test_engine_submit_race_is_409(eng, monkeypatch):
    """Another job can start between the pre-check and the submit."""
    from local_llm_admin.jobs import JobConflict, JobRunner

    def raced(self, *a, **kw):
        raise JobConflict("other")

    monkeypatch.setattr(JobRunner, "submit_sequence", raced)
    c, _ = eng
    r = await c.post("/api/engine", json={"target": "comfyui"})
    assert r.status_code == 409 and "another job" in r.json()["detail"]


async def test_rollback_does_not_start_mlx_while_comfyui_still_runs(eng, mlx_health):
    """Review finding: the old engine must not start if the target never went down."""
    c, fake = eng
    _start_mlx(fake)
    (fake.state / "comfy_never_ready").write_text("")
    (fake.state / "comfy_stop_fails").write_text("")
    (fake.state / "no_guard").write_text("")  # mlx-model without the D-C1 patch
    j = await _job(c, (await c.post("/api/engine", json={"target": "comfyui"})).json()["id"])
    assert j["status"] == "timeout" and j["rolled_back"] is False
    assert _alive(fake.root / "run/comfyui.pid")
    assert not _alive(fake.settings.mlx_root / "run/mlx-server.pid")
    assert "==> mlx-model start" not in j["output"]


async def test_status_reports_intent_after_engine_switch(eng, mlx_health):
    c, fake = eng
    _start_mlx(fake)
    j = await _job(c, (await c.post("/api/engine", json={"target": "comfyui"})).json()["id"])
    assert j["status"] == "succeeded" and j["source"] == "admin"
    st = (await c.get("/api/status")).json()
    assert st["intent"]["value"] == "comfyui" and st["recovery_blocked"] is None


async def test_reservation_storage_failure_is_503(eng, monkeypatch):
    from local_llm_admin.jobs import JobStore

    def broken(self, job):
        raise OSError("read-only database")

    monkeypatch.setattr(JobStore, "save_job", broken)
    c, fake = eng
    r = await c.post("/api/models/stop")
    assert r.status_code == 503 and fake.calls() == []


async def test_admin_restart_reconciles_interrupted_job(fake_comfy, mlx_health):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    from local_llm_admin.jobs import JobStore

    store = JobStore(s.admin_data_dir / "admin.sqlite")
    store.set_kv("intent", {"value": "mlx:qwen"})
    store.save_job(
        {
            "id": "lost1",
            "action": "switch",
            "name": "gemma",
            "from_name": "qwen",
            "to_name": "gemma",
            "source": "admin",
            "status": "running",
            "returncode": None,
            "started": 1.0,
            "finished": None,
            "rolled_back": None,
            "observed_engine": None,
            "output": "",
        }
    )
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            assert (await c.get("/api/jobs/lost1")).json()["status"] == "interrupted"
            st = (await c.get("/api/status")).json()
            assert st["intent"]["value"] == "unknown" and "interrupted" in st["recovery_blocked"]
    assert set(fake_comfy.calls()) <= {"status", "list"}  # read-only CLI calls; no replay
