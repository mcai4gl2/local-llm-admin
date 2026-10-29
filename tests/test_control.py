import asyncio
import os
import subprocess
import time

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.control import ControlError, ControlService, process_generation
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobConflict, JobRunner, JobStore, Step
from local_llm_admin.mlx import MlxClient


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


@pytest.fixture
def svc(fake_comfy):
    s = fake_comfy.settings
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    return ControlService(s, store, runner, mlx, comfy)


def _cli(*args):
    subprocess.run([str(a) for a in args], capture_output=True, check=False)


def _start_mlx(svc):
    _cli(svc.settings.mlx_model_path, "start", "qwen")


def _start_comfy(svc):
    _cli(svc.settings.comfyui_path, "start")


def _wait(svc, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = svc.runner.get(job_id)
        if job["status"] != "running" and svc.runner.running() is None:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _intent(svc):
    got = svc.intent()
    return got["value"] if got else None


# --- intent after completion -------------------------------------------------


async def test_stop_sets_intent_off(svc):
    _start_mlx(svc)
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    _wait(svc, (await svc.lifecycle("stop", None))["id"])
    assert _intent(svc) == "off"


async def test_switch_and_bare_start_set_mlx_intent(svc):
    _start_mlx(svc)
    job = _wait(svc, (await svc.lifecycle("switch", "gemma"))["id"])
    assert _intent(svc) == "mlx:gemma" and svc.intent()["job_id"] == job["id"]
    _wait(svc, (await svc.lifecycle("stop", None))["id"])
    _wait(svc, (await svc.lifecycle("start", None))["id"])
    assert _intent(svc) == "mlx:qwen"  # MLX_ACTIVE_NAME from active.env


async def test_failed_job_keeps_prior_intent(svc, fake_mlx):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    fake_mlx.set_exit(1)
    _wait(svc, (await svc.lifecycle("stop", None))["id"])
    assert _intent(svc) == "mlx:qwen"


async def test_engine_switch_sets_target_intent(svc):
    _start_mlx(svc)
    _wait(svc, (await svc.engine("comfyui"))["id"])
    assert _intent(svc) == "comfyui"
    _wait(svc, (await svc.engine("off"))["id"])
    assert _intent(svc) == "off"


def _sequence(svc, *, rollback_ok, source="admin"):
    fail = lambda: (_ for _ in ()).throw(RuntimeError("no"))  # noqa: E731
    return svc.runner.submit_sequence(
        "engine",
        name=None,
        from_name="qwen",
        to_name="comfyui",
        steps=[Step("stop", call=lambda: None), Step("start", call=fail)],
        rollback=[Step("restore", call=(lambda: None) if rollback_ok else fail)],
        rollback_after=1,
        source=source,
    )


def test_successful_rollback_restores_prior_intent(svc):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    _wait(svc, _sequence(svc, rollback_ok=True)["id"])
    assert _intent(svc) == "mlx:qwen" and svc.recovery_blocked() is None


def test_failed_rollback_records_unknown_and_suspends(svc):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    _wait(svc, _sequence(svc, rollback_ok=False)["id"])
    assert _intent(svc) == "unknown"
    assert "rollback" in svc.recovery_blocked()


async def test_admin_success_clears_suspension_but_watchdog_success_does_not(svc):
    svc.store.set_kv("recovery_suspended", {"reason": "rollback failed"})
    _wait(svc, (await svc.lifecycle("stop", None, source="watchdog"))["id"])
    assert svc.recovery_blocked() is not None
    _wait(svc, (await svc.lifecycle("stop", None))["id"])
    assert svc.recovery_blocked() is None


async def test_registration_never_changes_intent(svc, fake_mlx):
    svc.store.set_kv("intent", {"value": "off"})
    (fake_mlx.root / "models/New").mkdir()
    for f, data in (("config.json", b"{}"), ("model.safetensors", b"x" * 10)):
        (fake_mlx.root / "models/New" / f).write_bytes(data)
        os.utime(fake_mlx.root / "models/New" / f, (1, 1))
    os.utime(fake_mlx.root / "models/New", (1, 1))
    job = await svc.register("New", "new-model")
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert _intent(svc) == "off"


def test_unrecorded_completion_blocks_recovery_even_if_db_is_down(svc, monkeypatch):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    monkeypatch.setattr(svc.store, "add_history", lambda **kw: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(svc.store, "set_kv", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    _wait(svc, svc.runner.submit("stop", None)["id"])
    assert "not recorded" in svc.recovery_blocked()


# --- submission ----------------------------------------------------------------


async def test_duplicate_submissions_admit_exactly_one(svc, fake_mlx):
    fake_mlx.set_delay(0.3)
    results = await asyncio.gather(
        svc.lifecycle("stop", None),
        svc.lifecycle("stop", None, source="watchdog"),
        return_exceptions=True,
    )
    jobs = [r for r in results if isinstance(r, dict)]
    errors = [r for r in results if isinstance(r, ControlError)]
    assert len(jobs) == 1 and len(errors) == 1 and errors[0].code == 409
    _wait(svc, jobs[0]["id"])
    assert fake_mlx.calls() == ["stop"]


async def test_held_slot_refuses_before_validation(svc, fake_mlx):
    token = svc.runner.claim()
    with pytest.raises(ControlError) as exc:
        await svc.lifecycle("switch", "gemma", source="watchdog")
    assert exc.value.code == 409 and "another job" in exc.value.message
    svc.runner.release(token)


async def test_validation_after_claim_releases_the_slot(svc, fake_mlx):
    (fake_mlx.root / "run/mlx.lock").mkdir()
    with pytest.raises(ControlError) as exc:
        await svc.lifecycle("stop", None)
    assert exc.value.code == 409 and "mlx.lock" in exc.value.message
    (fake_mlx.root / "run/mlx.lock").rmdir()
    _wait(svc, (await svc.lifecycle("stop", None))["id"])


async def test_lock_appearing_during_validation_is_seen(svc, fake_mlx, monkeypatch):
    """The lock is checked after the slot is held, not before."""
    real = svc.runner.claim

    def claim_then_lock():
        token = real()
        (fake_mlx.root / "run/mlx.lock").mkdir()
        return token

    monkeypatch.setattr(svc.runner, "claim", claim_then_lock)
    with pytest.raises(ControlError, match="mlx.lock"):
        await svc.lifecycle("stop", None)
    assert svc.runner.running() is None and fake_mlx.calls() == []


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda s: s.lifecycle("switch", "a;b"), 400),
        (lambda s: s.lifecycle("switch", "nosuch"), 404),
        (lambda s: s.engine("bogus"), 400),
        (lambda s: s.engine("mlx:nosuch"), 404),
        (lambda s: s.engine("off"), 409),
        (lambda s: s.register("missing", "x"), 404),
    ],
)
async def test_refusals_release_the_slot(svc, call, code):
    with pytest.raises(ControlError) as exc:
        await call(svc)
    assert exc.value.code == code
    svc.runner.release(svc.runner.claim())  # slot is free


