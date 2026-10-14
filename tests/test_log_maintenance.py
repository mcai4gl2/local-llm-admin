"""Admin side of log rotation (M4 §3): daily job, deferral visibility, safe retries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.alerts import AlertStore, Snapshot
from local_llm_admin.control import ControlService
from local_llm_admin.engines import ComfyClient
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.log_rotation import (
    ALERT_DEFERRED,
    ALERT_FAILED,
    ROTATION_KEY,
    LogMaintenance,
    build_rotate_argv,
    parse_rotate_output,
)
from local_llm_admin.mlx import MlxClient

HKT = timezone(timedelta(hours=8))
MORNING = datetime(2026, 10, 1, 5, 0, tzinfo=HKT).timestamp()  # after the 04:00 slot
MIB = 1024 * 1024

SNAP = Snapshot(
    wall=MORNING,
    mono=0.0,
    drive_mounted=True,
    access_error=None,
    access_known=True,
    engine="mlx",
    mlx_serving=True,
    intent="mlx:qwen",
    job_running=False,
    lock=None,
    pressure="normal",
    litellm_ok=None,
    suspended=None,
)


# --- pure helpers ------------------------------------------------------------------


def test_parse_rotate_output():
    out = parse_rotate_output(
        "rotated mlx-server.log mlx-server.log.20261001-040000 73400320\n"
        "deferred download-models.log active-writer 60000000\n"
        "skipped chat-qwen.log below-threshold 12\n"
        "missing serve-qwen.log\n"
        "pruned mlx-server.log.20260901-040000\n"
        "error download-x.log create-failed restored\n"
        "Another line from the shell\n"
        "rotated ../../etc/passwd x 1\n"
        "deferred mlx-server.log active-writer notanumber\n"
    )
    assert out == {
        "rotated": [
            {
                "name": "mlx-server.log",
                "archive": "mlx-server.log.20261001-040000",
                "bytes": 73400320,
            }
        ],
        "deferred": [{"name": "download-models.log", "reason": "active-writer", "bytes": 60000000}],
        "skipped": ["chat-qwen.log"],
        "missing": ["serve-qwen.log"],
        "pruned": ["mlx-server.log.20260901-040000"],
        "errors": [{"name": "download-x.log", "reason": "create-failed restored"}],
    }


def test_build_rotate_argv_is_fixed_and_bounded(tmp_path):
    bin_path = tmp_path / "mlx-model"
    assert build_rotate_argv(bin_path, 50, 8) == [
        str(bin_path),
        "rotate-logs",
        "--max-bytes",
        str(50 * MIB),
        "--keep",
        "8",
    ]
    for mib, keep in ((0, 8), (10241, 8), (50, 0), (50, 51)):
        with pytest.raises(ValueError):
            build_rotate_argv(bin_path, mib, keep)


# --- scheduler -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def maint(fake_comfy):
    s = fake_comfy.settings
    path = s.admin_data_dir / "admin.sqlite"
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    store = JobStore(path)
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    control = ControlService(s, store, runner, mlx, comfy)
    clock = Clock(MORNING)
    m = LogMaintenance(s, control, AlertStore(path), clock=clock)
    m.clock_ctl = clock
    m.fake = fake_comfy
    return m


async def _settle(m: LogMaintenance) -> None:
    for _ in range(500):
        if m.control.runner.running() is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


def _calls(m):
    return [c for c in m.fake.calls() if c.startswith("rotate-logs")]


async def _tick(m, **changes):
    await m.on_tick(replace(SNAP, wall=m.clock_ctl.t, **changes), None)
    await _settle(m)


async def test_runs_once_a_day_after_the_configured_hour(maint):
    maint.clock_ctl.t = MORNING - 2 * 3600  # 03:00, before the slot
    await _tick(maint)
    assert _calls(maint) == []
    maint.clock_ctl.t = MORNING
    await _tick(maint)
    await _tick(maint)
    assert _calls(maint) == [f"rotate-logs --max-bytes {50 * MIB} --keep 8"]
    st = maint.control.store.get_kv(ROTATION_KEY)
    assert st["day"] == "2026-10-01" and st["outcome"] == "succeeded"
    maint.clock_ctl.t = MORNING + 86400
    await _tick(maint)
    assert len(_calls(maint)) == 2


async def test_missed_days_are_coalesced_into_one_run(maint):
    maint.control.store.set_kv(ROTATION_KEY, {"day": "2026-09-27", "outcome": "succeeded"})
    await _tick(maint)
    await _tick(maint)
    assert len(_calls(maint)) == 1


async def test_skips_while_a_job_runs_or_the_drive_is_unknown(maint):
    await _tick(maint, job_running=True)
    await _tick(maint, drive_mounted=None)
    assert _calls(maint) == []
    assert maint.control.store.get_kv(ROTATION_KEY) is None


async def test_a_refused_submission_is_retried_later(maint):
    lock = maint.control.settings.run_dir / "mlx.lock"
    lock.mkdir()
    await _tick(maint)
    assert _calls(maint) == []
    st = maint.control.store.get_kv(ROTATION_KEY)
    assert st["day"] is None and "mlx.lock" in st["skipped"]
    lock.rmdir()
    await _tick(maint)
    assert len(_calls(maint)) == 1


async def test_deferred_active_log_raises_a_warning_and_resolves_later(maint):
    out = maint.fake.state / "rotate_output"
    out.write_text("deferred mlx-server.log active-writer 73400320\n")
    await _tick(maint)
    st = maint.control.store.get_kv(ROTATION_KEY)
    assert st["result"]["deferred"][0]["name"] == "mlx-server.log"
    open_keys = maint.alerts.open_condition_keys("log_rotation")
    assert open_keys == [ALERT_DEFERRED]
    (row,) = maint.alerts.list("open", 10)
    assert "mlx-server.log" in row["detail"] and "70.0 MiB" in row["detail"]
    # MLX stays up: no retry within the hour, nor while it serves
    maint.clock_ctl.t += 7200
    await _tick(maint)
    assert len(_calls(maint)) == 1
    # MLX stopped: the next tick retries (writer gone), and success resolves the alert
    out.write_text("rotated mlx-server.log mlx-server.log.20261001-070000 73400320\n")
    await _tick(maint, engine="none", intent="off")
    assert len(_calls(maint)) == 2
    assert maint.alerts.open_condition_keys("log_rotation") == []


async def test_retry_waits_an_hour_between_attempts(maint):
    (maint.fake.state / "rotate_output").write_text(
        "deferred mlx-server.log active-writer 73400320\n"
    )
    await _tick(maint, engine="none", intent="off")
    await _tick(maint, engine="none", intent="off")
    assert len(_calls(maint)) == 1
    maint.clock_ctl.t += 3601
    await _tick(maint, engine="none", intent="off")
    assert len(_calls(maint)) == 2


async def test_errors_raise_a_failure_alert(maint):
    (maint.fake.state / "rotate_output").write_text("error mlx-server.log rename-failed\n")
    (maint.fake.state / "rotate_exit").write_text("1")
    await _tick(maint)
    st = maint.control.store.get_kv(ROTATION_KEY)
    assert st["outcome"] == "failed"
    assert maint.alerts.open_condition_keys("log_rotation") == [ALERT_FAILED]
    (row,) = maint.alerts.list("open", 10)
    assert "rename-failed" in row["detail"]


async def test_rotation_never_changes_the_intended_engine(maint):
    maint.control.store.set_kv("intent", {"value": "mlx:qwen"})
    await _tick(maint)
    assert maint.control.intent()["value"] == "mlx:qwen"
    assert maint.control.recovery_blocked() is None
    job = maint.control.store.get_job(maint.control.store.get_kv(ROTATION_KEY)["job_id"])
    assert job["source"] == "maintenance" and job["action"] == "rotate-logs"


async def test_interrupted_rotation_does_not_suspend_recovery(maint):
    control = maint.control
    control.store.save_job(
        {
            "id": "rot1",
            "action": "rotate-logs",
            "name": None,
            "from_name": None,
            "to_name": None,
            "source": "maintenance",
            "status": "running",
            "returncode": None,
            "started": MORNING,
            "finished": None,
            "rolled_back": None,
            "observed_engine": None,
            "output": "",
        }
    )
    control.store.set_kv("intent", {"value": "mlx:qwen"})
    assert [j["id"] for j in control.reconcile()] == ["rot1"]
    assert control.intent()["value"] == "mlx:qwen"
    assert control.recovery_blocked() is None


def test_state_view_reports_settings_and_last_outcome(maint):
    view = maint.state()
    assert view["max_mib"] == 50 and view["keep"] == 8 and view["hour"] == 4
    assert view["last"] is None


async def test_a_running_rotation_is_not_submitted_again(maint):
    (maint.fake.state / "delay").write_text("0")
    job = await maint.control.rotate_logs(["/bin/sleep", "0.3"])
    maint.control.store.set_kv(
        ROTATION_KEY, {"day": None, "outcome": "running", "job_id": job["id"]}
    )
    await maint.on_tick(replace(SNAP, wall=maint.clock_ctl.t), None)
    await _settle(maint)
    assert _calls(maint) == []


async def test_a_run_left_running_by_an_admin_restart_does_not_block_rotation(maint):
    maint.control.store.set_kv(
        ROTATION_KEY, {"day": "2026-09-30", "outcome": "running", "job_id": "gone"}
    )
    await _tick(maint)
    assert len(_calls(maint)) == 1


async def test_other_jobs_leave_the_rotation_record_alone(maint):
    job = await maint.control.lifecycle("stop", None)
    await _settle(maint)
    assert maint.control.runner.get(job["id"])["status"] == "succeeded"
    assert maint.control.store.get_kv(ROTATION_KEY) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("log_rotate_max_mib", 0),
        ("log_rotate_max_mib", 10241),
        ("log_rotate_keep", 0),
        ("log_rotate_keep", 51),
        ("log_rotate_hour", 24),
        ("availability_interval_s", 5),
        ("observability_retention_days", 1),
        ("perf_slow_ttft_s", 0),
    ],
)
def test_m4_settings_are_bounded(field, value):
    from pydantic import ValidationError

    from local_llm_admin.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{field: value})
