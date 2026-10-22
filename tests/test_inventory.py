"""M3a: model inventory, removal preview and the remove API (fake CLI)."""

from __future__ import annotations

import os
import time

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.app import create_app
from local_llm_admin.inventory import (
    InventoryStore,
    build_remove_argv,
    metadata_fingerprint,
    removal_preview,
)
from local_llm_admin.jobs import JobStore
from local_llm_admin.performance_store import PERF_SOURCE, PerformanceStore

# --- fingerprint --------------------------------------------------------------------


def test_fingerprint_follows_metadata_and_shard_sizes(tmp_path):
    d = tmp_path / "M"
    d.mkdir()
    (d / "config.json").write_text('{"a": 1}')
    (d / "model.safetensors").write_bytes(b"x" * 10)
    first = metadata_fingerprint(d)
    assert first is not None and first.startswith("meta:")
    assert metadata_fingerprint(d) == first
    (d / "model.safetensors").write_bytes(b"x" * 11)
    second = metadata_fingerprint(d)
    assert second != first
    (d / "config.json").write_text('{"a": 2}')
    assert metadata_fingerprint(d) not in (first, second)
    assert metadata_fingerprint(tmp_path / "missing") is None


# --- removal preview ----------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "Models"
    for m in ("A", "B"):
        (r / "models" / m).mkdir(parents=True)
        (r / "models" / m / "config.json").write_text("{}")
        (r / "models" / m / "w.safetensors").write_bytes(b"w" * 100)
    return r


def _reg(root, *pairs):
    return [{"name": n, "dir": str(root / "models" / d) if "/" not in d else d} for n, d in pairs]


def _preview(root, registered, name, selected=None, loaded=False):
    return removal_preview(
        registered,
        name,
        selected=selected,
        loaded=loaded,
        models_root=root / "models",
        dir_size=lambda p: 100,
    )


def test_preview_of_an_idle_model_allows_both(root):
    p = _preview(root, _reg(root, ("a", "A"), ("b", "B")), "b")
    canon = str((root / "models/B").resolve())
    assert p["canonical"] == canon and p["inode"] == os.stat(canon).st_ino
    assert p["size_bytes"] == 100 and p["aliases"] == []
    assert p["unregister_refusals"] == [] and p["delete_refusals"] == []


def test_unknown_name_is_none(root):
    assert _preview(root, _reg(root, ("a", "A")), "zzz") is None


def test_selected_and_loaded_models_are_refused(root):
    p = _preview(root, _reg(root, ("a", "A"), ("b", "B")), "b", selected="b", loaded=True)
    assert p["unregister_refusals"] and p["delete_refusals"]
    assert "loaded" in p["unregister_refusals"][0]


def test_active_link_target_is_refused(root):
    (root / "models/active").symlink_to(root / "models/B")
    p = _preview(root, _reg(root, ("a", "A"), ("b", "B")), "b", selected="a")
    assert any("active" in r for r in p["unregister_refusals"])


def test_duplicate_names_are_refused(root):
    p = _preview(root, _reg(root, ("b", "A"), ("b", "B")), "b")
    assert any("more than once" in r for r in p["unregister_refusals"])


def test_shared_directory_refuses_only_deletion(root):
    p = _preview(root, _reg(root, ("b", "B"), ("b2", "B")), "b")
    assert p["aliases"] == ["b2"]
    assert p["unregister_refusals"] == []
    assert any("b2" in r for r in p["delete_refusals"])


def test_symlink_outside_and_missing_directories_refuse_deletion(root, tmp_path):
    (root / "models/L").symlink_to(root / "models/A")
    outside = tmp_path / "X"
    outside.mkdir()
    reg = _reg(root, ("a", "A"), ("l", "L"), ("x", str(outside)), ("m", "Missing"))
    for name, word in (("l", "symlink"), ("x", "outside"), ("m", "missing")):
        p = _preview(root, reg, name)
        assert p["unregister_refusals"] == [], name
        assert any(word in r for r in p["delete_refusals"]), (name, p["delete_refusals"])
    assert _preview(root, reg, "m")["size_bytes"] is None


def test_remove_argv_is_fixed(tmp_path):
    argv = build_remove_argv(tmp_path / "mlx-model", "b", True, "/V/models/B", 42)
    assert argv[1:] == [
        "remove",
        "b",
        "--delete-files",
        "--expect-path",
        "/V/models/B",
        "--expect-inode",
        "42",
    ]
    assert build_remove_argv(tmp_path / "m", "b", False, None, None)[1:] == ["remove", "b"]
    with pytest.raises(ValueError):
        build_remove_argv(tmp_path / "m", "../b", False, None, None)
    with pytest.raises(ValueError):
        build_remove_argv(tmp_path / "m", "b", True, "relative", 1)


