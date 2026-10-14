"""M8: the always-on embedding service in the control service."""

import json

import pytest

from local_llm_admin.control import ControlError, ControlService
from local_llm_admin.decision import DecisionClient
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient
from local_llm_admin.sidecars import SidecarRegistry, read_service_intent

from .test_control import _start_mlx, _wait
from .test_control_decision import fast_poll as fast_poll  # noqa: F401
from .test_decision import QWEN36


@pytest.fixture
def svc3(fake_embed, fake_decision, fake_comfy, fake_mlx, fast_poll, monkeypatch):  # noqa: F811
    from local_llm_admin import sidecars as sidecars_mod

    # budgets must not depend on the dev container's RAM
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)
    s = fake_embed.settings
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    reg = SidecarRegistry(
        s, {"openjev": fake_decision.transport, "qwen-embed": fake_embed.transport}
    )
    reg.set_warmup("qwen-embed", lambda: True)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    dj = DecisionClient(s, transport=fake_decision.transport)
    return ControlService(s, store, runner, mlx, comfy, decision=dj, sidecars=reg)


def _calls(fake_mlx):
    p = fake_mlx.state / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def _intent(svc):
    return read_service_intent(svc.store, svc.sidecars.get_service("qwen-embed"))


def _running(svc):
    return [s.name for s in svc.sidecars.services_running()]


async def _start_embed(svc):
    job = await svc.service_start("qwen-embed", "normal")
    done = _wait(svc, job["id"])
    assert done["status"] == "succeeded", done
    return done


async def test_service_start_stop_records_intent(svc3, fake_mlx):
    _start_mlx(svc3)
    job = await _start_embed(svc3)
    assert job["action"] == "service" and job["to_name"] == "running"
    assert _intent(svc3) == "running" and _running(svc3) == ["qwen-embed"]
    with pytest.raises(ControlError, match="already running"):
        await svc3.service_start("qwen-embed", "normal")
    done = _wait(svc3, (await svc3.service_stop("qwen-embed"))["id"])
    assert done["status"] == "succeeded" and _intent(svc3) == "stopped" and _running(svc3) == []
    with pytest.raises(ControlError, match="not running"):
        await svc3.service_stop("qwen-embed")


async def test_service_and_slot_names_do_not_mix(svc3):
    with pytest.raises(ControlError) as e:
        await svc3.sidecar_start("qwen-embed", "normal")
    assert e.value.code == 404
    with pytest.raises(ControlError) as e:
        await svc3.service_start("openjev", "normal")
    assert e.value.code == 404


async def test_service_start_refused_next_to_comfyui(svc3, fake_mlx):
    from .test_control import _start_comfy

    _start_comfy(svc3)
    with pytest.raises(ControlError, match="ComfyUI is running") as e:
        await svc3.service_start("qwen-embed", "normal")
    assert e.value.code == 409 and "qwen-embed start" not in _calls(fake_mlx)


async def test_failed_start_leaves_intent_stopped(svc3, fake_mlx):
    _start_mlx(svc3)
    (fake_mlx.state / "embed_never_ready").write_text("")
    svc3.settings.embed_ready_timeout_s = 0.3
    done = _wait(svc3, (await svc3.service_start("qwen-embed", "normal"))["id"])
    assert done["status"] == "timeout" and done["rolled_back"] is True
    assert _running(svc3) == [] and _intent(svc3) == "stopped"


async def test_slot_swap_leaves_embed_running(svc3, fake_mlx):
    _start_mlx(svc3)
    await _start_embed(svc3)
    plan = await svc3.sidecar_budget("openjev", "normal")
    assert f"embeddings {svc3.settings.embed_footprint_gb:.1f} GB (running)" in plan.lines
    done = _wait(svc3, (await svc3.sidecar_start("openjev", "normal"))["id"])
    assert done["status"] == "succeeded"
    assert _running(svc3) == ["qwen-embed"] and "qwen-embed stop" not in _calls(fake_mlx)
    assert [s.name for s in svc3.sidecars.running()] == ["openjev"]  # the slot never sees it


async def test_engine_round_trip_restores_embed(svc3, fake_mlx):
    _start_mlx(svc3)
    await _start_embed(svc3)
    done = _wait(svc3, (await svc3.engine("comfyui"))["id"], timeout=20)
    assert done["status"] == "succeeded", done
    assert _running(svc3) == [] and _intent(svc3) == "running"  # paused, still meant to run
    done = _wait(svc3, (await svc3.engine("mlx:qwen"))["id"], timeout=20)
    assert done["status"] == "succeeded", done
    assert _running(svc3) == ["qwen-embed"] and _intent(svc3) == "running"
    calls = _calls(fake_mlx)
    assert calls.index("qwen-embed stop") < calls.index("comfyui start")
    last_start = max(i for i, c in enumerate(calls) if c == "qwen-embed start")
    assert calls.index("comfyui stop") < last_start


async def test_restore_failure_does_not_fail_the_switch(svc3, fake_mlx):
    _start_mlx(svc3)
    await _start_embed(svc3)
    _wait(svc3, (await svc3.engine("comfyui"))["id"], timeout=20)
    (fake_mlx.state / "embed_start_exit").write_text("")
    svc3.settings.embed_ready_timeout_s = 0.3
    done = _wait(svc3, (await svc3.engine("mlx:qwen"))["id"], timeout=20)
    assert done["status"] == "succeeded" and _running(svc3) == []
    assert _intent(svc3) == "running"  # the monitor's embed_down alert reports it (Task 10)


async def test_forced_switch_stops_embed_and_clears_intent(svc3, fake_mlx):
    _start_mlx(svc3)
    await _start_embed(svc3)
    with pytest.raises(ControlError, match="the embeddings service is running and gemma"):
        await svc3.engine("mlx:gemma")
    done = _wait(svc3, (await svc3.engine("mlx:gemma", force=True))["id"], timeout=20)
    assert done["status"] == "succeeded" and _running(svc3) == [] and _intent(svc3) == "stopped"


async def test_lifecycle_switch_respects_embed_allow_list(svc3):
    _start_mlx(svc3)
    await _start_embed(svc3)
    with pytest.raises(ControlError, match="the embeddings service is running"):
        await svc3.lifecycle("switch", "gemma")


def test_interrupted_service_job_does_not_suspend(svc3):
    from .test_control import _running_row

    row = _running_row("j3")
    row.update(action="service", name="qwen-embed", from_name=None, to_name="running")
    svc3.store.save_job(row)
    svc3.startup()
    assert svc3.store.get_job("j3")["status"] == "interrupted" and svc3.recovery_blocked() is None
