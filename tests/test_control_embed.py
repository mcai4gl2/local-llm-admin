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
    control = ControlService(s, store, runner, mlx, comfy, decision=dj, sidecars=reg)
    control.memory_pressure = lambda: "normal"
    return control


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


async def test_start_counts_disabled_but_running_sibling(svc3, monkeypatch):
    from dataclasses import replace

    from local_llm_admin import sidecars
    original_status = sidecars.SidecarProcess.status
    def third(settings):
        return replace(sidecars.embed_spec(settings), name="third", port=8099,
                       intent_key="third_intent", models=(sidecars.ServiceModel("third-model", "x"),),
                       footprint_gb=60.0, enabled=False)
    monkeypatch.setattr(sidecars, "SERVICE_BUILDERS", [sidecars.embed_spec, third])
    monkeypatch.setattr(sidecars.SidecarProcess, "status", lambda self:
        sidecars.SidecarStatus(True, 123) if self.spec.name == "third" else original_status(self))
    with pytest.raises(ControlError, match="exceeds"):
        await svc3.service_start("qwen-embed", "normal")
    assert svc3.runner.running() is None


async def test_explicit_stop_clears_suspended_running_intent(svc3, fake_mlx):
    from local_llm_admin.sidecars import write_service_intent
    write_service_intent(svc3.store, svc3.sidecars.get_service("qwen-embed"), "running")
    done = _wait(svc3, (await svc3.service_stop("qwen-embed"))["id"])
    assert done["status"] == "succeeded" and _intent(svc3) == "stopped"
    assert "qwen-embed stop" not in _calls(fake_mlx)


async def test_disabled_running_service_can_be_stopped(svc3):
    await _start_embed(svc3)
    svc3.settings.embed_enabled = False
    done = _wait(svc3, (await svc3.service_stop("qwen-embed"))["id"])
    assert done["status"] == "succeeded" and _running(svc3) == []


async def test_restore_budget_is_checked_again_before_start(svc3, fake_mlx, monkeypatch):
    _start_mlx(svc3)
    await _start_embed(svc3)
    _wait(svc3, (await svc3.engine("comfyui"))["id"], timeout=20)
    svc3.memory_pressure = lambda: "normal"
    # Aggregate target fits; pressure changes after planning, before service restoration.
    original = svc3._service_plan
    def now_critical(spec, pressure):
        return original(spec, "critical")
    monkeypatch.setattr(svc3, "_service_plan", now_critical)
    done = _wait(svc3, (await svc3.engine("mlx:qwen"))["id"], timeout=20)
    assert done["status"] == "succeeded" and _running(svc3) == []
    assert "memory pressure is critical" in done["output"]
    assert _calls(fake_mlx).count("qwen-embed start") == 1


async def test_target_budget_counts_all_pending_restores(svc3, monkeypatch):
    from dataclasses import replace

    from local_llm_admin import sidecars
    from local_llm_admin.sidecars import write_service_intent
    def third(settings):
        return replace(sidecars.embed_spec(settings), name="third", port=8099,
                       intent_key="third_intent", models=(sidecars.ServiceModel("third-model", "x"),),
                       footprint_gb=60.0)
    monkeypatch.setattr(sidecars, "SERVICE_BUILDERS", [sidecars.embed_spec, third])
    for spec in svc3.sidecars.services():
        write_service_intent(svc3.store, spec, "running")
    from .test_control import _start_comfy
    _start_comfy(svc3)
    svc3.memory_pressure = lambda: "normal"
    with pytest.raises(ControlError, match="exceeds"):
        await svc3.engine("mlx:qwen")
    assert svc3.comfy.running().running


async def test_port_only_resident_owner_refuses_admission(svc3, monkeypatch):
    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus
    original = SidecarProcess.status
    monkeypatch.setattr(SidecarProcess, "status", lambda self:
        SidecarStatus(True, None, owned=False) if self.spec.name == "qwen-embed" else original(self))
    with pytest.raises(ControlError, match="ownership"):
        await svc3.sidecar_start("openjev", "normal")
    with pytest.raises(ControlError, match="ownership"):
        await svc3.engine("comfyui")


async def test_unknown_pressure_refuses_target_with_running_services(svc3):
    _start_mlx(svc3)
    await _start_embed(svc3)
    svc3.memory_pressure = lambda: None
    with pytest.raises(ControlError, match="pressure is unavailable"):
        await svc3.lifecycle("switch", "qwen")
    assert _running(svc3) == ["qwen-embed"]


async def test_busy_service_stop_and_exclusive_switch_require_force(svc3):
    await _start_embed(svc3)
    svc3.sidecars.set_activity("qwen-embed", lambda: True)
    with pytest.raises(ControlError, match="active work"):
        await svc3.service_stop("qwen-embed")
    with pytest.raises(ControlError, match="active work"):
        await svc3.engine("comfyui")
    done = _wait(svc3, (await svc3.engine("comfyui", force=True))["id"], timeout=20)
    assert done["status"] == "succeeded" and _running(svc3) == []


