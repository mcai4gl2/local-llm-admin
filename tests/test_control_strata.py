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
