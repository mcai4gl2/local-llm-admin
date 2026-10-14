"""M3b API: the admin drives the real mlx-model and helper against a fake Hugging Face."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.alerts import Snapshot
from local_llm_admin.app import create_app
from local_llm_admin.config import Settings
from local_llm_admin.download_store import DownloadStore
from local_llm_admin.downloads import (
    DownloadError,
    DownloadService,
    build_argv,
    suggest_dest,
    validate_repo,
)

from .fake_hf import FakeHF, Repo, serve
from .host_harness import make_host
from .test_downloads import SHA, _files, install_helper


def _snap(mounted):
    return Snapshot(
        wall=time.time(),
        mono=time.monotonic(),
        drive_mounted=mounted,
        access_error=None,
        access_known=True,
        engine="none",
        mlx_serving=False,
        intent=None,
        job_running=False,
        lock=None,
        pressure=None,
        litellm_ok=None,
        suspended=None,
    )


@pytest.fixture
def hf():
    fake = FakeHF()
    fake.repos["mlx-community/Tiny"] = Repo(SHA, _files())
    fake.repos["someone/Other"] = Repo(SHA, _files(1))
    server = serve(fake)
    yield fake
    server.shutdown()


@pytest.fixture
def host(tmp_path, hf, monkeypatch):
    h = make_host(tmp_path)
    install_helper(h.root, hf.endpoint)
    for k in ("PATH", "FAKE_HOST_STATE"):
        monkeypatch.setenv(k, h.env[k])
    yield h
    h.cleanup()


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
async def app(settings):
    app = create_app(settings, start_background=False)
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


async def _until(client, run_id, states=("published", "failed", "cancelled")):
    for _ in range(300):
        row = (await client.get(f"/api/downloads/{run_id}")).json()
        if row["state"] in states:
            return row
        await asyncio.sleep(0.1)
    raise AssertionError(f"download stayed {row['state']}")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/downloads/preview"),
        ("POST", "/api/downloads"),
        ("GET", "/api/downloads/runs"),
        ("GET", "/api/downloads/r00000000001"),
        ("POST", "/api/downloads/r00000000001/cancel"),
        ("POST", "/api/downloads/r00000000001/resume"),
    ],
)
async def test_endpoints_require_login(app, method, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.request(method, path, json={})).status_code == 401


async def test_preview_start_publish_then_register(client, host):
    r = await client.post("/api/downloads/preview", json={"repo": "mlx-community/Tiny"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["revision"] == SHA and p["suggested_dest"] == "Tiny" and p["card"]
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": SHA, "dest": "Tiny"}
    )
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["log_source"] == f"download-{run['id']}"
    row = await _until(client, run["id"])
    assert row["state"] == "published" and row["bytes_done"] == row["bytes_total"]
    assert row["published_path"] == str(host.root / "models/Tiny")
    runs = (await client.get("/api/downloads/runs")).json()
    assert [x["id"] for x in runs["runs"]] == [run["id"]]
    assert runs["allowed_owners"] == "mlx-community"
    # a published directory shows up on disk, unregistered, until registered explicitly
    disk = (await client.get("/api/models")).json()["discovered"]
    tiny = next(d for d in disk if d["dir_name"] == "Tiny")
    assert tiny["registered_as"] is None and tiny["state"] == "complete"
    inv = (await client.get("/api/models/inventory?refresh=true")).json()
    (entry,) = [u for u in inv["unregistered"] if u["dir_name"] == "Tiny"]
    assert entry["downloaded_at"] == pytest.approx(row["finished"])
    logs = (await client.get(f"/api/logs/download-{run['id']}")).json()
    assert any("done fail=0" in ln for ln in logs["lines"])
    r = await client.post("/api/models/register", json={"dir_name": "Tiny", "name": "tiny"})
    assert r.status_code == 202, r.text


async def test_owner_allow_list_and_input_validation(client):
    r = await client.post("/api/downloads/preview", json={"repo": "someone/Other"})
    assert r.status_code == 403 and "DOWNLOAD_ALLOWED_OWNERS" in r.json()["detail"]
    for repo in ("https://huggingface.co/x/y", "../x", "a/b/c", "x"):
        r = await client.post("/api/downloads/preview", json={"repo": repo})
        assert r.status_code == 400, repo
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": "main", "dest": "T"}
    )
    assert r.status_code == 400
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": SHA, "dest": "../T"}
    )
    assert r.status_code == 400
    assert (await client.get("/api/downloads/NOTANID")).status_code == 400
    assert (await client.get("/api/downloads/r00000000009")).status_code == 404


async def test_gated_repo_gives_an_actionable_auth_error(client, hf):
    hf.repos["mlx-community/Gated"] = Repo(SHA, _files(1), gated=True)
    hf.token = "hf_SECRETTOKEN123456"
    r = await client.post("/api/downloads/preview", json={"repo": "mlx-community/Gated"})
    assert r.status_code == 403 and "token" in r.json()["detail"]


async def test_existing_destination_is_refused(client, host):
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": SHA, "dest": "A"}
    )
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    runs = (await client.get("/api/downloads/runs")).json()["runs"]
    assert runs[0]["state"] == "failed"


async def test_cancel_resume_and_one_at_a_time(client, hf):
    name = "model-00001-of-00002.safetensors"
    hf.slow[name] = 0.05
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": SHA, "dest": "Tiny"}
    )
    run_id = r.json()["id"]
    r = await client.post(
        "/api/downloads", json={"repo": "mlx-community/Tiny", "revision": SHA, "dest": "T2"}
    )
    assert r.status_code == 409 and "one at a time" in r.json()["detail"]
    for _ in range(200):
        if hf.ranges(name):
            break
        await asyncio.sleep(0.05)
    r = await client.post(f"/api/downloads/{run_id}/cancel")
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "cancelled" and r.json()["retryable"] is True
    del hf.slow[name]
    r = await client.post(f"/api/downloads/{run_id}/resume")
    assert r.status_code == 202, r.text
    row = await _until(client, run_id)
    assert row["state"] == "published"
    r = await client.post(f"/api/downloads/{run_id}/resume")
    assert r.status_code == 409


async def test_restarted_admin_reconnects_to_a_running_download(settings, hf, host):
    hf.slow["model-00001-of-00002.safetensors"] = 0.02
    store = DownloadStore(settings.admin_data_dir / "admin.sqlite")
    from local_llm_admin.diagnostics import ProbeRunner

    svc = DownloadService(settings, store, ProbeRunner(), new_id=lambda: "r00000000007")
    svc.start("mlx-community/Tiny", SHA, "Tiny")
    # a "new admin process": fresh service over the same database, driven by ticks
    svc2 = DownloadService(settings, DownloadStore(store.path), ProbeRunner())
    snap = _snap(True)
    for _ in range(300):
        await svc2.on_tick(snap, None)
        if store.get("r00000000007")["state"] == "published":
            break
        await asyncio.sleep(0.1)
    assert store.get("r00000000007")["state"] == "published"
    log = (host.root / "logs/download-r00000000007.log").read_text()
    assert log.count(" start =====") == 1  # followed, never relaunched


# --- units --------------------------------------------------------------------------


def test_build_argv_validates_every_argument(tmp_path):
    b = tmp_path / "mlx-model"
    assert build_argv(b, "download-status", "r00000000001")[1:] == [
        "download-status",
        "r00000000001",
    ]
    bad = [
        ("download-preview", "a/b", "../x"),
        ("download", "a/b", "main", "D", "r00000000001"),
        ("download", "a/b", SHA, "active", "r00000000001"),
        ("download-cancel", "r1"),
        ("rm", "-rf"),
        ("download-status",),
    ]
    for args in bad:
        with pytest.raises(ValueError):
            build_argv(b, *args)


def test_owner_allow_list():
    assert validate_repo("mlx-community/x", "mlx-community, froggeric") == "mlx-community/x"
    assert validate_repo("froggeric/x", "mlx-community, froggeric")
    assert validate_repo("anyone/x", "*")
    with pytest.raises(DownloadError) as exc:
        validate_repo("evil/x", "mlx-community")
    assert exc.value.code == 403


def test_suggest_dest():
    assert suggest_dest("mlx-community/Qwen3.8-27B-4bit") == "Qwen3.8-27B-4bit"
    assert suggest_dest("o/..weird..name") == "weird.name"


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _svc(settings, results):
    calls = []

    def cli(argv, timeout):
        calls.append(argv[1:])
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    store = DownloadStore(settings.admin_data_dir / "unit.sqlite")
    from local_llm_admin.diagnostics import ProbeRunner

    return DownloadService(
        settings, store, ProbeRunner(), cli=cli, new_id=lambda: "r00000000001"
    ), calls


def test_cli_failures_map_to_errors(settings):
    import subprocess

    svc, _ = _svc(settings, [subprocess.TimeoutExpired("x", 1)])
    with pytest.raises(DownloadError) as exc:
        svc.preview("mlx-community/x", "main")
    assert exc.value.code == 504
    svc, _ = _svc(settings, [OSError("nope")])
    with pytest.raises(DownloadError) as exc:
        svc.preview("mlx-community/x", "main")
    assert exc.value.code == 503
    svc, _ = _svc(settings, [_Proc(1, "garbage", "boom")])
    with pytest.raises(DownloadError) as exc:
        svc.preview("mlx-community/x", "main")
    assert exc.value.code == 502 and "boom" in exc.value.message
    with pytest.raises(DownloadError):
        svc.preview("mlx-community/x", "../x")


def test_unknown_host_run_is_marked_failed_and_errors_do_not_stop_refresh(settings):
    svc, _ = _svc(settings, [_Proc(0, json.dumps({"id": "r00000000001", "state": "running"}))])
    svc.start("mlx-community/x", SHA, "X")
    svc._cli = lambda argv, t: _Proc(1, json.dumps({"id": "r00000000001", "state": "unknown"}))
    assert svc.refresh("r00000000001")["state"] == "failed"
    svc.store.update("r00000000001", {"state": "running"})

    def broken(argv, t):
        raise OSError("gone")

    svc._cli = broken
    assert svc.refresh_active() == 1  # logged, not raised
    svc._cli = lambda argv, t: _Proc(1, "", "not running")
    with pytest.raises(DownloadError):
        svc.cancel("r00000000001")
    with pytest.raises(DownloadError):
        svc.cancel("BAD")
    with pytest.raises(DownloadError):
        svc.resume("BAD")


async def test_tick_skips_without_drive_or_active_runs(settings):
    svc, calls = _svc(settings, [])
    await svc.on_tick(_snap(False), None)
    await svc.on_tick(_snap(True), None)
    assert calls == []
    time.sleep(0)
