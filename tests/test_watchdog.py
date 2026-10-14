import asyncio
import os
import signal
import subprocess
import time
from dataclasses import replace

import httpx
import pytest
import respx

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.alerts import AlertStore
from local_llm_admin.control import ControlService
from local_llm_admin.diagnostics import ProbeRunner
from local_llm_admin.engines import ComfyClient
from local_llm_admin.ingestion import HostLogs, IngestStore
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient
from local_llm_admin.watchdog import (
    GENERATION_KEY,
    PENDING_KEY,
    Supervisor,
    WatchInput,
    decide,
)

GEN = {"pid": 100, "started": 1000.0}
NOW = 1_790_000_000.0


def _in(**kw):
    base = WatchInput(
        enabled=True,
        intent="mlx:qwen",
        engine="none",
        drive_ok=True,
        job_busy=False,
        lock_state="none",
        suspended=None,
        pending=None,
        last_generation=GEN,
        handled_generation=None,
        outage_for_s=31.0,
        in_startup_grace=False,
        evidence="complete",
        shutdown_seen=False,
        crashes=[],
        now_wall=NOW,
    )
    return replace(base, **kw)


@pytest.mark.parametrize(
    ("kw", "action", "why"),
    [
        ({}, "restart", "crashed"),
        ({"enabled": False}, "none", "disabled"),
        ({"pending": {"job_id": "x"}}, "none", "in progress"),
        ({"job_busy": True}, "none", "job"),
        ({"drive_ok": None}, "none", "drive"),
        ({"drive_ok": False}, "none", "drive"),
        ({"engine": "unknown"}, "none", "unknown"),
        ({"engine": "conflict"}, "none", "conflict"),
        ({"intent": None}, "none", "unknown"),
        ({"intent": "unknown"}, "none", "unknown"),
        ({"intent": "comfyui"}, "none", "only MLX"),
        ({"intent": "off"}, "none", "only MLX"),
        ({"engine": "mlx"}, "none", "running"),
        ({"engine": "comfyui"}, "none", "running"),
        ({"outage_for_s": 29.0}, "wait", "grace"),
        ({"outage_for_s": None}, "wait", "grace"),
        ({"in_startup_grace": True}, "wait", "grace"),
        ({"suspended": "x"}, "none", "suspended"),
        ({"last_generation": None}, "suspend", "no MLX process"),
        ({"handled_generation": GEN}, "none", "already handled"),
        ({"lock_state": "live"}, "none", "mlx.lock"),
        ({"lock_state": "stale"}, "none", "mlx.lock"),
        ({"lock_state": None}, "none", "not observed"),
        ({"evidence": "behind"}, "wait", "catching up"),
        ({"shutdown_seen": True}, "mark_off", "gracefully"),
        ({"evidence": "gap"}, "suspend", "evidence"),
        ({"evidence": "unavailable"}, "suspend", "evidence"),
        ({"crashes": [NOW - 60]}, "suspend", "second"),
        ({"crashes": [NOW - 1801]}, "restart", "crashed"),
        ({"crashes": [NOW + 600]}, "suspend", "second"),  # clock went back
    ],
)
def test_decide(kw, action, why):
    d = decide(_in(**kw))
    assert d.action == action and why in d.reason
    assert d.record_crash == (action in ("restart",) or "second" in d.reason)


def test_graceful_stop_wins_over_missing_evidence_only_when_seen():
    assert decide(_in(shutdown_seen=True, evidence="gap")).action == "mark_off"


# --- supervisor integration --------------------------------------------------------


HEALTHY = {"status": "healthy", "loaded_model": "/Volumes/Models/models/active"}


class Clock:
    def __init__(self):
        self.mono = 10_000.0
        self.wall = time.time()

    def advance(self, s):
        self.mono += s
        self.wall += s


class FakeSampler:
    def current(self):
        return {"pressure": "normal"}


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


@pytest.fixture
def health():
    with respx.mock(assert_all_called=False) as r:
        route = r.route(host="mlx.test", path="/health")
        route.mock(return_value=httpx.Response(200, json=HEALTHY))
        yield route


