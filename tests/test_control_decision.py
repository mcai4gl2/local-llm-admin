"""M6 Phase 2: starting and stopping the decision sidecar through the control service."""

import json

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin import sidecars as sidecars_mod
from local_llm_admin.control import ControlError, ControlService
from local_llm_admin.decision import DecisionClient
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient
from local_llm_admin.sidecars import read_intent, write_intent

from .test_control import _cli, _running_row, _start_comfy, _start_mlx, _wait
from .test_decision import QWEN36


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)
    monkeypatch.setattr(sidecars_mod, "system_ram_gb", lambda: 64.0)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: None)


@pytest.fixture
def svc(fake_decision, fake_comfy, fake_mlx):
    s = fake_decision.settings
    (fake_mlx.root / "models/Qwen/config.json").write_text(json.dumps(QWEN36))
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    dj = DecisionClient(s, transport=fake_decision.transport)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    return ControlService(s, store, runner, mlx, comfy, decision=dj)


def _weights(svc):
    return svc.mlx.dir_size(svc.settings.mlx_root / "models/Qwen")


def _calls(fake_mlx):
    p = fake_mlx.state / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def _dj_intent(svc):
    row = read_intent(svc.store)
    return row["value"] if row and row["name"] == "openjev" else None


async def test_start_and_stop_record_intent(svc, fake_mlx):
    _start_mlx(svc)
    job = await svc.decision_start("normal")
    assert job["action"] == "sidecar" and job["name"] == "openjev" and job["to_name"] == "running"
    done = _wait(svc, job["id"])
    assert done["status"] == "succeeded", done
    assert svc.decision.running().running and _dj_intent(svc) == "running"
    assert "openjev start" in _calls(fake_mlx)
    job = await svc.decision_stop()
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert not svc.decision.running().running and _dj_intent(svc) == "stopped"


async def test_start_with_mlx_off_counts_only_the_sidecar(svc):
    job = await svc.decision_start("normal")
    assert _wait(svc, job["id"])["status"] == "succeeded"


async def test_start_does_not_change_the_engine_intent(svc):
    _start_mlx(svc)
    svc.set_intent("mlx:qwen", "test")
    job = await svc.decision_start("normal")
    _wait(svc, job["id"])
    assert svc.intent()["value"] == "mlx:qwen"


@pytest.mark.parametrize(
    ("setup", "pressure", "code", "text"),
    [
        (lambda s, f: None, "warn", 409, "pressure is warn"),
        (lambda s, f: None, None, 409, "pressure is unavailable"),
        (
            lambda s, f: setattr(s.settings, "decision_allowed_mlx", "other"),
            "normal",
            409,
            "not allowed",
        ),
        (
            lambda s, f: (f.root / "models/Qwen/config.json").write_text("{}"),
            "normal",
            409,
            "cannot estimate",
        ),
        (lambda s, f: setattr(s.settings, "decision_footprint_gb", 60.0), "normal", 409, "exceeds"),
    ],
)
async def test_refused_start_changes_nothing(svc, fake_mlx, setup, pressure, code, text):
    _start_mlx(svc)
    setup(svc, fake_mlx)
    with pytest.raises(ControlError) as e:
        await svc.decision_start(pressure)
    assert e.value.code == code and text in e.value.message
    assert "openjev start" not in _calls(fake_mlx)
    assert svc.runner.running() is None and _dj_intent(svc) is None


async def test_refused_while_comfyui_runs(svc, fake_mlx):
    _start_comfy(svc)
    with pytest.raises(ControlError, match="ComfyUI") as e:
        await svc.decision_start("normal")
    assert e.value.code == 409 and "openjev start" not in _calls(fake_mlx)


async def test_wired_limit_binds(svc, monkeypatch):
    _start_mlx(svc)
    monkeypatch.setattr(sidecars_mod, "wired_limit_gb", lambda: 20.0)
    with pytest.raises(ControlError, match="20.0 GB limit"):
        await svc.decision_start("normal")


async def test_start_refused_when_running_or_busy_or_disabled(svc, fake_decision):
    fake_decision.start()
    with pytest.raises(ControlError, match="already running") as e:
        await svc.decision_start("normal")
    assert e.value.code == 409
    claim = svc.runner.claim()
    try:
        with pytest.raises(ControlError) as e:
            await svc.decision_stop()
        assert e.value.code == 409
    finally:
        svc.runner.release(claim)
    svc.settings.decision_enabled = False
    with pytest.raises(ControlError) as e:
        await svc.decision_start("normal")
    assert e.value.code == 404


async def test_stop_when_not_running(svc):
    with pytest.raises(ControlError, match="not running") as e:
        await svc.decision_stop()
    assert e.value.code == 409


async def test_drive_missing_refused(svc, fake_mlx):
    svc.settings.mlx_root = fake_mlx.root / "gone"
    with pytest.raises(ControlError, match="not mounted"):
        await svc.decision_start("normal")