# --- first/last observed ------------------------------------------------------------


def test_first_observed_survives_later_scans(tmp_path):
    store = InventoryStore(tmp_path / "db.sqlite")
    seen = store.observe([("/m/A", "meta:1", 10)], now=100.0)
    assert seen["/m/A"]["first_seen"] == 100.0
    seen = store.observe([("/m/A", "meta:2", 12), ("/m/B", None, None)], now=200.0)
    assert seen["/m/A"] == {"first_seen": 100.0, "last_seen": 200.0}
    assert seen["/m/B"]["first_seen"] == 200.0


# --- API ----------------------------------------------------------------------------


@pytest.fixture
def settings(fake_mlx):
    s = fake_mlx.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "test-secret"
    llama = fake_mlx.root / "models/Llama"
    llama.mkdir()
    (llama / "config.json").write_text('{"model_type": "llama"}')
    (llama / "model.safetensors").write_bytes(b"y" * 50)
    fake_mlx.set_list(
        f"* qwen {fake_mlx.root}/models/Qwen\n  gemma {fake_mlx.root}/models/Gemma\n"
        f"  llama {llama}\n"
    )
    return s


@pytest.fixture
async def app(settings):
    app = create_app(settings, start_background=False)
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        await app.state.backfill_task
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        c.headers["x-csrf-token"] = (await c.get("/api/session")).json()["csrf"]
        yield c


async def _wait(client, job_id):
    for _ in range(200):
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] != "running":
            return job
        await _sleep()
    raise AssertionError("job did not finish")


async def _sleep():
    import asyncio

    await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/models/inventory"),
        ("GET", "/api/models/llama/remove-preview"),
        ("POST", "/api/models/remove"),
    ],
)
async def test_endpoints_require_login(app, method, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.request(method, path, json={})).status_code == 401