async def test_forced_service_stop_uses_existing_lifecycle_lock(svc3):
    await _start_embed(svc3)
    svc3.sidecars.set_activity("qwen-embed", lambda: True)
    done = _wait(svc3, (await svc3.service_stop("qwen-embed", force=True))["id"])
    assert done["status"] == "succeeded" and _intent(svc3) == "stopped"


async def test_restore_rechecks_owners_and_refuses_a_second_start(svc3):
    await _start_embed(svc3)
    with pytest.raises(RuntimeError, match="acquired resources"):
        svc3._admit_service_restore(svc3.sidecars.get_service("qwen-embed"))


async def test_catalog_drift_refuses_admission_before_a_job_is_submitted(svc3, monkeypatch):
    monkeypatch.setattr(svc3.sidecars, "catalog_error", lambda: "Resident host catalog differs.")
    with pytest.raises(ControlError, match="catalog differs"):
        await svc3.service_start("qwen-embed", "normal")
    assert svc3.runner.running() is None
    assert svc3._target_mlx_estimate("unregistered") is None


async def test_conflicting_slot_owners_refuse_resident_admission(svc3, monkeypatch):
    monkeypatch.setattr(svc3.sidecars, "running", lambda: svc3.sidecars.specs(enabled_only=False))
    with pytest.raises(ControlError, match="running"):
        await svc3.service_budget("qwen-embed", "normal")


async def test_refused_start_never_rolls_back_an_owner_started_by_another_caller(svc3, fake_embed, monkeypatch):
    from local_llm_admin.jobs import Step
    def raced_start():
        fake_embed.start()
        raise RuntimeError("already running; no process created by this job")
    original = svc3._sidecar_start_steps
    monkeypatch.setattr(svc3, "_sidecar_start_steps", lambda spec, **kwargs:
                        [Step("racing start", call=raced_start), *original(spec)[1:]])
    done = _wait(svc3, (await svc3.service_start("qwen-embed", "normal"))["id"])
    assert done["status"] == "failed" and _running(svc3) == ["qwen-embed"]


async def test_generation_checked_start_binds_failed_warmup_cleanup_to_its_token(svc3, tmp_path, monkeypatch):
    import httpx

    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus
    root = tmp_path / "ocr"
    root.mkdir()
    script = root / "cli"
    script.write_text("""#!/usr/bin/env python3
import os,pathlib,sys
root=pathlib.Path(os.environ['FAKE_OCR_ROOT'])
with (root/'calls').open('a') as log: log.write(' '.join(sys.argv[1:])+'\\n')
if sys.argv[1]=='start':
    (root/'token').write_text(sys.argv[3]); (root/'ready').touch()
elif sys.argv[1]=='stop':
    if len(sys.argv)>2 and (root/'token').read_text()!=sys.argv[3]: sys.exit(1)
    (root/'ready').unlink(missing_ok=True)
""")
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_OCR_ROOT", str(root))
    s = svc3.settings
    s.ocr_enabled, s.ocr_port, s.ocr_vlm_port = True, 8097, 8098
    s.ocr_root, s.ocr_bin, s.ocr_footprint_gb = root, script, 12.0
    s.ocr_ready_timeout_s = 0.05
    original = SidecarProcess.status
    monkeypatch.setattr(SidecarProcess, "status", lambda self: SidecarStatus(
        (root / "ready").exists(), 123) if self.spec.name == "paddleocr-vl" else original(self))
    svc3.sidecars._transports["paddleocr-vl"] = httpx.MockTransport(lambda _: httpx.Response(200))
    svc3.sidecars.set_warmup("paddleocr-vl", lambda: False)
    done = _wait(svc3, (await svc3.service_start("paddleocr-vl", "normal"))["id"])
    assert done["status"] == "timeout" and done["rolled_back"] is True
    start, stop = [line.split() for line in (root / "calls").read_text().splitlines()]
    assert start[:2] == ["start", "--owner-token"]
    assert stop[:2] == ["stop", "--if-owner-token"] and start[2] == stop[2]
    assert not (root / "ready").exists()
    # The same ownership protocol applies to restoration after an exclusive engine.
    from local_llm_admin.sidecars import write_service_intent

    from .test_control import _start_comfy
    s.ocr_allowed_mlx = "qwen"
    write_service_intent(svc3.store, svc3.sidecars.get_service("paddleocr-vl"), "running")
    _start_comfy(svc3)
    restored = _wait(svc3, (await svc3.engine("mlx:qwen"))["id"], timeout=20)
    assert restored["status"] == "succeeded" and not (root / "ready").exists()
    calls = [line.split() for line in (root / "calls").read_text().splitlines()]
    assert len(calls) == 4 and calls[2][2] == calls[3][2] and calls[2][2] != start[2]


async def test_generation_checked_stop_verifies_cleanup_when_all_processes_are_down(svc3, fake_mlx, monkeypatch):
    from dataclasses import replace
    original = svc3.sidecars.get_service
    monkeypatch.setattr(svc3.sidecars, "get_service", lambda name, **kwargs:
                        replace(original(name, **kwargs), generation_checked=True))
    done = _wait(svc3, (await svc3.service_stop("qwen-embed"))["id"])
    assert done["status"] == "succeeded"
    assert "qwen-embed stop" in _calls(fake_mlx)
