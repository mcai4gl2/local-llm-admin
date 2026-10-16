"""M2 §3: bounded quality runs that restore the original engine and model."""

import asyncio
import json
import os
import subprocess

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.alerts import AlertStore
from local_llm_admin.control import ControlError, ControlService
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient
from local_llm_admin.quality import BENCHMARK_CASES
from local_llm_admin.quality_runner import QualityError, QualityRunner
from local_llm_admin.quality_store import QualityStore

from .fake_mlx_server import FakeMlxServer

P128 = (BENCHMARK_CASES[0],)


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


class Sampler:
    def current(self):
        return {"pressure": "normal", "swap_used": 0}


@pytest.fixture
def server():
    return FakeMlxServer()


@pytest.fixture
def env(fake_comfy, fake_mlx, server):
    s = fake_comfy.settings
    s.quality_request_timeout_s = 5
    s.quality_ready_timeout_s = 1
    s.quality_rss_interval_s = 0.01
    (fake_mlx.state / "track_env").write_text("")
    gemma = fake_mlx.root / "models/Gemma"
    gemma.mkdir()
    (gemma / "config.json").write_text(json.dumps({"model_type": "gemma4"}))
    (fake_mlx.root / "models/Qwen/config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "vision_config": {}})
    )
    mlx = MlxClient(s)
    path = s.admin_data_dir / "admin.sqlite"
    jobs = JobStore(path)
    runner = JobRunner(s, jobs, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    control = ControlService(s, jobs, runner, mlx, ComfyClient(s, transport=fake_comfy.transport))
    store = QualityStore(path)
    alerts = AlertStore(path)
    qr = QualityRunner(s, control, store, alerts, Sampler(), transport=server.transport, cases=P128)
    qr.fake = fake_mlx
    return qr


def _cli(qr, *args):
    subprocess.run([str(qr.settings.mlx_model_path), *args], capture_output=True, check=False)


def _calls(qr):
    return [c for c in qr.fake.calls() if c.split()[0] in ("start", "switch", "stop", "select")]


def _active(qr):
    return qr.control.mlx.active_env().get("MLX_ACTIVE_NAME")


@pytest.fixture
def running(env):
    _cli(env, "start", "qwen")
    env.control.store.set_kv("intent", {"value": "mlx:qwen"})
    return env


async def _finish(qr, run):
    await qr.wait()
    return qr.store.get_run(run["id"], measurements=True)


# --- a normal run ---------------------------------------------------------------------


async def test_benchmark_current_model_without_switching(running, server):
    qr = running
    run = await qr.start("benchmark", "current")
    assert run["status"] == "running" and qr.active()["run_id"] == run["id"]
    done = await _finish(qr, run)
    assert done["status"] == "succeeded" and done["runtime_version"] == "0.7.3"
    assert done["runtime_fingerprint"] == server.settings["fingerprint"]
    assert done["restoration"]["outcome"] == "not_needed" and done["controlled"] is True
    (case,) = done["cases"]
    assert case["model"] == "qwen" and case["case_key"] == "p128" and case["status"] == "complete"
    assert case["cache_state"] == "verified_cold" and case["fingerprint"]
    assert len(case["measurements"]) == 4 and case["measurements"][0]["warmup"] is True
    data = case["measurements"][1]["data"]
    assert data["completion_tokens"] == 8 and data["backend_decode_tok_s"] == 40.0
    assert data["peak_rss_bytes"] and data["system"]["pressure_after"] == "normal"
    assert case["summary"]["medians"]["backend_decode_tok_s"] == 40.0
    assert _calls(qr) == ["start qwen"]  # only the fixture's start
    job = qr.control.runner.get(done["job_id"])
    assert job["status"] == "succeeded" and job["source"] == "benchmark"
    assert qr.control.intent()["value"] == "mlx:qwen" and qr.control.recovery_blocked() is None
    assert qr.active() is None and qr.control.runner.running() is None
    assert all(r["model"].endswith("models/active") for r in server.requests)


async def test_all_models_switch_sequentially_and_restore(running):
    qr = running
    done = await _finish(qr, await qr.start("benchmark", "all"))
    assert done["status"] == "succeeded"
    assert [c["model"] for c in done["cases"]] == ["qwen", "gemma"]
    assert _calls(qr) == ["start qwen", "switch gemma", "switch qwen"]
    assert done["restoration"]["outcome"] == "restored" and _active(qr) == "qwen"
    gemma = done["cases"][1]
    assert gemma["load_s"] is not None and gemma["fingerprint"] != done["cases"][0]["fingerprint"]
    hist = [(h["action"], h["to_name"], h["source"]) for h in qr.control.store.history()]
    assert ("switch", "gemma", "benchmark") in hist and ("switch", "qwen", "benchmark") in hist


async def test_single_named_model(running):
    qr = running
    done = await _finish(qr, await qr.start("benchmark", "model", "gemma"))
    assert [c["model"] for c in done["cases"]] == ["gemma"]
    assert _calls(qr)[-2:] == ["switch gemma", "switch qwen"]


async def test_off_requires_confirmation_then_returns_to_off_and_prior_selection(env):
    qr = env
    with pytest.raises(QualityError) as err:
        await qr.start("benchmark", "all")
    assert err.value.code == 409 and "confirm" in err.value.message
    assert qr.control.runner.running() is None
    run = await qr.start("benchmark", "all", confirm_off=True)
    assert "returns it to off" in run["warning"]
    done = await _finish(qr, run)
    assert done["status"] == "succeeded" and done["original"]["engine"] == "none"
    assert _calls(qr) == ["start qwen", "switch gemma", "stop", "select qwen"]
    assert qr.control.observe()["engine"] == "none" and _active(qr) == "qwen"
    assert done["restoration"]["outcome"] == "restored"


async def test_compatibility_run_records_outcomes(running, server):
    qr = running
    done = await _finish(qr, await qr.start("compatibility", "current"))
    cases = {c["case_key"]: c for c in done["cases"]}
    assert set(cases) >= {"plain", "stream", "tools", "json", "vision"}
    assert all(c["kind"] == "compatibility" for c in cases.values())
    assert cases["plain"]["status"] == "pass"
    assert cases["tools"]["status"] == "fail"  # the fake answers without a tool call
    assert cases["vision"]["status"] == "fail"  # evidence says vision; the fake says "ready"
    latest = qr.store.latest_compatibility()
    assert latest["qwen"]["plain"]["status"] == "pass"


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("bogus", "current"), 400),
        (("benchmark", "bogus"), 400),
        (("benchmark", "model"), 400),
        (("benchmark", "current", "qwen"), 400),
        (("benchmark", "model", "x;y"), 400),
        (("benchmark", "model", "nope"), 404),
    ],
)
async def test_invalid_requests_are_refused_and_release_the_slot(running, args, code):
    with pytest.raises(QualityError) as err:
        await running.start(*args)
    assert err.value.code == code
    assert running.control.runner.running() is None
    running.control.runner.release(running.control.runner.claim())