async def test_inventory_lists_models_disk_and_observed_use(client, settings, fake_mlx):
    db = settings.admin_data_dir / "admin.sqlite"
    now = time.time()
    blank = dict.fromkeys(
        (
            "finished stream images cached_tokens prefill_s prefill_tok_s ttft_s decode_s"
            " decode_tok_s elapsed_s finish_reason error log_identity log_start log_end"
        ).split()
    )
    rows = [
        {
            **blank,
            "key": f"k{i}",
            "request_id": f"r{i}",
            "started": now - 100 + i,
            "model": "llama",
            "attribution": "generation",
            "prompt_tokens": 10,
            "generated_tokens": 5,
            "state": "completed",
            "complete": 1,
        }
        for i in range(3)
    ]
    PerformanceStore(db).insert_rows(PERF_SOURCE, rows)
    JobStore(db).add_history(
        ts=now - 50,
        action="switch",
        from_name="qwen",
        to_name="llama",
        result="succeeded",
        duration_s=1.0,
        output_tail="",
        source="admin",
    )
    r = await client.get("/api/models/inventory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["disk"]["free_bytes"] > 0
    by = {m["name"]: m for m in body["models"]}
    assert by["qwen"]["selected"] is True
    assert by["gemma"]["exists"] is False and by["gemma"]["size_bytes"] is None
    llama = by["llama"]
    assert llama["size_bytes"] >= 50 and llama["fingerprint"].startswith("meta:")
    assert llama["weights"]["state"] == "complete"
    assert llama["first_observed"] is not None and llama["downloaded_at"] is None
    assert llama["last_activated"] == pytest.approx(now - 50)
    assert llama["requests"]["count"] == 3
    assert llama["requests"]["prompt_tokens"] == 30
    assert llama["requests"]["generated_tokens"] == 15
    assert "route" in body["token_attribution"]


async def test_inventory_times_out_instead_of_hanging(client, app, monkeypatch):
    import local_llm_admin.app as app_mod

    def slow(*a, **kw):
        time.sleep(1.0)
        return {}

    monkeypatch.setattr(app_mod, "build_inventory", slow)
    app.state.inventory_timeout_s = 0.1
    app.state.inventory_cache = None
    body = (await client.get("/api/models/inventory")).json()
    assert body["available"] is False and "reason" in body


async def test_remove_preview(client, fake_mlx):
    r = await client.get("/api/models/llama/remove-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["canonical"] == str((fake_mlx.root / "models/Llama").resolve())
    assert body["delete_refusals"] == []
    assert (await client.get("/api/models/zzz/remove-preview")).status_code == 404
    assert (await client.get("/api/models/..%2Fx/remove-preview")).status_code in (400, 404)
    qwen = (await client.get("/api/models/qwen/remove-preview")).json()
    assert qwen["unregister_refusals"]


async def test_remove_requires_csrf(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        await c.post("/login", data={"password": "pw"})
        r = await c.post("/api/models/remove", json={"name": "llama"})
        assert r.status_code == 403


async def test_delete_needs_the_typed_name(client, fake_mlx):
    r = await client.post(
        "/api/models/remove", json={"name": "llama", "delete_files": True, "confirmation": "x"}
    )
    assert r.status_code == 400
    assert not any(c.startswith("remove") for c in fake_mlx.calls())


async def test_refused_removals_never_reach_the_cli(client, fake_mlx):
    r = await client.post("/api/models/remove", json={"name": "qwen"})
    assert r.status_code == 409 and "selected" in r.json()["detail"]
    r = await client.post(
        "/api/models/remove",
        json={"name": "gemma", "delete_files": True, "confirmation": "gemma"},
    )
    assert r.status_code == 409 and "missing" in r.json()["detail"]
    assert (await client.post("/api/models/remove", json={"name": "zzz"})).status_code == 404
    assert (await client.post("/api/models/remove", json={"name": "a b"})).status_code == 400
    assert not any(c.startswith("remove") for c in fake_mlx.calls())


async def test_delete_runs_the_cli_with_the_previewed_identity(client, fake_mlx):
    canon = (fake_mlx.root / "models/Llama").resolve()
    r = await client.post(
        "/api/models/remove",
        json={"name": "llama", "delete_files": True, "confirmation": "llama"},
    )
    assert r.status_code == 202, r.text
    job = await _wait(client, r.json()["id"])
    assert job["status"] == "succeeded" and job["action"] == "remove"
    call = [c for c in fake_mlx.calls() if c.startswith("remove")][-1]
    assert call == (
        f"remove llama --delete-files --expect-path {canon} --expect-inode {os.stat(canon).st_ino}"
    )


async def test_unregister_runs_the_cli(client, fake_mlx):
    r = await client.post("/api/models/remove", json={"name": "gemma"})
    assert r.status_code == 202, r.text
    job = await _wait(client, r.json()["id"])
    assert job["status"] == "succeeded"
    assert [c for c in fake_mlx.calls() if c.startswith("remove")] == ["remove gemma"]
    names = {m["name"] for m in (await client.get("/api/models")).json()["models"]}
    assert "gemma" not in names


def test_an_interrupted_removal_does_not_suspend_recovery(settings):
    store = JobStore(settings.admin_data_dir / "admin.sqlite")
    store.save_job(
        {
            "id": "j1",
            "action": "remove",
            "name": "llama",
            "from_name": None,
            "to_name": None,
            "source": "admin",
            "status": "running",
            "returncode": None,
            "started": time.time(),
            "finished": None,
            "rolled_back": None,
            "observed_engine": None,
            "output": "",
        }
    )
    app = create_app(settings, start_background=False)
    app.state.control.reconcile()
    assert app.state.control.recovery_blocked() is None
    assert store.get_job("j1")["status"] == "interrupted"


def test_inventory_lists_unregistered_dirs_and_survives_disk_errors(
    settings, fake_mlx, monkeypatch
):
    import shutil as _shutil

    from local_llm_admin.inventory import build_inventory
    from local_llm_admin.mlx import MlxClient

    extra = fake_mlx.root / "models/New"
    extra.mkdir()
    (extra / "config.json").write_text("{}")
    (extra / "model.safetensors").write_bytes(b"z" * 7)

    def broken(_p):
        raise OSError("gone")

    monkeypatch.setattr(_shutil, "disk_usage", broken)
    store = InventoryStore(settings.admin_data_dir / "inv.sqlite")
    inv = build_inventory(
        MlxClient(settings), store, downloaded=lambda: {str(extra.resolve()): 5.0}, now=10.0
    )
    assert inv["disk"] is None
    (new,) = [d for d in inv["unregistered"] if d["dir_name"] == "New"]
    assert new["size_bytes"] == 7 and new["downloaded_at"] == 5.0 and new["first_observed"] == 10.0
    assert inv["requests_since"] is None


def test_fingerprint_skips_unreadable_entries(tmp_path, monkeypatch):
    d = tmp_path / "M"
    d.mkdir()
    (d / "config.json").mkdir()  # a directory where a file is expected: skipped
    os.symlink(tmp_path / "nowhere", d / "dangling")
    assert metadata_fingerprint(d) is not None


async def test_inventory_error_and_cache(client, app, monkeypatch):
    import local_llm_admin.app as app_mod

    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise RuntimeError("x")

    monkeypatch.setattr(app_mod, "build_inventory", boom)
    app.state.inventory_cache = None
    body = (await client.get("/api/models/inventory")).json()
    assert body == {"available": False, "reason": "the scan failed"}
    app.state.inventory_cache = (time.monotonic(), {"available": True, "cached": 1})
    assert (await client.get("/api/models/inventory")).json()["cached"] == 1
    assert len(calls) == 1


async def test_inventory_rows_carry_promoted_eval_means(client, settings, app):
    from local_llm_admin.quality_runner import model_info

    llama_fp = model_info(str(settings.mlx_root / "models/Llama"))["fingerprint"]
    with app.state.evals._conn() as c:
        c.execute(
            "INSERT INTO eval_scores (fingerprint, suite_id, suite_hash, settings_hash, model,"
            " run_id, n, excluded, mean, lo, hi, by_category, judge, self_judged, ts)"
            " VALUES (?, 'core-v1', 'h', 's', 'llama', 'r1', 10, 0, 0.62, 0.5, 0.7, '{}',"
            " NULL, 0, 1.0)",
            (llama_fp,),
        )
    body = (await client.get("/api/models/inventory?refresh=true")).json()
    by = {m["name"]: m for m in body["models"]}
    assert by["llama"]["eval"] == {"core-v1": 0.62}
    assert by["qwen"]["eval"] == {} and by["gemma"]["eval"] == {}


async def test_cache_hit_does_no_drive_io_but_shows_new_scores(client, settings, app, monkeypatch):
    import local_llm_admin.app as app_mod
    from local_llm_admin.quality_runner import model_info

    await client.get("/api/models/inventory?refresh=true")

    def boom(*a, **kw):
        raise AssertionError("model_info called on a cache hit")

    monkeypatch.setattr(app_mod, "model_info", boom)
    fp = model_info(str(settings.mlx_root / "models/Llama"))["fingerprint"]
    with app.state.evals._conn() as c:
        c.execute(
            "INSERT INTO eval_scores (fingerprint, suite_id, suite_hash, settings_hash, model,"
            " run_id, n, excluded, mean, lo, hi, by_category, judge, self_judged, ts)"
            " VALUES (?, 'core-v1', 'h', 's', 'llama', 'r1', 10, 0, 0.4, 0.3, 0.5, '{}',"
            " NULL, 0, 1.0), ('', 'core-v1', 'h', 's2', 'x', 'r2', 1, 0, 0.9, 0.9, 0.9, '{}',"
            " NULL, 0, 2.0)",
            (fp,),
        )
    by = {m["name"]: m for m in (await client.get("/api/models/inventory")).json()["models"]}
    assert by["llama"]["eval"] == {"core-v1": 0.4}
    # missing directory (null fingerprint) never matches, even a row keyed ""
    assert by["gemma"]["exists"] is False and by["gemma"]["eval"] == {}


async def test_inventory_eval_shows_thinking_off_scores_only(client, settings, app):
    from local_llm_admin.quality_runner import model_info

    fp = model_info(str(settings.mlx_root / "models/Llama"))["fingerprint"]
    with app.state.evals._conn() as c:
        c.execute(
            "INSERT INTO eval_scores (fingerprint, suite_id, suite_hash, settings_hash, model,"
            " run_id, n, excluded, mean, lo, hi, by_category, judge, self_judged, ts, thinking)"
            " VALUES (?, 'core-v1', 'h', 's', 'llama', 'r1', 10, 0, 0.62, 0.5, 0.7, '{}',"
            " NULL, 0, 1.0, 0), (?, 'core-v1', 'h', 's2', 'llama', 'r2', 10, 0, 0.9, 0.8, 1.0,"
            " '{}', NULL, 0, 2.0, 1)",
            (fp, fp),
        )
    by = {m["name"]: m for m in (await client.get("/api/models/inventory?refresh=true")).json()["models"]}
    assert by["llama"]["eval"] == {"core-v1": 0.62}