async def test_drive_missing_refused(svc):
    svc.settings.mlx_root = svc.settings.mlx_root.parent / "gone"
    with pytest.raises(ControlError, match="not mounted"):
        await svc.lifecycle("stop", None)


async def test_mlx_start_refused_while_comfyui_runs(svc):
    _start_comfy(svc)
    with pytest.raises(ControlError, match="Engine control"):
        await svc.lifecycle("start", None)


async def test_reservation_storage_failure_is_503(svc, monkeypatch, fake_mlx):
    monkeypatch.setattr(svc.store, "save_job", lambda job: (_ for _ in ()).throw(OSError("ro")))
    with pytest.raises(ControlError) as exc:
        await svc.lifecycle("stop", None)
    assert exc.value.code == 503 and fake_mlx.calls() == []


async def test_runner_conflict_at_submit_is_409(svc, monkeypatch):
    def raced(*a, **kw):
        raise JobConflict("other")

    monkeypatch.setattr(svc.runner, "submit_sequence", raced)
    with pytest.raises(ControlError) as exc:
        await svc.lifecycle("stop", None)
    assert exc.value.code == 409


# --- startup -------------------------------------------------------------------


def _running_row(job_id):
    return {
        "id": job_id,
        "action": "engine",
        "name": None,
        "from_name": "qwen",
        "to_name": "comfyui",
        "source": "admin",
        "status": "running",
        "returncode": None,
        "started": 1.0,
        "finished": None,
        "rolled_back": None,
        "observed_engine": None,
        "output": "",
    }


def test_startup_after_interrupted_job_is_unknown_and_suspended(svc, fake_mlx):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    svc.store.save_job(_running_row("j1"))
    _start_mlx(svc)
    calls_before = fake_mlx.calls()
    svc.startup()
    assert svc.store.get_job("j1")["status"] == "interrupted"
    assert _intent(svc) == "unknown" and "interrupted" in svc.recovery_blocked()
    assert fake_mlx.calls() == calls_before  # nothing replayed


def test_startup_keeps_existing_intent(svc):
    svc.store.set_kv("intent", {"value": "comfyui"})
    svc.startup()
    assert _intent(svc) == "comfyui" and svc.recovery_blocked() is None


def test_first_run_adopts_running_mlx(svc):
    _start_mlx(svc)
    svc.startup()
    assert _intent(svc) == "mlx:qwen" and svc.intent()["reason"] == "adopted"


def test_first_run_adopts_off_not_stale_history(svc):
    svc.store.add_history(
        ts=1.0,
        action="switch",
        from_name="qwen",
        to_name="gemma",
        result="succeeded",
        duration_s=1,
        output_tail="",
        source="admin",
    )
    svc.startup()
    assert _intent(svc) == "off"


def test_first_run_adopts_comfyui(svc):
    _start_comfy(svc)
    svc.startup()
    assert _intent(svc) == "comfyui"


def test_first_run_conflict_stays_unknown(svc):
    _start_comfy(svc)
    _start_mlx_without_guard(svc)
    svc.startup()
    assert _intent(svc) == "unknown"