async def test_comfyui_running_is_refused(env, fake_comfy):
    subprocess.run([str(env.settings.comfyui_path), "start"], capture_output=True, check=False)
    with pytest.raises(QualityError) as err:
        await env.start("benchmark", "current", confirm_off=True)
    assert err.value.code == 409 and "comfyui" in err.value.message.lower()


async def test_decision_sidecar_running_is_refused(running, fake_decision):
    """M6: benchmark numbers stay comparable; the sidecar shares the GPU."""
    from local_llm_admin.decision import DecisionClient

    running.control.decision = DecisionClient(fake_decision.settings)
    fake_decision.start()
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 409 and "decision sidecar" in err.value.message
    assert running.control.runner.running() is None


async def test_unknown_original_selection_is_refused(env, fake_mlx):
    (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=deleted\n")
    with pytest.raises(QualityError) as err:
        await env.start("benchmark", "all", confirm_off=True)
    assert err.value.code == 409 and "selected model" in err.value.message


async def test_stale_lock_is_refused(running, fake_mlx):
    (fake_mlx.root / "run/mlx.lock").mkdir()
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 409


async def test_busy_slot_is_refused(running):
    claim = running.control.runner.claim()
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 409
    running.control.runner.release(claim)


async def test_a_second_run_and_manual_lifecycle_are_busy(running, server):
    server.stream_delay = 0.05
    run = await running.start("benchmark", "current")
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 409 and "quality run" in err.value.message
    with pytest.raises(ControlError) as cerr:
        await running.control.lifecycle("stop", None)
    assert cerr.value.code == 409 and "quality run" in cerr.value.message
    await running.cancel(run["id"])
    await running.wait()


# --- failures, cancellation and restoration -------------------------------------------


async def test_switch_failure_is_recorded_and_the_sweep_continues(running, fake_mlx):
    qr = running

    def fail_gemma():
        (fake_mlx.state / "exit").write_text("1")

    # the first switch (to gemma) fails; the CLI then succeeds for the restore
    fail_gemma()
    orig = qr._run_cli

    async def once(*a, **kw):
        result = await orig(*a, **kw)
        (fake_mlx.state / "exit").unlink(missing_ok=True)
        return result

    qr._run_cli = once
    done = await _finish(qr, await qr.start("benchmark", "all"))
    assert done["status"] == "failed" and "gemma" in done["error"]
    load = [c for c in done["cases"] if c["case_key"] == "load"]
    assert load and load[0]["status"] == "failed" and load[0]["model"] == "gemma"
    assert done["restoration"]["outcome"] == "restored"


async def test_request_timeout_marks_repeats_failed_but_run_finishes(running, server):
    running.settings.quality_request_timeout_s = 0.05
    server.stream_parts = ["x"] * 20
    server.stream_delay = 0.02
    done = await _finish(running, await running.start("benchmark", "current"))
    (case,) = done["cases"]
    assert case["status"] == "incomplete"
    assert all(m["error"] for m in case["measurements"])
    assert done["status"] == "succeeded"


async def test_run_deadline_stops_and_restores(running):
    running.settings.quality_run_deadline_s = 0.0
    done = await _finish(running, await running.start("benchmark", "all"))
    assert done["status"] == "failed" and "deadline" in done["error"]
    assert done["restoration"]["outcome"] in ("not_needed", "restored")
    assert running.control.runner.running() is None


async def test_cancel_during_a_stream_closes_it_and_restores(running, server):
    qr = running
    server.stream_parts = ["x"] * 200
    server.stream_delay = 0.02
    run = await qr.start("benchmark", "all")
    for _ in range(200):
        if server.requests:
            break
        await asyncio.sleep(0.01)
    out = await qr.cancel(run["id"])
    assert out["id"] == run["id"]
    done = await _finish(qr, run)
    assert done["status"] == "cancelled" and server.closed_streams >= 1
    assert done["restoration"]["outcome"] == "not_needed"
    assert done["cases"][0]["status"] == "cancelled"
    assert qr.control.runner.running() is None
    assert qr.control.runner.get(done["job_id"])["status"] == "cancelled"


async def test_cancel_during_a_switch_waits_then_restores(running, fake_mlx):
    qr = running
    fake_mlx.set_delay(0.3)
    run = await qr.start("benchmark", "model", "gemma")
    await asyncio.sleep(0.1)
    await qr.cancel(run["id"])
    done = await _finish(qr, run)
    assert done["status"] == "cancelled"
    assert _calls(qr) == ["start qwen", "switch gemma", "switch qwen"]
    assert done["restoration"]["outcome"] == "restored" and _active(qr) == "qwen"


async def test_cancel_unknown_or_finished_run(running):
    with pytest.raises(QualityError) as err:
        await running.cancel("nope")
    assert err.value.code == 404
    run = await running.start("benchmark", "current")
    await running.wait()
    with pytest.raises(QualityError) as err:
        await running.cancel(run["id"])
    assert err.value.code == 409


async def test_restore_failure_alerts_and_suspends(running, fake_mlx):
    qr = running
    orig = qr._run_cli

    async def fail_restore(act, name, label=None):
        if name == "qwen":
            (fake_mlx.state / "exit").write_text("1")
        try:
            return await orig(act, name, label)
        finally:
            (fake_mlx.state / "exit").unlink(missing_ok=True)

    qr._run_cli = fail_restore
    done = await _finish(qr, await qr.start("benchmark", "model", "gemma"))
    assert done["restoration"]["outcome"] == "failed"
    assert done["restoration"]["observed"]["engine"] == "mlx"
    assert qr.control.intent()["value"] == "unknown"
    assert "quality run" in qr.control.recovery_blocked()
    keys = [a["key"] for a in qr.alerts.list("all", 50)]
    assert "quality_restore_failed" in keys


async def test_external_engine_change_aborts_without_overwriting(running, server, fake_mlx):
    qr = running

    def operator_switches(payload):
        (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=gemma\n")

    server.on_chat = operator_switches
    done = await _finish(qr, await qr.start("benchmark", "current"))
    assert done["status"] == "intervention_required"
    assert done["restoration"]["outcome"] == "not_attempted"
    assert _calls(qr) == ["start qwen"]  # no restore over the operator's change
    assert _active(qr) == "gemma"
    assert "quality run" in qr.control.recovery_blocked()
    assert "quality_intervention" in [a["key"] for a in qr.alerts.list("all", 50)]


async def test_external_traffic_marks_repeats_contaminated(running, server):
    server.external_per_request = 1
    done = await _finish(running, await running.start("benchmark", "current"))
    (case,) = done["cases"]
    assert all(m["contaminated"] for m in case["measurements"])
    assert case["status"] == "incomplete" and done["controlled"] is True


async def test_unreadable_metrics_make_the_run_uncontrolled(running, server):
    server.metrics_ok = False
    done = await _finish(running, await running.start("benchmark", "current"))
    (case,) = done["cases"]
    assert done["controlled"] is False and case["summary"]["controlled"] is False
    assert case["summary"]["regressions"] == []
    assert (
        running.store.baseline(
            case["fingerprint"], "p128", case["settings_hash"], case["cache_state"]
        )
        is None
    )


async def test_server_death_mid_case_fails_the_case(running, server):
    def die(payload):
        server.unreachable = True

    server.on_chat = die
    done = await _finish(running, await running.start("benchmark", "current"))
    (case,) = done["cases"]
    assert case["status"] == "failed" and done["status"] == "failed"


async def test_admin_shutdown_interrupts_without_silent_change(running, server):
    qr = running
    server.stream_parts = ["x"] * 200
    server.stream_delay = 0.02
    run = await qr.start("benchmark", "current")
    await asyncio.sleep(0.1)
    await qr.shutdown()
    done = qr.store.get_run(run["id"])
    assert done["status"] == "interrupted"
    assert done["restoration"]["outcome"] == "not_attempted"
    assert qr.control.runner.running() is None
    assert qr.control.intent()["value"] == "unknown"
    assert qr.control.recovery_blocked()


def test_reconcile_marks_leftover_runs_interrupted(env):
    env.store.create_run({"id": "old", "kind": "benchmark", "scope": "all", "started": 1.0})
    assert [r["id"] for r in env.reconcile()] == ["old"]
    assert env.store.get_run("old")["status"] == "interrupted"


async def test_rss_reader_handles_a_missing_process(env, fake_mlx):
    assert env._rss() is None
    pidfile = fake_mlx.root / "run/mlx-server.pid"
    pidfile.write_text(str(os.getpid()))
    assert env._rss() > 0
    pidfile.write_text("999999999")
    assert env._rss() is None


# --- edge and failure paths (critical: 100% branch coverage) ---------------------------

from local_llm_admin import quality_runner as qr_mod  # noqa: E402
from local_llm_admin.jobs import JobStorageError  # noqa: E402
from local_llm_admin.quality_runner import RunPlan, model_info  # noqa: E402


def test_model_info(tmp_path):
    assert model_info(None) == {"fingerprint": None, "vision": False}
    assert model_info(str(tmp_path / "missing")) == {"fingerprint": None, "vision": False}
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text("{not json")
    assert model_info(str(d))["vision"] is False and model_info(str(d))["fingerprint"]
    (d / "config.json").write_text('{"vision_config": {}}')
    (d / "model.safetensors").write_bytes(b"x" * 10)
    a = model_info(str(d))
    (d / "model.safetensors").write_bytes(b"x" * 11)
    b = model_info(str(d))
    assert a["vision"] is True and a["fingerprint"] != b["fingerprint"]


def test_model_info_unlistable_directory(tmp_path, monkeypatch):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text("{}")

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(qr_mod.Path, "iterdir", boom)
    assert model_info(str(d)) == {"fingerprint": None, "vision": False}


async def test_job_storage_failure_at_start_is_503(running, monkeypatch):
    def fail(*a, **kw):
        raise JobStorageError("disk")

    monkeypatch.setattr(running.control.runner, "begin", fail)
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 503 and running.control.runner.running() is None


async def test_run_record_failure_at_start_releases_the_job(running, monkeypatch):
    def fail(run):
        raise OSError("disk")

    monkeypatch.setattr(running.store, "create_run", fail)
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 503 and running.control.runner.running() is None
    assert running.control.intent()["value"] == "mlx:qwen"


async def test_model_that_never_becomes_healthy_fails_to_load(running, server):
    qr = running
    qr.settings.quality_ready_timeout_s = 0.1
    orig = qr._run_cli

    async def switch(act, name, label=None):
        server.health = None if name == "gemma" else dict(qr_mod_health)
        return await orig(act, name, label)

    qr_mod_health = server.health
    qr._run_cli = switch
    done = await _finish(qr, await qr.start("benchmark", "model", "gemma"))
    assert "did not become healthy" in done["error"]
    assert done["restoration"]["outcome"] == "restored"


async def test_wrong_model_after_switch_fails_to_load(running, fake_mlx):
    qr = running
    orig = qr._run_cli

    async def switch(act, name, label=None):
        status = await orig(act, name, label)
        if name == "gemma":
            (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=other\n")
        return status

    qr._run_cli = switch
    done = await _finish(qr, await qr.start("benchmark", "model", "gemma"))
    assert "not the serving model" in done["error"]


async def test_compatibility_load_failure_is_unknown_not_fail(running, fake_mlx):
    qr = running
    orig = qr._run_cli

    async def switch(act, name, label=None):
        if name == "gemma":
            (fake_mlx.state / "exit").write_text("1")
        try:
            return await orig(act, name, label)
        finally:
            (fake_mlx.state / "exit").unlink(missing_ok=True)

    qr._run_cli = switch
    done = await _finish(qr, await qr.start("compatibility", "model", "gemma"))
    (load,) = [c for c in done["cases"] if c["case_key"] == "load"]
    assert load["status"] == "unknown" and load["kind"] == "compatibility"


async def test_deadline_during_a_case_fails_the_case(running, server):
    running.settings.quality_run_deadline_s = 0.3
    server.stream_parts = ["x"] * 100
    server.stream_delay = 0.02
    done = await _finish(running, await running.start("benchmark", "current"))
    assert done["status"] == "failed" and "deadline" in done["error"]
    assert done["cases"][0]["status"] == "failed"


async def test_internal_error_still_restores_and_releases(running, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(running.store, "add_case", boom)
    done = await _finish(running, await running.start("benchmark", "model", "gemma"))
    assert done["status"] == "failed" and "internal error" in done["error"]
    assert done["restoration"]["outcome"] == "restored"
    assert running.control.runner.running() is None


async def test_recording_failure_at_the_end_still_releases(running, monkeypatch):
    real = running.store.update_run

    def update(run_id, **fields):
        if "status" in fields:
            raise OSError("disk")
        return real(run_id, **fields)

    monkeypatch.setattr(running.store, "update_run", update)
    run = await running.start("benchmark", "current")
    await running.wait()
    assert running.control.runner.running() is None
    assert running.control.runner.get(run["job_id"])["status"] == "succeeded"


async def test_missing_runtime_version_is_null(running, server):
    server.openapi_ok = False
    done = await _finish(running, await running.start("benchmark", "current"))
    assert done["runtime_version"] is None and done["status"] == "succeeded"


async def test_quiesce_waits_for_in_flight_then_gives_up(env, server, monkeypatch):
    monkeypatch.setattr(qr_mod, "_QUIESCE_S", 0.3)
    server.in_flight = 3
    import httpx

    async with httpx.AsyncClient(base_url="http://mlx.test", transport=server.transport) as http:
        t0 = asyncio.get_running_loop().time()
        await env._quiesce(http)
        assert asyncio.get_running_loop().time() - t0 >= 0.25


async def test_not_restored_tolerates_an_observation_failure(env, monkeypatch):
    def boom():
        raise OSError("drive gone")

    monkeypatch.setattr(env.control, "observe", boom)
    out = await env._not_restored("why")
    assert out == {"outcome": "not_attempted", "detail": "why", "observed": None}


def _plan(qr, engine, name="qwen"):
    plan = RunPlan(
        run_id="r",
        job_id="",
        kind="benchmark",
        targets=[],
        dirs={},
        original={"engine": engine, "name": name},
        deadline=0,
    )
    job = qr.control.runner.begin(
        "quality",
        name=None,
        from_name=None,
        to_name=None,
        source="benchmark",
        claim=qr.control.runner.claim(),
    )
    plan.job_id = job["id"]
    qr._plan = plan
    return plan


async def _restore(qr, plan, observed=None):
    import httpx

    if observed is not None:
        seq = list(observed)
        real = qr.control.observe

        def observe():
            return seq.pop(0) if seq else real()

        qr.control.observe = observe
    async with httpx.AsyncClient(base_url="http://mlx.test", transport=qr.transport) as http:
        out = await qr._restore(http, plan)
    qr.control.runner.finish(plan.job_id, "succeeded", rolled_back=True)
    return out


def _obs(engine, name=None):
    return {"engine": engine, "mlx_name": name, "generation": None}


async def test_restore_to_mlx_refuses_another_engine(env):
    out = await _restore(env, _plan(env, "mlx"), [_obs("comfyui")])
    assert out["outcome"] == "failed" and "comfyui" in out["detail"]


async def test_restore_to_mlx_from_off_starts_it(env):
    out = await _restore(env, _plan(env, "mlx"))
    assert out["outcome"] == "restored" and _calls(env) == ["start qwen"]


async def test_restore_to_off_refuses_another_engine(env):
    out = await _restore(env, _plan(env, "none"), [_obs("conflict")])
    assert out["outcome"] == "failed" and "conflict" in out["detail"]


async def test_restore_to_off_reports_a_failed_stop(running, fake_mlx):
    fake_mlx.set_exit(1)
    out = await _restore(running, _plan(running, "none"))
    assert out["outcome"] == "failed" and "stop" in out["detail"]


async def test_restore_to_off_reports_a_failed_select(env, fake_mlx):
    (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=gemma\n")
    (fake_mlx.state / "select_fails").write_text("")
    out = await _restore(env, _plan(env, "none"))
    assert out["outcome"] == "failed" and "select" in out["detail"]


async def test_restore_checks_the_final_state(env, fake_mlx):
    # the stop "succeeds" but the process is still there
    out = await _restore(env, _plan(env, "none"), [_obs("none"), _obs("mlx", "qwen")])
    assert out["outcome"] == "failed" and "after restoring" in out["detail"]


async def test_restore_error_is_a_failure(env, monkeypatch):
    plan = _plan(env, "mlx")

    def boom():
        raise OSError("drive gone")

    monkeypatch.setattr(env.control, "observe", boom)
    import httpx

    async with httpx.AsyncClient(base_url="http://mlx.test", transport=env.transport) as http:
        out = await env._restore(http, plan)
    assert out == {"outcome": "failed", "detail": "restore error: drive gone", "observed": None}
    env.control.runner.finish(plan.job_id, "failed", rolled_back=False)


async def test_wait_and_shutdown_without_a_run_are_no_ops(env):
    await env.wait()
    await env.shutdown()
    assert env.active() is None


async def test_embed_service_running_is_refused(running, fake_embed):
    """M8: the always-on service shares the GPU too; numbers must stay comparable."""
    from local_llm_admin.sidecars import SidecarRegistry

    running.control.sidecars = SidecarRegistry(fake_embed.settings)
    fake_embed.start()
    with pytest.raises(QualityError) as err:
        await running.start("benchmark", "current")
    assert err.value.code == 409 and "embeddings service" in err.value.message
    assert running.control.runner.running() is None


# --- Strata (Phase 4): compatibility of the current engine, no switching ------------------
STRATA_HEALTH = {"status": "ok", "model": "Qwen3.8-Flash-Next", "max_context": 32768, "images": False,
                 "loaded": True, "service": "strata"}


@pytest.fixture
def strata_env(fake_strata, fake_comfy, fake_mlx):
    import httpx

    from local_llm_admin.engines import StrataClient

    s = fake_strata.settings
    s.quality_request_timeout_s = 5
    s.quality_ready_timeout_s = 1
    s.strata_api_key = "k-strata"
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=STRATA_HEALTH)
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body.get("stream"):
                chunks = [
                    {"choices": [{"index": 0, "delta": {"content": "one two"}}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "ready"}}]}
            )
        return httpx.Response(404)

    mlx = MlxClient(s)
    path = s.admin_data_dir / "admin.sqlite"
    jobs = JobStore(path)
    runner = JobRunner(s, jobs, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    transport = httpx.MockTransport(handler)
    control = ControlService(
        s,
        jobs,
        runner,
        mlx,
        ComfyClient(s, transport=fake_comfy.transport),
        strata=StrataClient(s, transport=fake_strata.transport),
    )
    qr = QualityRunner(
        s, control, QualityStore(path), AlertStore(path), Sampler(), transport=transport, cases=P128
    )
    qr.seen = seen
    fake_strata.start()
    control.store.set_kv("intent", {"value": "strata"})
    return qr


async def test_compatibility_against_strata(strata_env):
    qr = strata_env
    done = await _finish(qr, await qr.start("compatibility", "current"))
    assert done["status"] in ("succeeded", "failed"), done
    cases = {c["case_key"]: c for c in done["cases"]}
    assert cases["plain"]["status"] == "pass" and cases["plain"]["model"] == "strata"
    assert cases["vision"]["status"] == "unsupported"  # /health reports no image input
    assert done["restoration"]["outcome"] == "not_needed"
    posts = [json.loads(r.content) for r in qr.seen if r.url.path == "/v1/chat/completions"]
    assert posts and all(p["model"] == "Qwen3.8-Flash-Next" for p in posts)
    plain = next(p for p in posts if "ready" in p["messages"][0]["content"])
    assert plain["chat_template_kwargs"] == {"enable_thinking": False}
    assert all(r.headers.get("authorization") == "Bearer k-strata" for r in qr.seen)
    assert str(qr.seen[0].url).startswith("http://strata.test/")
    assert qr.control.strata.running().running and qr.control.intent()["value"] == "strata"


@pytest.mark.parametrize(("kind", "scope"), [("benchmark", "current"), ("compatibility", "all")])
async def test_strata_refuses_benchmarks_and_other_scopes(strata_env, kind, scope):
    with pytest.raises(QualityError) as err:
        await strata_env.start(kind, scope)
    assert err.value.code == 409 and "Strata" in err.value.message
    assert strata_env.control.runner.running() is None
