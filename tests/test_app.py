import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from local_llm_admin import auth
from local_llm_admin.app import create_app

from .fake_openai import FakeOpenAI, sse_chunks

FIX = Path(__file__).parent / "fixtures"
KEY = "sk-viewer-SECRET-123"
HEALTHY = {
    "status": "healthy",
    "loaded_model": "/Volumes/Models/models/active",
    "loaded_context_size": 32768,
}


@pytest.fixture
def settings(fake_mlx):
    s = fake_mlx.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "test-secret"
    return s


@pytest.fixture
def fake_llm():
    return FakeOpenAI(sse_chunks(["Hi", "!"], usage={"prompt_tokens": 5, "completion_tokens": 2}))


@pytest.fixture
async def app(settings, fake_llm):
    app = create_app(settings, start_background=False, chat_transport=fake_llm.transport)
    app.state.follow_interval_s = 0.05
    app.state.follow_max_s = 0.6
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        await app.state.backfill_task  # background at startup; wait so tests see the rows
        yield app


@pytest.fixture
def mlx_health():
    with respx.mock(assert_all_called=False) as router:
        router.route(host="mlx.test", path="/health").mock(
            return_value=httpx.Response(200, json=HEALTHY)
        )
        yield router


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    )


@pytest.fixture
async def anon(app):
    async with _client(app) as c:
        yield c


@pytest.fixture
async def client(app):
    async with _client(app) as c:
        r = await c.post("/login", data={"password": "pw"})
        assert r.status_code == 303
        csrf = (await c.get("/api/session")).json()["csrf"]
        c.headers["X-CSRF-Token"] = csrf
        yield c


async def _wait_job(client, job_id):
    for _ in range(200):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("job stuck")


# --- auth -------------------------------------------------------------------


async def test_healthz_is_public(anon):
    r = await anon.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


@pytest.mark.parametrize(
    "path",
    [
        "/api/status",
        "/api/models",
        "/api/logs/mlx-server",
        "/api/usage/daily",
        "/api/metrics/current",
        "/api/session",
    ],
)
async def test_api_requires_login(anon, path):
    assert (await anon.get(path)).status_code == 401


async def test_pages_redirect_to_login(anon):
    r = await anon.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert (await anon.get("/login")).status_code == 200


