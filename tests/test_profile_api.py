"""M3c API: profiles through the real mlx-model/helper; chat precedence; M2 staleness."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.app import create_app
from local_llm_admin.config import Settings
from local_llm_admin.profiles import (
    ProfileError,
    ProfileService,
    build_profile_argv,
    verified_efforts,
)
from local_llm_admin.quality import settings_hash
from local_llm_admin.quality_store import QualityStore

from .fake_openai import FakeOpenAI, sse_chunks
from .host_harness import HOST, make_host, redirect


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = make_host(tmp_path)
    redirect(HOST / "bin/mlx-profile", h.root / "bin/mlx-profile", h.root)
    (h.state / "healthy").write_text("")
    for k in ("PATH", "FAKE_HOST_STATE"):
        monkeypatch.setenv(k, h.env[k])
    yield h
    h.cleanup()


@pytest.fixture
def fake_llm():
    return FakeOpenAI(sse_chunks(["Hi"], usage={"prompt_tokens": 5, "completion_tokens": 1}))


@pytest.fixture
def settings(host, tmp_path):
    return Settings(
        mlx_root=host.root,
        mlx_model_bin=host.cli,
        mlx_root_must_be_mount=False,
        mlx_api_base="http://mlx.test",
        chat_ui_port=1,
        mlx_port=1,
        comfyui_root=tmp_path / "comfyui",
        comfyui_bin=tmp_path / "no-comfyui",
        comfyui_port=1,
        comfyui_api_base="http://comfy.test",
        admin_data_dir=tmp_path / "data",
        admin_log_dir=tmp_path / "adminlogs",
        admin_password_hash=auth.hash_password("pw"),
        admin_session_secret="test-secret",
    )


@pytest.fixture
async def app(settings, fake_llm):
    app = create_app(settings, start_background=False, chat_transport=fake_llm.transport)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
        timeout=60,
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        c.headers["x-csrf-token"] = (await c.get("/api/session")).json()["csrf"]
        yield c


async def _job(client, r):
    assert r.status_code == 202, r.text
    for _ in range(200):
        job = (await client.get(f"/api/jobs/{r.json()['id']}")).json()
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish")


def _compat(settings, model, results, profile=None):
    q = QualityStore(settings.admin_data_dir / "admin.sqlite")
    q.create_run(
        {
            "id": f"run-{time.time_ns()}",
            "kind": "compatibility",
            "scope": "current",
            "model": model,
            "status": "succeeded",
            "started": time.time(),
        }
    )
    run_id = q.list_runs(1)[0]["id"]
    for key, status in results.items():
        cid = q.add_case(
            run_id,
            model=model,
            model_path=None,
            fingerprint=None,
            case_key=key,
            kind="compatibility",
            settings_hash=None,
            cache_state=None,
            load_s=None,
            profile_fingerprint=profile,
        )
        q._finish_case(cid, status, None, None)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/models/a/profile"),
        ("POST", "/api/models/a/profile"),
        ("POST", "/api/models/a/profile/reset"),
        ("POST", "/api/models/a/profile/revert"),
    ],
)
async def test_endpoints_require_login(app, method, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.request(method, path, json={})).status_code == 401


async def test_save_does_not_restart_and_shows_the_mismatch(client, host):
    assert host.run("start", "a").returncode == 0
    before = (host.state / "server_args").read_text()
    view = (await client.get("/api/models/a/profile")).json()
    assert view["profile"]["fingerprint"] == "default" and view["running"] is True
    assert view["running_fingerprint"] == "default" and view["restart_required"] is False
    assert {f["name"] for f in view["schema"]["launch"]} >= {"max_kv_size"}
    job = await _job(
        client,
        await client.post(
            "/api/models/a/profile", json={"launch": {"max_kv_size": 16384}, "chat": {}}
        ),
    )
    assert job["status"] == "succeeded" and job["action"] == "profile"
    assert (host.state / "server_args").read_text() == before  # nothing restarted
    view = (await client.get("/api/models/a/profile")).json()
    assert view["profile"]["launch"] == {"max_kv_size": 16384}
    assert view["restart_required"] is True
    # the separate Restart (a switch to the same model) applies it
    job = await _job(client, await client.post("/api/models/switch", json={"name": "a"}))
    assert job["status"] == "succeeded", job["output"]
    assert "--max-kv-size" in (host.state / "server_args").read_text()
    assert (await client.get("/api/models/a/profile")).json()["restart_required"] is False
    models = (await client.get("/api/models")).json()["models"]
    assert next(m for m in models if m["name"] == "a")["profile_fingerprint"] != "default"
    assert next(m for m in models if m["name"] == "b")["profile_fingerprint"] == "default"


async def test_invalid_settings_fail_the_job_and_change_nothing(client, host):
    job = await _job(
        client,
        await client.post("/api/models/a/profile", json={"launch": {"api_key": "x"}, "chat": {}}),
    )
    assert job["status"] == "failed" and "unsupported launch settings" in job["output"]
    assert not (host.root / "etc/profiles/a.json").exists()


async def test_unknown_models_and_names(client):
    assert (await client.get("/api/models/zzz/profile")).status_code == 404
    assert (await client.get("/api/models/a%20b/profile")).status_code == 400
    r = await client.post("/api/models/zzz/profile", json={"launch": {}})
    assert r.status_code == 404


async def test_reasoning_effort_needs_a_passed_compatibility_check(client, settings):
    body = {"launch": {}, "chat": {"reasoning_effort": "high"}}
    r = await client.post("/api/models/a/profile", json=body)
    assert r.status_code == 409 and "verified values: none" in r.json()["detail"]
    _compat(settings, "a", {"reasoning_effort_low": "pass", "reasoning_effort_high": "fail"})
    r = await client.post("/api/models/a/profile", json=body)
    assert r.status_code == 409 and "verified values: low" in r.json()["detail"]
    view = (await client.get("/api/models/a/profile")).json()
    assert view["verified_reasoning_effort"] == ["low"]
    body["chat"]["reasoning_effort"] = "low"
    job = await _job(client, await client.post("/api/models/a/profile", json=body))
    assert job["status"] == "succeeded"


async def test_checks_run_with_other_settings_are_marked_stale(client, settings):
    _compat(settings, "a", {"plain": "pass"}, profile="default")
    assert (await client.get("/api/models/a/profile")).json()["checks_stale"] is False
    await _job(
        client,
        await client.post("/api/models/a/profile", json={"launch": {"prefill_step_size": 512}}),
    )
    view = (await client.get("/api/models/a/profile")).json()
    assert view["checks_stale"] is True and view["checks_fingerprint"] == "default"


async def test_reset_and_revert(client, host):
    await _job(
        client,
        await client.post("/api/models/a/profile", json={"launch": {"prefill_step_size": 512}}),
    )
    job = await _job(client, await client.post("/api/models/a/profile/reset"))
    assert job["status"] == "succeeded"
    assert (await client.get("/api/models/a/profile")).json()["profile"]["saved"] is None
    job = await _job(client, await client.post("/api/models/a/profile/revert"))
    assert job["status"] == "succeeded"
    view = (await client.get("/api/models/a/profile")).json()
    assert view["profile"]["launch"] == {"prefill_step_size": 512}


async def test_a_profile_change_waits_for_a_running_job(client, app):
    claim = app.state.control.runner.claim()  # e.g. a quality run holds the slot
    try:
        r = await client.post("/api/models/a/profile", json={"launch": {}})
        assert r.status_code == 409
    finally:
        app.state.control.runner.release(claim)


async def test_chat_uses_the_selected_models_saved_defaults(client, host, fake_llm):
    import respx

    await _job(
        client,
        await client.post(
            "/api/models/a/profile", json={"chat": {"temperature": 0.1, "max_tokens": 99}}
        ),
    )
    assert host.run("start", "a").returncode == 0
    healthy = {"status": "healthy", "loaded_model": "/Volumes/Models/models/active"}
    with respx.mock(assert_all_called=False) as router:
        router.route(host="mlx.test", path="/health").mock(
            return_value=httpx.Response(200, json=healthy)
        )
        router.route(host="comfy.test").mock(side_effect=httpx.ConnectError("down"))
        r = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 7}
        )
    assert r.status_code == 200, r.text
    sent = fake_llm.requests[-1]
    assert sent["temperature"] == 0.1 and sent["max_tokens"] == 7
    done = [json.loads(ln[5:]) for ln in r.text.splitlines() if ln.startswith("data:")][-1]
    assert done["settings"] == {"temperature": 0.1, "max_tokens": 7}


# --- units --------------------------------------------------------------------------


def test_argv_is_fixed(tmp_path):
    b = tmp_path / "mlx-model"
    assert build_profile_argv(b, "schema")[1:] == ["profile", "schema"]
    argv = build_profile_argv(b, "set", "a", {"launch": {"max_kv_size": 1}})
    assert argv[1:4] == ["profile", "set", "a"] and json.loads(argv[4])["launch"]
    for bad in (
        ("set", "../a", {}),
        ("rm", "a", None),
        ("show", None, None),
        ("schema", "a", None),
        ("reset", "a", {"x": 1}),
    ):
        with pytest.raises(ValueError):
            build_profile_argv(b, *bad)
    with pytest.raises(ValueError):
        build_profile_argv(b, "set", "a", {"x": float("nan")})


def test_verified_efforts():
    assert verified_efforts(None) == []
    compat = {
        "reasoning_effort_low": {"status": "pass"},
        "reasoning_effort_high": {"status": "fail"},
    }
    assert verified_efforts(compat) == ["low"]


def test_settings_hash_keeps_old_keys_for_the_default_profile():
    base = settings_hash({"t": 0}, "fp")
    assert settings_hash({"t": 0}, "fp", "default") == base
    assert settings_hash({"t": 0}, "fp", None) == base
    assert settings_hash({"t": 0}, "fp", "abc123") != base


def test_cli_errors_are_mapped(settings):
    import subprocess

    class P:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def svc(result):
        def cli(argv, t):
            if isinstance(result, Exception):
                raise result
            return result

        return ProfileService(settings, None, None, None, cli=cli)  # type: ignore[arg-type]

    for result, code in (
        (subprocess.TimeoutExpired("x", 1), 504),
        (OSError("x"), 503),
        (P(2, "", "invalid profile: x"), 400),
        (P(1, "", "boom"), 502),
        (P(0, "not json"), 502),
    ):
        with pytest.raises(ProfileError) as exc:
            svc(result).schema()
        assert exc.value.code == code
    assert svc(P(1, "", "boom")).fingerprints() == {}
    assert svc(P(1, "", "boom")).chat_defaults("a") == {}
    assert svc(P(0, "{}")).chat_defaults(None) == {}