async def test_failed_start_rolls_back_and_keeps_intent(svc, fake_mlx):
    (fake_mlx.state / "decision_never_ready").write_text("")
    svc.settings.decision_ready_timeout_s = 0.3
    job = await svc.decision_start("normal")
    done = _wait(svc, job["id"])
    assert done["status"] == "timeout" and done["rolled_back"] is True
    assert not svc.decision.running().running
    assert _dj_intent(svc) is None
    assert svc.recovery_blocked() is None  # a sidecar failure never suspends MLX recovery


async def test_budget_view(svc):
    _start_mlx(svc)
    b = await svc.decision_budget("normal")
    assert b.ok and b.lines[0].startswith("MLX ") and "(qwen)" in b.lines[0]
    # weights dir + 262144 tokens x 20 KiB
    assert b.mlx_gb == pytest.approx((_weights(svc) + 262144 * 20480) / 1024**3)


async def test_budget_uses_profile_cap(svc, fake_mlx):
    _start_mlx(svc)
    (fake_mlx.root / "etc/profiles").mkdir()
    (fake_mlx.root / "etc/profiles/qwen.json").write_text(
        json.dumps({"version": 1, "launch": {"max_kv_size": 1024}})
    )
    b = await svc.decision_budget("normal")
    assert b.mlx_gb == pytest.approx((_weights(svc) + 1024 * 20480) / 1024**3)
    (fake_mlx.root / "etc/profiles/qwen.json").write_text("not json")
    assert (await svc.decision_budget("normal")).mlx_gb == pytest.approx(
        (_weights(svc) + 262144 * 20480) / 1024**3
    )


# --- engine and lifecycle rules ----------------------------------------------------------


async def test_engine_to_comfyui_stops_the_sidecar(svc, fake_decision, fake_mlx):
    _start_mlx(svc)
    fake_decision.start()
    write_intent(svc.store, "openjev", "running")
    job = await svc.engine("comfyui")
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert not svc.decision.running().running and svc.comfy.running().running
    assert _dj_intent(svc) == "stopped"
    calls = _calls(fake_mlx)
    assert calls.index("openjev stop") < calls.index("comfyui start")


async def test_engine_switch_to_disallowed_model_needs_force(svc, fake_decision, fake_mlx):
    _start_mlx(svc)
    fake_decision.start()
    svc.settings.decision_allowed_mlx = "qwen"
    fake_mlx.set_list(f"* qwen {fake_mlx.root}/models/Qwen\n  big {fake_mlx.root}/models/Qwen\n")
    with pytest.raises(ControlError, match="decision sidecar") as e:
        await svc.engine("mlx:big")
    assert e.value.code == 409
    job = await svc.engine("mlx:big", force=True)
    _wait(svc, job["id"])
    assert "openjev stop" in _calls(fake_mlx)


async def test_lifecycle_switch_to_disallowed_model_refused(svc, fake_decision, fake_mlx):
    _start_mlx(svc)
    fake_decision.start()
    fake_mlx.set_list(f"* qwen {fake_mlx.root}/models/Qwen\n  big {fake_mlx.root}/models/Qwen\n")
    with pytest.raises(ControlError, match="decision sidecar") as e:
        await svc.lifecycle("switch", "big")
    assert e.value.code == 409
    job = await svc.lifecycle("switch", "qwen")  # an allowed model is fine
    _wait(svc, job["id"])


async def test_lifecycle_bare_start_checks_the_selected_model(svc, fake_decision, fake_mlx):
    fake_decision.start()
    svc.settings.decision_allowed_mlx = "other"
    with pytest.raises(ControlError, match="decision sidecar"):
        await svc.lifecycle("start", None)


async def test_engine_off_leaves_the_sidecar(svc, fake_decision):
    _start_mlx(svc)
    fake_decision.start()
    job = await svc.engine("off")
    assert _wait(svc, job["id"])["status"] == "succeeded"
    assert svc.decision.running().running


def test_interrupted_decision_job_does_not_suspend(svc):
    row = _running_row("j1")
    row.update(action="decision", name="openjev", from_name=None, to_name="running")
    svc.store.save_job(row)
    svc.startup()
    assert svc.store.get_job("j1")["status"] == "interrupted"
    assert svc.recovery_blocked() is None


async def test_without_a_decision_client(fake_comfy, fake_mlx):
    s = fake_comfy.settings
    mlx = MlxClient(s)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    runner = JobRunner(s, store, current_name=lambda: None)
    svc = ControlService(s, store, runner, mlx, ComfyClient(s, transport=fake_comfy.transport))
    with pytest.raises(ControlError) as e:
        await svc.decision_start("normal")
    assert e.value.code == 404
    assert svc.ops.sidecar_down("openjev") is True
    _cli(s.mlx_model_path, "start", "qwen")
    job = await svc.engine("comfyui")
    assert _wait(svc, job["id"])["status"] == "succeeded"


def test_ops_argv_comes_from_the_registry(svc):
    assert svc.ops.sidecar_argv("openjev", "stop")[-1] == "stop"
    assert svc.ops.sidecar_down("openjev") is True


async def test_budget_without_an_active_path_is_unknown(svc, fake_mlx):
    _start_mlx(svc)
    (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=qwen\n")
    b = await svc.decision_budget("normal")
    assert not b.ok and any("cannot estimate" in r for r in b.reasons)
