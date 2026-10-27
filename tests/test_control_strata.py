"""Engine control with Strata as the third exclusive engine (Strata Phase 2)."""

import subprocess
import time

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.control import ControlError, ControlService, adopted_intent, intent_after
from local_llm_admin.engines import ComfyClient, StrataClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


@pytest.fixture
def svc(fake_strata, fake_comfy):
    s = fake_strata.settings
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    strata = StrataClient(s, transport=fake_strata.transport)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    return ControlService(s, store, runner, mlx, comfy, strata=strata)


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


def test_intent_helpers_know_strata():
    assert intent_after({"action": "engine", "to_name": "strata"}) == "strata"
    assert adopted_intent({"engine": "strata", "mlx_name": None}) == "strata"


async def test_mlx_to_strata_and_back(svc, fake_strata):
    subprocess.run([str(svc.settings.mlx_model_path), "start", "qwen"], capture_output=True)
    job = _wait(svc, (await svc.engine("strata"))["id"])
    assert job["status"] == "succeeded", job
    assert (job["from_name"], job["to_name"]) == ("qwen", "strata")
    assert _intent(svc) == "strata"
    obs = svc.observe()
    assert obs["engine"] == "strata" and obs["generation"]["pid"] == svc.strata.running().pid
    job = _wait(svc, (await svc.engine("mlx:qwen"))["id"])
    assert job["status"] == "succeeded", job
    assert svc.strata.running().running is False and _intent(svc) == "mlx:qwen"


async def test_strata_to_off(svc, fake_strata):
    fake_strata.start()
    job = _wait(svc, (await svc.engine("off"))["id"])
    assert job["status"] == "succeeded" and job["from_name"] == "strata"
    assert _intent(svc) == "off" and svc.strata.running().running is False


async def test_strata_never_ready_rolls_back_to_mlx(svc, fake_strata):
    subprocess.run([str(svc.settings.mlx_model_path), "start", "qwen"], capture_output=True)
    fake_strata.flags["loading"] = True
    job = _wait(svc, (await svc.engine("strata"))["id"], timeout=20)
    assert job["status"] == "timeout" and job["rolled_back"] is True
    assert svc.strata.running().running is False and svc.mlx.engine_running()


async def test_strata_target_refused_when_disabled(svc):
    svc.settings.strata_enabled = False
    with pytest.raises(ControlError) as ei:
        await svc.engine("strata")
    assert ei.value.code == 404


async def test_mlx_lifecycle_refused_while_strata_runs(svc, fake_strata):
    fake_strata.start()
    with pytest.raises(ControlError) as ei:
        await svc.lifecycle("start", "qwen")
    assert ei.value.code == 409 and "Strata" in ei.value.message


async def test_sidecar_and_service_plans_refuse_beside_strata(svc, fake_strata):
    fake_strata.start()
    assert svc.exclusive_running() == "strata"


async def test_switching_strata_packs_from_the_engine_control(svc, fake_strata):
    fake_strata.start()  # the remembered pack (Q2_0)
    assert (await svc.strata.status())["pack"] == "Q2_0"
    job = _wait(svc, (await svc.engine("strata:IQ3_XXS"))["id"])
    assert job["status"] == "succeeded", job
    assert (job["from_name"], job["to_name"], job["name"]) == ("strata", "strata", "IQ3_XXS")
    calls = (fake_strata.state / "calls.log").read_text().splitlines()
    assert calls[-2:] == ["strata stop", "strata start IQ3_XXS"]
    assert (await svc.strata.status())["pack"] == "IQ3_XXS" and _intent(svc) == "strata"
    with pytest.raises(ControlError) as ei:
        await svc.engine("strata:IQ3_XXS")
    assert ei.value.code == 409


async def test_disabled_owner_is_still_stopped_before_start(svc, fake_strata):
    fake_strata.start()
    svc.settings.strata_enabled = False
    job = _wait(svc, (await svc.engine("mlx:qwen"))["id"])
    assert job["status"] == "succeeded", job
    assert not svc.strata.running().running
    assert svc.mlx.engine_running()


async def test_unknown_listener_blocks_activation_and_releases_claim(svc, monkeypatch):
    from local_llm_admin.mlx import ProcessStatus
    monkeypatch.setattr(svc.comfy, "running", lambda: ProcessStatus(True, None))
    with pytest.raises(ControlError, match="ownership"):
        await svc.engine("mlx:qwen", force=True)
    assert svc.runner.running() is None
    claim = svc.runner.claim()
    svc.runner.release(claim)


async def test_generation_changes_before_execution_never_stop_source(svc, fake_strata, monkeypatch):
    from dataclasses import replace
    fake_strata.start()
    original = svc._submitted
    observe = svc.backends.observe
    def submit_after_reuse(submit):
        def reused(**kwargs):
            return [replace(o, generation={"pid": o.generation["pid"], "started": 9999999999})
                    if o.backend == "strata" and o.generation else o
                    for o in observe(**kwargs)]
        monkeypatch.setattr(svc.backends, "observe", reused)
        return original(submit)
    monkeypatch.setattr(svc, "_submitted", submit_after_reuse)
    job = _wait(svc, (await svc.engine("mlx:qwen"))["id"])
    assert job["status"] == "failed"
    assert svc.strata.running().running
    assert "strata stop" not in (fake_strata.state / "calls.log").read_text()