async def test_login_sets_strict_httponly_cookie(anon):
    r = await anon.post("/login", data={"password": "pw"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert r.headers["location"] == "/"


async def test_wrong_password_then_rate_limit(anon):
    for _ in range(5):
        r = await anon.post("/login", data={"password": "nope"})
        assert r.status_code == 401
    r = await anon.post("/login", data={"password": "pw"})
    assert r.status_code == 429


async def test_login_disabled_without_hash(settings, fake_llm):
    settings.admin_password_hash = ""
    app = create_app(settings, start_background=False)
    async with _client(app) as c:
        assert (await c.post("/login", data={"password": ""})).status_code == 401


async def test_post_requires_csrf(client):
    token = client.headers.pop("X-CSRF-Token")
    r = await client.post("/api/models/stop")
    assert r.status_code == 403
    r = await client.post("/api/models/stop", headers={"X-CSRF-Token": token + "x"})
    assert r.status_code == 403


async def test_logout(client):
    r = await client.post("/logout")
    assert r.status_code == 303
    client.cookies.clear()
    assert (await client.get("/api/status")).status_code == 401


async def test_pages_render(client, mlx_health):
    for path in ("/", "/models", "/chat", "/logs", "/usage"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert 'name="csrf-token"' in r.text
    assert (await client.get("/static/app.js")).status_code == 200


# --- status / metrics -------------------------------------------------------


async def test_status_serving(client, mlx_health):
    st = (await client.get("/api/status")).json()
    assert st["state"] == "serving" and st["selected"] == "qwen"
    assert st["job"] is None


async def test_metrics(client):
    cur = (await client.get("/api/metrics/current")).json()
    assert cur["mem_total"] > 0
    hist = (await client.get("/api/metrics/history", params={"window": "15m"})).json()
    assert isinstance(hist["points"], list)
    assert (await client.get("/api/metrics/history", params={"window": "9y"})).status_code == 400


# --- models / jobs ----------------------------------------------------------


async def test_models_list(client):
    models = (await client.get("/api/models")).json()["models"]
    assert [m["name"] for m in models] == ["qwen", "gemma"]


async def test_switch_runs_job_and_records_history(client, fake_mlx):
    r = await client.post("/api/models/switch", json={"name": "gemma"})
    assert r.status_code == 202
    job = await _wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded"
    assert "switch gemma" in fake_mlx.calls()
    hist = (await client.get("/api/history")).json()["history"]
    assert hist[0]["action"] == "switch" and hist[0]["to_name"] == "gemma"
    assert {h["source"] for h in hist} == {"admin", "log"}  # log markers backfilled at startup


@pytest.mark.parametrize(("name", "code"), [("x;rm -rf /", 400), ("$(id)", 400), ("nosuch", 404)])
async def test_switch_rejects_bad_names(client, fake_mlx, name, code):
    r = await client.post("/api/models/switch", json={"name": name})
    assert r.status_code == code
    assert not [c for c in fake_mlx.calls() if c.startswith("switch")]


async def test_switch_refused_when_locked(client, fake_mlx, mlx_health):
    (fake_mlx.root / "run/mlx.lock").mkdir()
    r = await client.post("/api/models/switch", json={"name": "gemma"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "run/mlx.lock" in detail and "remove" in detail and "min" in detail
    st = (await client.get("/api/status")).json()
    assert st["lock"] is True and st["lock_age_s"] >= 0


async def test_downloads_summary_covers_every_download_log(client, fake_mlx):
    (fake_mlx.root / "logs/download-variant.log").write_text(
        "===== 2026-09-30 10:00:00 start =====\nGET org/m/config.json\n"
        " 42 4000M   42 1680M    0     0  9000k      0  0:07:35  0:03:11  0:04:24 9.1M\n"
    )
    body = (await client.get("/api/downloads")).json()
    (entry,) = body["logs"]
    assert entry["id"] == "download-variant"
    assert entry["active"] is True and entry["last_progress"]["percent"] == 42
    sources = (await client.get("/api/logs")).json()["sources"]
    assert "download-variant" in [s["id"] for s in sources]
    r = await client.get("/api/logs/download-variant", params={"lines": 5})
    assert r.status_code == 200 and r.json()["lines"][0].startswith("=====")


# --- discovery & registration --------------------------------------------------


def _weights(root, name, complete=True, age_s=3600):
    import json as _json
    import os as _os
    import time as _time

    d = root / "models" / name
    d.mkdir(parents=True)
    (d / "config.json").write_text(_json.dumps({"model_type": "qwen3"}))
    (d / "model.safetensors.index.json").write_text(
        _json.dumps({"metadata": {"total_size": 100}, "weight_map": {"a": "a.safetensors"}})
    )
    with open(d / "a.safetensors", "wb") as fh:
        fh.truncate(100 if complete else 40)
    t = _time.time() - age_s
    for p in d.iterdir():
        _os.utime(p, (t, t))
    return d


async def test_new_download_appears_without_restart(client, fake_mlx):
    before = (await client.get("/api/models")).json()
    assert [d["dir_name"] for d in before["discovered"]] == ["Qwen"]
    _weights(fake_mlx.root, "NewModel-4bit", complete=False, age_s=1)
    after = (await client.get("/api/models")).json()
    new = next(d for d in after["discovered"] if d["dir_name"] == "NewModel-4bit")
    assert new["state"] == "downloading" and new["progress"] == 0.4 and new["registered_as"] is None
    qwen = next(d for d in after["discovered"] if d["dir_name"] == "Qwen")
    assert qwen["registered_as"] == "qwen"
    assert next(m for m in after["models"] if m["name"] == "qwen")["weights"]["state"] in (
        "complete",
        "unknown",
        "no-weights",
    )


async def test_register_complete_model_via_mlx_model_add(client, fake_mlx):
    d = _weights(fake_mlx.root, "NewModel-4bit")
    r = await client.post(
        "/api/models/register", json={"dir_name": "NewModel-4bit", "name": "newmodel-4bit"}
    )
    assert r.status_code == 202
    job = await _wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded"
    assert f"add newmodel-4bit {d}" in fake_mlx.calls()
    models = (await client.get("/api/models")).json()
    assert "newmodel-4bit" in [m["name"] for m in models["models"]]
    assert (
        next(x for x in models["discovered"] if x["dir_name"] == "NewModel-4bit")["registered_as"]
        == "newmodel-4bit"
    )


@pytest.mark.parametrize(
    ("body", "code", "text"),
    [
        ({"dir_name": "Missing", "name": "x"}, 404, "not found"),
        ({"dir_name": "../Qwen", "name": "x"}, 404, "not found"),
        ({"dir_name": "Partial", "name": "partial"}, 409, "downloading"),
        ({"dir_name": "Qwen", "name": "qwen-again"}, 409, "already registered"),
        ({"dir_name": "Done", "name": "qwen"}, 409, "name is already used"),
        ({"dir_name": "Done", "name": "bad;name"}, 400, "invalid"),
    ],
)
async def test_register_refusals(client, fake_mlx, body, code, text):
    _weights(fake_mlx.root, "Partial", complete=False, age_s=1)
    _weights(fake_mlx.root, "Done")
    r = await client.post("/api/models/register", json=body)
    assert r.status_code == code and text in r.json()["detail"]
    assert not [c for c in fake_mlx.calls() if c.startswith("add")]


async def test_switch_refused_when_drive_missing(client, fake_mlx):
    fake_mlx.settings.mlx_root_must_be_mount = True
    r = await client.post("/api/models/stop")
    assert r.status_code == 409 and "drive" in r.json()["detail"]


async def test_switch_refused_while_job_running(client, fake_mlx):
    fake_mlx.set_delay(0.5)
    first = await client.post("/api/models/stop")
    assert first.status_code == 202
    r = await client.post("/api/models/start", json={})
    assert r.status_code == 409
    await _wait_job(client, first.json()["id"])


async def test_start_with_and_without_name(client, fake_mlx):
    j = await client.post("/api/models/start", json={"name": "gemma"})
    await _wait_job(client, j.json()["id"])
    j = await client.post("/api/models/start")
    await _wait_job(client, j.json()["id"])
    assert fake_mlx.calls()[-2:] == ["start gemma", "start"]


async def test_unknown_job(client):
    assert (await client.get("/api/jobs/nope")).status_code == 404


# --- logs -------------------------------------------------------------------


async def test_logs_list_and_tail(client):
    sources = (await client.get("/api/logs")).json()["sources"]
    assert [s["id"] for s in sources] == ["mlx-server", "chat-ui", "serve", "admin", "comfyui"]
    assert sources[0]["exists"] is True and sources[1]["exists"] is False
    r = await client.get("/api/logs/mlx-server", params={"lines": 3})
    assert len(r.json()["lines"]) == 3
    r = await client.get("/api/logs/mlx-server", params={"grep": "decode", "lines": 500})
    assert r.json()["lines"] == [
        ln for ln in (FIX / "mlx-server.log").read_text().splitlines() if "Decode" in ln
    ]
    r = await client.get("/api/logs/mlx-server", params={"level": "WARNING"})
    assert r.json()["lines"] == ["2026-09-25 12:00:30,000 WARNING slow load"]


@pytest.mark.parametrize("sid", ["..%2F..%2Fetc%2Fpasswd", "nope", "mlx-server.log"])
async def test_logs_reject_unknown_sources(client, sid):
    assert (await client.get(f"/api/logs/{sid}")).status_code == 404


async def test_logs_param_validation(client):
    assert (await client.get("/api/logs/mlx-server", params={"level": "TRACE"})).status_code == 422
    assert (await client.get("/api/logs/mlx-server", params={"lines": 0})).status_code == 422
    assert (await client.get("/api/logs/mlx-server", params={"lines": 99999})).status_code == 422


async def test_logs_download(client):
    r = await client.get("/api/logs/mlx-server/download", params={"lines": 2})
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.text.count("\n") == 2


async def test_logs_follow_streams_appended_lines(client, fake_mlx):
    log = fake_mlx.root / "logs/mlx-server.log"

    async def append():
        await asyncio.sleep(0.15)
        with log.open("a") as fh:
            fh.write("2026-09-29 10:00:00,000 INFO Decode completed id=z rate=20.0 tok/s\n")

    task = asyncio.create_task(append())
    r = await client.get("/api/logs/mlx-server/follow")
    await task
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert events[0]["type"] == "info"
    assert {
        "type": "line",
        "line": "2026-09-29 10:00:00,000 INFO Decode completed id=z rate=20.0 tok/s",
    } in events


# --- chat -------------------------------------------------------------------


async def test_chat_streams(client, mlx_health, fake_llm):
    r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert "".join(e["content"] for e in events if e["type"] == "delta") == "Hi!"
    assert events[-1]["type"] == "done"
    assert fake_llm.requests[0]["messages"] == [{"role": "user", "content": "hello"}]


async def test_chat_refused_when_not_serving(client):
    with respx.mock(assert_all_called=False) as router:
        router.route(host="mlx.test").mock(side_effect=httpx.ConnectError("down"))
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 409


async def test_chat_validation(client, mlx_health):
    r = await client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "x"}], "max_tokens": 32769}
    )
    assert r.status_code == 422


# --- usage ------------------------------------------------------------------


@pytest.fixture
def litellm():
    with respx.mock(assert_all_called=False) as router:
        router.route(host="mlx.test").mock(return_value=httpx.Response(200, json=HEALTHY))
        router.get("http://litellm.test/spend/logs/v2").mock(
            side_effect=lambda req: httpx.Response(
                200,
                json=json.loads(
                    (
                        FIX
                        / (
                            "spend_logs_recorded.json"
                            if req.url.params["page_size"] == "1000"
                            else f"spend_logs_p{req.url.params['page']}.json"
                        )
                    ).read_text()
                ),
            )
        )
        router.get("http://litellm.test/health/liveliness").mock(return_value=httpx.Response(200))
        yield router


async def test_usage_daily_with_active_models(client, litellm):
    r = await client.get("/api/usage/daily", params={"from": "2026-09-28", "to": "2026-09-29"})
    assert r.status_code == 200
    body = r.json()
    assert [(d["date"], d["api_requests"]) for d in body["days"]] == [
        ("2026-09-28", 13),
        ("2026-09-29", 1),
    ]
    assert body["days"][0]["active_models"] == ["qwen3.8-27b"]  # from backfilled log markers
    # one model all day: the route's tokens are exactly that model's (M3a)
    assert body["days"][0]["attributed_model"] == "qwen3.8-27b"


async def test_usage_bad_range(client, litellm):
    r = await client.get("/api/usage/daily", params={"from": "2026-01-01", "to": "2026-09-28"})
    assert r.status_code == 400
    r = await client.get("/api/usage/daily", params={"from": "1; DROP TABLE"})
    assert r.status_code == 400


async def test_usage_sessions_and_health(client, litellm):
    r = await client.get("/api/usage/sessions", params={"from": "2026-09-28", "to": "2026-09-29"})
    assert sum(s["requests"] for s in r.json()["sessions"]) == 14
    h = (await client.get("/api/usage/health")).json()
    assert h == {"reachable": True, "key_ok": True, "error": None}


async def test_usage_rejected_key_is_contained_and_never_leaks(client, settings):
    with respx.mock(assert_all_called=False) as router:
        router.route(host="mlx.test").mock(return_value=httpx.Response(200, json=HEALTHY))
        router.route(host="litellm.test").mock(return_value=httpx.Response(401, json={"k": KEY}))
        r = await client.get("/api/usage/daily")
        assert r.status_code == 502
        assert r.json()["error"] == "auth"
        assert KEY not in r.text
        assert (await client.get("/api/status")).status_code == 200  # rest keeps working
    log_text = (settings.admin_log_dir / "admin.log").read_text()
    assert KEY not in log_text


async def test_key_never_in_any_response(client, litellm):
    for path in (
        "/",
        "/usage",
        "/api/status",
        "/api/usage/daily",
        "/api/usage/health",
        "/api/logs/admin",
        "/api/session",
    ):
        r = await client.get(path)
        assert KEY not in r.text, path


async def test_startup_does_not_wait_for_the_model_drive(settings, fake_llm, monkeypatch):
    """A macOS privacy prompt can block the first read of /Volumes/Models indefinitely."""
    import time as _time

    import local_llm_admin.app as app_mod

    monkeypatch.setattr(app_mod, "_backfill", lambda s, st: _time.sleep(3) or 0)
    app = create_app(settings, start_background=False)
    t0 = _time.monotonic()
    async with app.router.lifespan_context(app):
        assert _time.monotonic() - t0 < 1.0
        async with _client(app) as c:
            assert (await c.get("/healthz")).status_code == 200


async def test_permission_denied_is_reported_not_500(client, fake_mlx, mlx_health):
    import os as _os

    logs_dir = fake_mlx.root / "logs"
    run_dir = fake_mlx.root / "run"
    _os.chmod(logs_dir, 0)
    _os.chmod(run_dir, 0)
    try:
        st = await client.get("/api/status")
        assert st.status_code == 200
        body = st.json()
        assert body["access_error"] and "Removable Volumes" in body["access_error"]
        assert (await client.get("/api/logs/mlx-server")).status_code == 200
        assert (await client.get("/api/logs")).status_code == 200
    finally:
        _os.chmod(logs_dir, 0o755)
        _os.chmod(run_dir, 0o755)


async def test_theme_switch_on_every_page(client, anon, mlx_health):
    pages = [await anon.get("/login")] + [
        await client.get(p) for p in ("/", "/models", "/chat", "/logs", "/usage")
    ]
    for r in pages:
        html = r.text
        assert 'id="theme-select"' in html
        # applied in <head>, before the stylesheet paints, so a saved theme never flashes
        head = html.split("</head>")[0]
        assert "localStorage" in head and "dataset.theme" in head
    assert (await client.get("/static/theme.js")).status_code == 200


async def test_engine_ui_elements_render(client, mlx_health):
    models = (await client.get("/models")).text
    for el_id in ("engine-card", "engine-target", "engine-apply", "comfy-card", "comfy-weights"):
        assert f'id="{el_id}"' in models
    assert 'id="conflict-banner"' in (await client.get("/")).text


async def test_models_page_has_the_sidecar_card(client, fake_tts):
    html = (await client.get("/models")).text
    assert 'id="sidecar-card"' in html and 'id="sidecar-select"' in html
    assert 'id="decision-card"' not in html


async def test_tts_page_and_nav(client, fake_tts):
    html = (await client.get("/tts")).text
    assert 'id="tts-form"' in html and 'id="tts-text"' in html and "/static/tts.js" in html
    assert 'href="/tts"' in (await client.get("/")).text
    assert 'id="tts-usage"' in (await client.get("/usage")).text
