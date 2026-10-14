"""M7: the generic sidecar controls with one sidecar (OpenJev)."""

import json
import subprocess
import time
from types import SimpleNamespace

import pytest

from local_llm_admin.alerts import AlertStore
from local_llm_admin.control import ControlError, ControlService
from local_llm_admin.decision import DecisionClient
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient
from local_llm_admin.sidecars import SidecarMonitor, SidecarRegistry, read_intent, write_intent
from local_llm_admin.tts import TtsClient

from .test_control import _start_mlx, _wait
from .test_control_decision import fast_poll as fast_poll  # noqa: F401
from .test_control_decision import svc as svc  # noqa: F401
from .test_decision import QWEN36


async def test_generic_start_stop_by_name(svc, fake_decision):  # noqa: F811
    job = await svc.sidecar_start("openjev", "normal")
    assert job["action"] == "sidecar" and job["name"] == "openjev" and job["from_name"] is None
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert read_intent(svc.store)["name"] == "openjev" and svc.sidecar_running().name == "openjev"
    job = await svc.sidecar_stop("openjev")
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert read_intent(svc.store)["value"] == "stopped" and svc.sidecar_running() is None


async def test_unknown_or_disabled_sidecar_is_404(svc):  # noqa: F811
    with pytest.raises(ControlError) as e:
        await svc.sidecar_start("nope", "normal")
    assert e.value.code == 404
    svc.settings.decision_enabled = False
    with pytest.raises(ControlError) as e:
        await svc.sidecar_stop("openjev")
    assert e.value.code == 404


async def test_budget_view_by_name(svc):  # noqa: F811
    b = await svc.sidecar_budget("openjev", "normal")
    assert b.ok and b.stop_first is None and b.lines[1] == "decision 20.0 GB"


def test_interrupted_sidecar_job_does_not_suspend(svc):  # noqa: F811
    from .test_control import _running_row

    row = _running_row("j2")
    row.update(action="sidecar", name="qwen-tts", from_name="openjev", to_name="running")
    svc.store.save_job(row)
    svc.startup()
    assert svc.store.get_job("j2")["status"] == "interrupted" and svc.recovery_blocked() is None


# --- two sidecars: swaps, conflicts, warm-up (Task 14) ---


def _race_tts(fake_tts):
    """A TTS process started by hand on the Mac, past every guard (fake_tts's teardown kills it)."""
    subprocess.run(
        [
            "bash",
            "-c",
            f'(exec -a "python -m mlx_audio.server" sleep 60) & echo $! > {fake_tts.root}/run/qwen-tts.pid',
        ],
        check=True,
    )


@pytest.fixture
def svc2(fake_tts, fake_decision, fake_comfy, fake_mlx):
    s = fake_tts.settings
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    dj = DecisionClient(s, transport=fake_decision.transport)
    reg = SidecarRegistry(s, {"openjev": fake_decision.transport, "qwen-tts": fake_tts.transport})
    tts = TtsClient(s, transport=fake_tts.transport)
    reg.set_warmup("qwen-tts", tts.warmup_sync)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    return ControlService(s, store, runner, mlx, comfy, decision=dj, sidecars=reg)


def _calls(fake_mlx):
    p = fake_mlx.state / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


async def test_tts_start_warms_up(svc2, fake_tts, fake_mlx):
    _start_mlx(svc2)
    job = await svc2.sidecar_start("qwen-tts", "normal")
    done = _wait(svc2, job["id"])
    assert done["status"] == "succeeded", done
    assert fake_tts.requests and fake_tts.requests[0]["input"] == "Hi."
    assert read_intent(svc2.store)["name"] == "qwen-tts"


async def test_swap_needs_confirm_then_stops_old_first(svc2, fake_decision, fake_mlx):
    _start_mlx(svc2)
    fake_decision.start()
    with pytest.raises(ControlError, match="confirm to stop it and start the TTS sidecar") as e:
        await svc2.sidecar_start("qwen-tts", "normal")
    assert e.value.code == 409 and "qwen-tts start" not in _calls(fake_mlx)
    job = await svc2.sidecar_start("qwen-tts", "normal", confirm=True)
    assert job["from_name"] == "openjev"
    assert _wait(svc2, job["id"])["status"] == "succeeded"
    calls = _calls(fake_mlx)
    assert calls.index("openjev stop") < calls.index("qwen-tts start")
    assert [s.name for s in svc2.sidecars.running()] == ["qwen-tts"]
    intent = read_intent(svc2.store)
    assert (intent["name"], intent["value"]) == ("qwen-tts", "running")


async def test_failed_swap_leaves_both_stopped_without_alert(svc2, fake_decision, fake_tts, fake_mlx):
    _start_mlx(svc2)
    fake_decision.start()
    write_intent(svc2.store, "openjev", "running")
    (fake_mlx.state / "tts_never_ready").write_text("")
    svc2.settings.tts_ready_timeout_s = 0.3
    job = await svc2.sidecar_start("qwen-tts", "normal", confirm=True)
    done = _wait(svc2, job["id"])
    assert done["status"] == "timeout" and done["rolled_back"] is True
    assert svc2.sidecars.running() == []
    assert _calls(fake_mlx).count("openjev start") == 1  # only our own start: never restarted
    assert read_intent(svc2.store)["value"] == "stopped"
    alerts = AlertStore(svc2.settings.admin_data_dir / "admin.sqlite")
    mon = SidecarMonitor(svc2.settings, svc2.sidecars, svc2.store, alerts)
    await mon(SimpleNamespace(job_running=False, wall=time.time()), None)
    assert alerts.open_condition_keys("decision_down") == []
    assert alerts.open_condition_keys("tts_down") == []


async def test_start_refused_while_two_run(svc2, fake_decision, fake_tts, fake_mlx):
    fake_decision.start()
    _race_tts(fake_tts)
    with pytest.raises(ControlError, match="more than one sidecar"):
        await svc2.sidecar_start("openjev", "normal")


async def test_engine_refuses_while_two_sidecars_run(svc2, fake_decision, fake_tts, fake_mlx):
    _start_mlx(svc2)
    fake_decision.start()
    _race_tts(fake_tts)
    with pytest.raises(ControlError, match="more than one sidecar"):
        await svc2.engine("comfyui")


async def test_lifecycle_uses_the_running_sidecars_allow_list(svc2, fake_tts, fake_mlx):
    _start_mlx(svc2)
    fake_tts.start()
    svc2.settings.tts_allowed_mlx = "qwen"
    fake_mlx.set_list(f"* qwen {fake_mlx.root}/models/Qwen\n  big {fake_mlx.root}/models/Qwen\n")
    with pytest.raises(ControlError, match="the TTS sidecar is running"):
        await svc2.lifecycle("switch", "big")