def test_fourth_engine_resource_policy_is_used(svc, monkeypatch):
    from local_llm_admin.backend_types import EngineObservation
    from local_llm_admin.backends import BackendRegistry
    from local_llm_admin.sidecars import SidecarSpec

    from .test_backends import FourthBackend
    fourth = FourthBackend(svc.settings, svc.mlx)
    monkeypatch.setattr(fourth, "observe", lambda: EngineObservation(
        "fourth", "alpha", state="ready", ownership="verified"))
    svc.backends = BackendRegistry((*svc.backends, fourth))
    spec = SidecarSpec(name="qwen-embed", label="embeddings", title="Embeddings",
                       port=8084, kind="service")
    plan = svc._service_plan(spec, "normal")
    assert not plan.ok
    assert any("Fourth is running" in reason for reason in plan.reasons)
    assert svc.exclusive_running() == "fourth"


async def test_queue_cancel_revalidates_generation_before_post(svc, fake_comfy, monkeypatch):
    from dataclasses import replace
    subprocess.run([str(svc.settings.comfyui_path), "start"], capture_output=True)
    fake_comfy.queue["queue_pending"] = [[1, "queued-prompt"]]
    original = svc._submitted
    observe = svc.backends.observe
    def changed(submit):
        def reused(**kwargs):
            return [replace(o, generation={"pid": o.generation["pid"], "started": 9999999999})
                    if o.backend == "comfyui" and o.generation else o
                    for o in observe(**kwargs)]
        monkeypatch.setattr(svc.backends, "observe", reused)
        return original(submit)
    monkeypatch.setattr(svc, "_submitted", changed)
    job = _wait(svc, (await svc.engine("mlx:qwen", force=True))["id"])
    assert job["status"] == "failed"
    assert all(method != "POST" for method, _, _ in fake_comfy.flags["requests"])


async def test_owner_appearing_after_source_exit_blocks_start_and_rollback(svc, fake_strata):
    fake_strata.start()
    original_down = svc.strata.down_sync
    def down_with_new_owner():
        if not original_down():
            return False
        subprocess.run([str(svc.settings.comfyui_path), "start"], capture_output=True)
        return True
    svc.strata.down_sync = down_with_new_owner
    job = _wait(svc, (await svc.engine("mlx:qwen"))["id"])
    assert job["status"] == "failed" and job["rolled_back"] is False
    assert svc.comfy.running().running and not svc.mlx.engine_running()
    calls = (fake_strata.state / "calls.log").read_text().splitlines()
    assert calls.count("strata start") == 1


async def test_observation_failure_releases_reservation(svc, monkeypatch):
    def unavailable(**kwargs):
        raise OSError("process observation unavailable")
    monkeypatch.setattr(svc.backends, "observe", unavailable)
    with pytest.raises(OSError):
        await svc.engine("mlx:qwen")
    claim = svc.runner.claim()
    svc.runner.release(claim)


async def test_disabled_setting_rechecked_at_execution(svc, monkeypatch):
    original = svc._submitted
    def disable_then_submit(submit):
        svc.settings.strata_enabled = False
        return original(submit)
    monkeypatch.setattr(svc, "_submitted", disable_then_submit)
    job = _wait(svc, (await svc.engine("strata"))["id"])
    assert job["status"] == "failed"
    assert not svc.strata.running().running


async def test_activation_with_unknown_generation_blocks_rollback(svc, fake_strata, monkeypatch):
    from dataclasses import replace
    subprocess.run([str(svc.settings.mlx_model_path), "start", "qwen"], capture_output=True)
    backend = svc.backends.get("strata")
    original = backend.observe
    def unknown_after_start():
        observed = original()
        return replace(observed, ownership="unknown", state="unknown") if observed.running else observed
    monkeypatch.setattr(backend, "observe", unknown_after_start)
    job = _wait(svc, (await svc.engine("strata"))["id"])
    assert job["status"] == "failed" and job["rolled_back"] is False
    assert svc.strata.running().running and not svc.mlx.engine_running()


def test_unknown_engine_blocks_both_sidecar_policy_paths(svc, monkeypatch):
    from local_llm_admin.backend_types import EngineObservation
    from local_llm_admin.sidecars import SidecarSpec
    spec = SidecarSpec(name="qwen-embed", label="embeddings", title="Embeddings", port=8084)
    monkeypatch.setattr(svc.backends, "observe", lambda **kw: [
        EngineObservation("mlx", state="unknown")])
    with pytest.raises(ControlError, match="ownership"):
        svc._service_plan(spec, "normal")
    with pytest.raises(ControlError, match="ownership"):
        svc._sidecar_plan(spec, None, False, "normal")


async def test_enable_flag_changing_during_observation_is_controlled_refusal(svc, monkeypatch):
    async def disabled_during_details(observations, **kwargs):
        svc.settings.strata_enabled = False
        return observations
    monkeypatch.setattr(svc.backends, "observed_details", disabled_during_details)
    with pytest.raises(ControlError, match="not enabled") as error:
        await svc.engine("strata")
    assert error.value.code == 404
    claim = svc.runner.claim()
    svc.runner.release(claim)