def _start_mlx_without_guard(svc):
    pidfile = svc.settings.run_dir / "mlx-server.pid"
    subprocess.run(["sh", "-c", f"sleep 60 >/dev/null 2>&1 & echo $! > {pidfile}"], check=False)


def test_first_run_with_drive_missing_stays_unknown(svc):
    svc.settings.mlx_root = svc.settings.mlx_root.parent / "gone"
    svc.startup()
    assert _intent(svc) == "unknown"


def test_first_run_mlx_without_active_name_stays_unknown(svc):
    _start_mlx(svc)
    (svc.settings.run_dir / "active.env").unlink()
    svc.startup()
    assert _intent(svc) == "unknown"


# --- observation ---------------------------------------------------------------


def test_observe_reports_generation_identity(svc):
    _start_mlx(svc)
    obs = svc.observe()
    pid = int((svc.settings.run_dir / "mlx-server.pid").read_text())
    assert obs["engine"] == "mlx" and obs["mlx_name"] == "qwen"
    assert obs["generation"]["pid"] == pid and obs["generation"]["started"] > 0


def test_process_generation_distinguishes_pid_reuse():
    gen = process_generation(os.getpid())
    assert gen is not None and gen["pid"] == os.getpid()
    assert gen != {"pid": os.getpid(), "started": gen["started"] + 1}
    assert process_generation(None) is None
    assert process_generation(2**22 + 12345) is None  # no such process


def test_observe_none_and_unknown(svc):
    assert svc.observe()["engine"] == "none"
    svc.settings.mlx_root = svc.settings.mlx_root.parent / "gone"
    assert svc.observe()["engine"] == "unknown"


def test_observe_is_recorded_on_jobs(svc):
    _start_mlx(svc)
    job = _wait(svc, svc.runner.submit("stop", None)["id"])
    assert '"engine": "none"' in svc.store.get_job(job["id"])["observed_engine"]


def test_unrecorded_completion_persists_unknown_when_kv_works(svc, monkeypatch):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    monkeypatch.setattr(svc.store, "add_history", lambda **kw: (_ for _ in ()).throw(OSError()))
    _wait(svc, svc.runner.submit("stop", None)["id"])
    assert _intent(svc) == "unknown"
    assert "not recorded" in svc.store.get_kv("recovery_suspended")["reason"]


def test_adoption_skipped_while_a_job_holds_the_slot(svc):
    token = svc.runner.claim()
    svc.adopt_initial()
    assert svc.intent() is None
    svc.runner.release(token)


# --- M2: quality runs hold the slot ---------------------------------------------------


def _quality(svc):
    return svc.runner.begin(
        "quality",
        name=None,
        from_name="qwen",
        to_name="qwen",
        source="benchmark",
        claim=svc.runner.claim(),
    )


def test_restored_quality_run_keeps_intent_and_suspension_state(svc):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    svc.store.set_kv("recovery_suspended", {"reason": "older"})
    job = _quality(svc)
    svc.runner.finish(job["id"], "succeeded", rolled_back=True)
    assert _intent(svc) == "mlx:qwen"
    assert svc.recovery_blocked() == "older"  # a benchmark is not a manual lifecycle action


@pytest.mark.parametrize("status", ["failed", "cancelled", "succeeded"])
def test_unrestored_quality_run_is_unknown_and_suspends(svc, status):
    svc.store.set_kv("intent", {"value": "mlx:qwen"})
    job = _quality(svc)
    svc.runner.finish(job["id"], status, rolled_back=False)
    assert _intent(svc) == "unknown"
    assert "quality run" in svc.recovery_blocked()


async def test_lifecycle_request_during_a_quality_run_names_it(svc):
    job = _quality(svc)
    with pytest.raises(ControlError) as err:
        await svc.lifecycle("stop", None)
    assert err.value.code == 409 and "quality run" in err.value.message
    svc.runner.finish(job["id"], "cancelled", rolled_back=True)


def test_preflight_is_public(svc, fake_mlx):
    svc.preflight()
    (fake_mlx.root / "run/mlx.lock").mkdir()
    with pytest.raises(ControlError):
        svc.preflight()


async def test_direct_mlx_stop_refuses_unowned_listener(svc, monkeypatch):
    from local_llm_admin.backend_types import EngineObservation
    monkeypatch.setattr(svc.backends, "observe", lambda **kw: [
        EngineObservation("mlx", state="unknown")])
    with pytest.raises(ControlError, match="ownership"):
        await svc.lifecycle("stop", None)
    assert svc.runner.running() is None


async def test_existing_owner_blocks_direct_start_at_execution(svc, fake_mlx):
    _start_mlx(svc)
    before = list(fake_mlx.calls())
    job = _wait(svc, (await svc.lifecycle("start", "qwen"))["id"])
    assert job["status"] == "failed"
    assert [c for c in fake_mlx.calls() if c != "list"] == before
    assert svc.observe()["engine"] == "mlx"