@pytest.fixture
def sup(fake_comfy, health):
    s = fake_comfy.settings
    s.probe_timeout_s = 5
    path = s.admin_data_dir / "admin.sqlite"
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    store = JobStore(path)
    runner = JobRunner(s, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME"))
    control = ControlService(s, store, runner, mlx, comfy)
    events = IngestStore(path)
    clock = Clock()
    probes = ProbeRunner()
    sv = Supervisor(
        s,
        control,
        AlertStore(path),
        events,
        HostLogs(s, events, now=lambda: clock.wall),
        probes,
        FakeSampler(),
        mlx,
        clock=lambda: clock.wall,
        mono=lambda: clock.mono,
        connect=lambda addr, timeout: _Conn(),
    )
    sv.clock_ctl = clock
    sv._startup_until = 0.0
    yield sv
    probes.shutdown()


class _Conn:
    def close(self):
        pass


def _start_mlx(sv):
    subprocess.run([str(sv.settings.mlx_model_path), "start", "qwen"], check=False)
    return int((sv.settings.run_dir / "mlx-server.pid").read_text())


def _crash(pid):
    os.kill(pid, signal.SIGKILL)
    for _ in range(100):
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if open(f"/proc/{pid}/stat").read().split()[2] == "Z":
            return
        time.sleep(0.02)


def _graceful(sv, pid):
    with (sv.settings.mlx_log_dir / "mlx-server.log").open("a") as fh:
        fh.write(f"INFO:     Finished server process [{pid}]\n")
    _crash(pid)


def _keys(sv, state="all"):
    return [a["key"] for a in sv.alerts.list(state, limit=100)]


async def _wait_job(sv):
    for _ in range(500):
        if sv.control.runner.running() is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job stuck")


async def _down_past_grace(sv):
    await sv.tick()  # outage first seen
    sv.clock_ctl.advance(31)
    await sv.tick()


async def _run_until_running(sv):
    await sv.tick()
    assert sv.store.get_kv(GENERATION_KEY) is not None


async def test_first_crash_restarts_once_and_reports_after_health(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    pending = sv.store.get_kv(PENDING_KEY)
    assert pending and sv.last_decision.action == "restart"
    job = sv.control.runner.get(pending["job_id"])
    assert job["source"] == "watchdog" and job["action"] == "start"
    await sv.tick()  # restart pending: a no-op, not a second submission
    await _wait_job(sv)
    await sv.tick()
    assert sv.store.get_kv(PENDING_KEY) is None
    assert "engine_restarted" in _keys(sv)
    starts = [c for c in _calls(sv) if c.startswith("start")]
    assert len(starts) == 2  # the initial start plus exactly one recovery


def _calls(sv):
    p = sv.settings.mlx_root.parent / "fake_state/calls.log"
    return p.read_text().splitlines()


@pytest.fixture
async def sv_running(sup):
    sup.control.store.set_kv("intent", {"value": "mlx:qwen"})
    pid = _start_mlx(sup)
    await _run_until_running(sup)
    return sup, pid


async def test_repeated_polls_of_one_outage_count_once(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    await _wait_job(sv)
    new_pid = int((sv.settings.run_dir / "mlx-server.pid").read_text())
    await sv.tick()  # observes the new generation
    assert sv.store.get_kv("watchdog_crashes") and len(sv.store.get_kv("watchdog_crashes")) == 1
    _crash(new_pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "suspend" and "second" in sv.last_decision.reason
    for _ in range(3):
        sv.clock_ctl.advance(15)
        await sv.tick()
    assert len(sv.store.get_kv("watchdog_crashes")) == 2
    assert "watchdog_suspended" in _keys(sv, "open")
    starts = [c for c in _calls(sv) if c.startswith("start")]
    assert len(starts) == 2


async def test_crash_after_the_window_restarts_again(sv_running):
    sv, pid = sv_running
    sv.store.set_kv("watchdog_crashes", [sv.clock_ctl.wall - 1900])
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "restart"


async def test_failed_restart_suspends(sv_running, fake_mlx):
    sv, pid = sv_running
    (fake_mlx.state / "start_fails").write_text("")
    _crash(pid)
    await _down_past_grace(sv)
    await _wait_job(sv)
    await sv.tick()
    assert "watchdog restart failed" in sv.control.recovery_blocked()
    assert "engine_restart_failed" in _keys(sv)
    sv.clock_ctl.advance(60)
    await sv.tick()
    assert len([c for c in _calls(sv) if c.startswith("start")]) == 2


async def test_graceful_stop_outside_admin_sets_off(sv_running):
    sv, pid = sv_running
    await sv.tick()  # ingest the log up to now
    _graceful(sv, pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "mark_off"
    assert sv.control.intent()["value"] == "off"
    assert "stopped_outside_admin" in _keys(sv)
    assert "engine_down" not in _keys(sv)  # the stop was intentional: no critical alert
    assert len([c for c in _calls(sv) if c.startswith("start")]) == 1


async def test_crash_still_raises_engine_down(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    assert "engine_down" in _keys(sv, "open")


async def test_last_decision_reflects_a_running_mlx(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    await _wait_job(sv)
    await sv.tick()  # observes the restarted generation
    assert sv.last_decision.action == "none"
    assert sv.last_decision.reason == "MLX is running"


async def test_old_shutdown_marker_for_another_generation_is_ignored(sv_running):
    sv, pid = sv_running
    with (sv.settings.mlx_log_dir / "mlx-server.log").open("a") as fh:
        fh.write("INFO:     Finished server process [1]\n")
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "restart"


async def test_missing_log_suspends_instead_of_restarting(sv_running):
    sv, pid = sv_running
    (sv.settings.mlx_log_dir / "mlx-server.log").unlink()
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "suspend" and "unavailable" in sv.last_decision.reason
    assert len([c for c in _calls(sv) if c.startswith("start")]) == 1


async def test_intentional_stop_through_admin_stays_off(sv_running):
    sv, _pid = sv_running
    job = await sv.control.lifecycle("stop", None)
    await _wait_job(sv)
    assert sv.control.runner.get(job["id"])["status"] == "succeeded"
    await _down_past_grace(sv)
    assert sv.last_decision.reason == "only MLX is restarted automatically"
    assert "engine_down" not in _keys(sv, "open")


async def test_manual_action_wins_the_race(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await sv.tick()
    sv.clock_ctl.advance(31)
    token = sv.control.runner.claim()  # a manual action holds the slot
    try:
        sv.control.runner.running = lambda: None  # not yet visible as a running job
        await sv.tick()
    finally:
        del sv.control.runner.running
        sv.control.runner.release(token)
    assert sv.store.get_kv(PENDING_KEY) is None
    assert sv.store.get_kv("watchdog_crashes") is None  # nothing counted
    await sv.tick()
    assert sv.store.get_kv(PENDING_KEY) is not None  # decided again once free


async def test_restart_during_recovery_is_reconciled_as_interrupted(sv_running):
    """The admin restarts while the watchdog's start job runs: the row stays "running"."""
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    await _wait_job(sv)
    lost = dict(
        sv.store.get_job(sv.store.get_kv(PENDING_KEY)["job_id"]), id="lost", status="running"
    )
    sv.store.save_job(lost)
    sv.store.set_kv(PENDING_KEY, {"job_id": "lost", "generation": None})
    sv.control.reconcile()  # what the next admin process does first
    await sv.tick()
    assert sv.store.get_job("lost")["status"] == "interrupted"
    assert sv.store.get_kv(PENDING_KEY) is None
    assert sv.control.recovery_blocked() is not None
    assert "engine_restart_failed" in _keys(sv)


async def test_resume_clears_budget_but_blockers_still_apply(sv_running):
    sv, pid = sv_running
    sv.control.suspend("second crash")
    sv.store.set_kv("watchdog_crashes", [sv.clock_ctl.wall])
    sv.control.resume()
    assert sv.control.recovery_blocked() is None and sv.store.get_kv("watchdog_crashes") is None
    (sv.settings.run_dir / "mlx.lock").mkdir()
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "none" and "mlx.lock" in sv.last_decision.reason


async def test_comfyui_is_alert_only(sup, fake_comfy):
    sup.control.store.set_kv("intent", {"value": "comfyui"})
    await _down_past_grace(sup)
    assert sup.last_decision.reason == "only MLX is restarted automatically"
    assert "engine_down" in _keys(sup, "open")
    assert not [c for c in fake_comfy.calls() if c == "comfyui start"]


async def test_startup_grace_delays_decisions(sv_running):
    sv, pid = sv_running
    sv._startup_until = sv.clock_ctl.mono + 100
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "wait"


async def test_unhealthy_alive_process_is_not_restarted(sv_running, health):
    sv, _pid = sv_running
    health.mock(return_value=httpx.Response(503))
    for _ in range(3):
        sv.clock_ctl.advance(200)
        await sv.tick()
    assert "engine_unhealthy" in _keys(sv, "open")
    assert len([c for c in _calls(sv) if c.startswith("start")]) == 1


async def test_download_runs_become_events_once(sup):
    log = sup.settings.mlx_log_dir / "download-models.log"
    log.write_text("")
    await sup.tick()
    with log.open("a") as fh:
        fh.write(
            "===== 2026-09-28 22:15:01 start =====\n===== 2026-09-28 23:10:00 done fail=0 =====\n"
        )
        fh.write(
            "===== 2026-09-29 22:15:01 start =====\n===== 2026-09-29 23:10:00 done fail=2 =====\n"
        )
    await sup.tick()
    await sup.tick()
    keys = _keys(sup)
    assert keys.count("download_finished") == 1 and keys.count("download_failed") == 1


async def test_disabled_watchdog_only_alerts(sv_running):
    sv, pid = sv_running
    sv.settings.watchdog_enabled = False
    _crash(pid)
    await _down_past_grace(sv)
    assert sv.last_decision.action == "none"
    assert "engine_down" in _keys(sv, "open")
    assert sv.state()["enabled"] is False


async def test_restart_pending_while_running_then_unconfirmed_health(sv_running, fake_mlx, health):
    sv, pid = sv_running
    fake_mlx.set_delay(0.5)
    _crash(pid)
    await _down_past_grace(sv)
    await sv.tick()  # the start job is still running
    assert sv.store.get_kv(PENDING_KEY) is not None
    await _wait_job(sv)
    health.mock(return_value=httpx.Response(503))
    await sv.tick()
    assert sv.store.get_kv(PENDING_KEY) is not None  # succeeded, but not yet serving
    assert "engine_restarted" not in _keys(sv)
    health.mock(return_value=httpx.Response(200, json=HEALTHY))
    await sv.tick()
    assert sv.store.get_kv(PENDING_KEY) is None and "engine_restarted" in _keys(sv)


async def test_restart_that_dies_before_confirmation_clears_pending(sv_running):
    sv, pid = sv_running
    _crash(pid)
    await _down_past_grace(sv)
    await _wait_job(sv)
    _crash(int((sv.settings.run_dir / "mlx-server.pid").read_text()))
    await sv.tick()
    assert sv.store.get_kv(PENDING_KEY) is None and "engine_restarted" not in _keys(sv)


async def test_probe_errors_make_state_unknown(sup, monkeypatch):
    def boom():
        raise OSError("hung volume")

    monkeypatch.setattr(sup.control, "observe", boom)
    sup.control.store.set_kv("intent", {"value": "mlx:qwen"})
    await sup.tick()
    assert sup.last_decision.reason == "engine state is unknown"


async def test_evidence_states(sup):
    from local_llm_admin.ingestion import PollResult

    gen = {"pid": 1, "started": 100.0}
    assert sup._evidence(None, gen) == "unavailable"
    assert sup._evidence({"mlx-server": PollResult(ok=False)}, gen) == "unavailable"
    assert sup._evidence({"mlx-server": PollResult(ok=True, caught_up=False)}, gen) == "behind"
    done = {"mlx-server": PollResult(ok=True, caught_up=True)}
    assert sup._evidence(done, gen) == "complete"  # no cursor yet
    sup.logs.poll_all()
    assert sup._evidence(done, gen) == "complete"
    sup.events.set_cursor_offset("mlx-server", 0)
    with sup.events._conn() as c:
        c.execute("UPDATE log_cursors SET gap_at = 200")
    assert sup._evidence(done, gen) == "gap"
    assert sup._evidence(done, {"pid": 1, "started": 300.0}) == "complete"


async def test_background_loop_starts_ticks_and_stops(sup):
    sup.settings.monitor_interval_s = 0.01
    ticks = []

    async def fake_tick():
        ticks.append(1)
        if len(ticks) == 2:
            raise RuntimeError("one bad tick")

    sup.tick = fake_tick
    sup.start()
    sup.start()  # idempotent
    for _ in range(100):
        if len(ticks) >= 3:
            break
        await asyncio.sleep(0.01)
    await sup.stop()
    await sup.stop()
    assert len(ticks) >= 3  # kept going after the failure


async def test_tick_listeners_get_the_final_snapshot_and_errors_are_isolated(sup):
    seen: list = []

    def boom(snap, observed):
        raise RuntimeError("listener failed")

    async def later(snap, observed):
        seen.append((snap.engine, snap.intent, observed))

    sup.add_listener(boom)
    sup.add_listener(later)
    sup.control.store.set_kv("intent", {"value": "off"})
    await sup.tick()
    await sup.tick()
    assert len(seen) == 2
    engine, intent, observed = seen[0]
    assert engine == "none" and intent == "off" and observed["engine"] == "none"


async def test_listener_sees_no_observation_when_the_drive_is_unknown(sup, monkeypatch):
    seen: list = []
    monkeypatch.setattr(sup.mlx, "drive_mounted", lambda: None)
    sup.add_listener(lambda snap, observed: seen.append(observed))
    await sup.tick()
    assert seen == [None]
