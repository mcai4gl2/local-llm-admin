import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from local_llm_admin import jobs as jobs_mod
from local_llm_admin.jobs import JobConflict, JobRunner, JobStore, Step

HK = timezone(timedelta(hours=8))


def _ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=HK).timestamp()


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "admin.sqlite")


def _wait(runner, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = runner.get(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def runner(fake_mlx, store):
    return JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")


def test_switch_job_succeeds_and_records_history(runner, fake_mlx, store):
    job = runner.submit("switch", "gemma")
    assert job["status"] == "running"
    done = _wait(runner, job["id"])
    assert done["status"] == "succeeded" and done["returncode"] == 0
    assert "mlx-model: switch gemma" in done["output"]
    assert fake_mlx.calls() == ["switch gemma"]
    (row,) = store.history()
    assert (row["action"], row["from_name"], row["to_name"], row["result"]) == (
        "switch",
        "qwen",
        "gemma",
        "succeeded",
    )
    assert row["duration_s"] >= 0 and "done" in row["output_tail"]
    assert row["source"] == "admin"


def test_failed_job(runner, fake_mlx, store):
    fake_mlx.set_exit(3)
    done = _wait(runner, runner.submit("stop", None)["id"])
    assert done["status"] == "failed" and done["returncode"] == 3
    assert store.history()[0]["to_name"] is None


def test_start_without_name_records_current_selection(runner, store):
    _wait(runner, runner.submit("start", None)["id"])
    assert store.history()[0]["to_name"] == "qwen"


def test_single_flight(runner, fake_mlx):
    fake_mlx.set_delay(0.5)
    first = runner.submit("switch", "gemma")
    assert runner.running()["id"] == first["id"]
    with pytest.raises(JobConflict):
        runner.submit("stop", None)
    _wait(runner, first["id"])
    assert runner.running() is None
    _wait(runner, runner.submit("stop", None)["id"])


def test_timeout_kills_cli_only(fake_mlx, store):
    fake_mlx.settings.admin_job_timeout_s = 0.3
    fake_mlx.set_delay(5)
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    t0 = time.monotonic()
    done = _wait(r, r.submit("switch", "gemma")["id"])
    assert done["status"] == "timeout"
    assert time.monotonic() - t0 < 3


def test_background_child_holding_stdout_does_not_block_completion(runner, fake_mlx):
    fake_mlx.set_exit(0)
    (fake_mlx.state / "spawn").write_text("3")
    t0 = time.monotonic()
    done = _wait(runner, runner.submit("start", "qwen")["id"])
    assert done["status"] == "succeeded"
    assert time.monotonic() - t0 < 2


def test_bad_inputs_rejected_before_running(runner, fake_mlx):
    with pytest.raises(ValueError):
        runner.submit("switch", "x;rm -rf /")
    with pytest.raises(ValueError, match="unsupported action"):
        runner.submit("list", None)
    with pytest.raises(ValueError, match="directory is required"):
        runner.submit("add", "x")
    assert fake_mlx.calls() == []
    assert runner.running() is None


def test_missing_binary_marks_job_failed(fake_mlx, store):
    fake_mlx.settings.mlx_model_bin = fake_mlx.root / "bin/nope"
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: None)
    done = _wait(r, r.submit("stop", None)["id"])
    assert done["status"] == "failed" and done["returncode"] is None
    assert "nope" in done["output"]


def test_get_unknown_job(runner):
    assert runner.get("nope") is None


def test_jobs_persist_to_store(runner, store):
    job_id = _wait(runner, runner.submit("stop", None)["id"])["id"]
    assert store.get_job(job_id)["status"] == "succeeded"
    assert store.get_job("missing") is None


def test_backfill_is_idempotent_and_uses_ops_timezone(store):
    markers = [
        ("2026-09-20 09:00:01", "Qwen3.8-27B-4bit"),
        ("2026-09-25 12:00:00", "qwen3.8-27b"),
        ("garbage ts", "x"),
    ]
    assert store.backfill(markers, tz_offset_min=-480) == 2
    assert store.backfill(markers, tz_offset_min=-480) == 0
    rows = store.history()
    assert [r["to_name"] for r in rows] == ["qwen3.8-27b", "Qwen3.8-27B-4bit"]  # newest first
    assert rows[1]["ts"] == _ts("2026-09-20 09:00:01")
    assert rows[0]["source"] == "log"


def test_active_by_day(store):
    store.backfill([("2026-09-20 09:00:01", "A"), ("2026-09-25 12:00:00", "B")], tz_offset_min=-480)
    store.add_history(
        ts=_ts("2026-09-25 23:30:00"),
        action="switch",
        from_name="B",
        to_name="C",
        result="succeeded",
        duration_s=40,
        output_tail="",
        source="admin",
    )
    store.add_history(
        ts=_ts("2026-09-26 08:00:00"),
        action="switch",
        from_name="C",
        to_name="D",
        result="failed",
        duration_s=40,
        output_tail="",
        source="admin",
    )
    got = store.active_by_day(
        ["2026-09-19", "2026-09-20", "2026-09-24", "2026-09-25", "2026-09-26"], tz_offset_min=-480
    )
    assert got == {
        "2026-09-19": [],
        "2026-09-20": ["A"],
        "2026-09-24": ["A"],
        "2026-09-25": ["A", "B", "C"],
        "2026-09-26": ["C"],
    }


def test_history_limit(store):
    for i in range(5):
        store.add_history(
            ts=float(i),
            action="stop",
            from_name=None,
            to_name=None,
            result="succeeded",
            duration_s=0,
            output_tail="",
            source="admin",
        )
    assert len(store.history(limit=2)) == 2


def test_timeout_sends_sigterm_first(fake_mlx, store):
    fake_mlx.settings.admin_job_timeout_s = 0.3
    fake_mlx.set_delay(5)
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    assert _wait(r, r.submit("stop", None)["id"])["status"] == "timeout"
    assert (fake_mlx.state / "terminated").read_text() == "term\n"


def test_slot_released_even_if_recording_fails(fake_mlx, store, monkeypatch):
    def broken(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_history", broken)
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    done = _wait(r, r.submit("stop", None)["id"])
    assert done["status"] == "failed"
    assert r.running() is None
    _wait(r, r.submit("stop", None)["id"])  # next job is accepted


def test_timeout_kills_when_term_is_ignored(fake_mlx, store, monkeypatch):
    import local_llm_admin.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "_TERM_GRACE_S", 0.2)
    fake_mlx.settings.admin_job_timeout_s = 0.3
    fake_mlx.set_delay(5)
    (fake_mlx.state / "ignore_term").write_text("")
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    t0 = time.monotonic()
    assert _wait(r, r.submit("stop", None)["id"])["status"] == "timeout"
    assert time.monotonic() - t0 < 3


def test_add_job_passes_name_and_path(runner, fake_mlx, store):
    done = _wait(runner, runner.submit("add", "new-model", path="/Volumes/Models/models/New")["id"])
    assert done["status"] == "succeeded"
    assert fake_mlx.calls() == ["add new-model /Volumes/Models/models/New"]
    row = store.history()[0]
    assert (row["action"], row["to_name"]) == ("add", "new-model")


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


def _seq(runner, steps, rollback=None, rollback_after=0):
    return runner.submit_sequence(
        "engine",
        name=None,
        from_name="qwen",
        to_name="comfyui",
        steps=steps,
        rollback=rollback,
        rollback_after=rollback_after,
    )


def test_sequence_runs_steps_in_order_and_logs_them(runner, fake_mlx, store, fast_poll):
    seen = []
    steps = [
        Step("stop mlx", argv=[str(fake_mlx.settings.mlx_model_path), "stop"]),
        Step("wait", wait=lambda: True, timeout_s=1),
        Step("call", call=lambda: seen.append("called")),
    ]
    done = _wait(runner, _seq(runner, steps)["id"])
    assert done["status"] == "succeeded" and done["steps"] == ["stop mlx", "wait", "call"]
    assert done["rolled_back"] is None and seen == ["called"]
    assert "==> stop mlx" in done["output"] and "<== call: succeeded" in done["output"]
    row = store.history()[0]
    assert (row["action"], row["from_name"], row["to_name"], row["result"]) == (
        "engine",
        "qwen",
        "comfyui",
        "succeeded",
    )


def test_wait_step_times_out(runner, fast_poll):
    done = _wait(runner, _seq(runner, [Step("never", wait=lambda: False, timeout_s=0.1)])["id"])
    assert done["status"] == "timeout"


def test_wait_step_exception_fails_step(runner, fast_poll):
    def boom():
        raise OSError("port check failed")

    done = _wait(runner, _seq(runner, [Step("w", wait=boom, timeout_s=1)])["id"])
    assert done["status"] == "failed" and "port check failed" in done["output"]
    assert runner.running() is None


def test_call_step_exception_fails_step(runner):
    def boom():
        raise RuntimeError("interrupt refused")

    done = _wait(runner, _seq(runner, [Step("c", call=boom)])["id"])
    assert done["status"] == "failed" and "interrupt refused" in done["output"]


def test_rollback_runs_after_failure_past_the_source_stop(runner, fake_mlx, fast_poll):
    ran = []
    steps = [
        Step("stop source", call=lambda: ran.append("stop")),
        Step("start target", call=lambda: (_ for _ in ()).throw(RuntimeError("no"))),
    ]
    rollback = [
        Step(
            "cleanup target", call=lambda: (_ for _ in ()).throw(RuntimeError("x")), optional=True
        ),
        Step("start source", call=lambda: ran.append("restore")),
    ]
    done = _wait(runner, _seq(runner, steps, rollback, rollback_after=1)["id"])
    assert done["status"] == "failed" and done["rolled_back"] is True
    assert ran == ["stop", "restore"]
    assert "rollback" in done["output"] and "(optional, continuing)" in done["output"]


def test_rollback_only_after_source_stopped(runner):
    ran = []
    steps = [Step("stop source", call=lambda: (_ for _ in ()).throw(RuntimeError("lock")))]
    rollback = [Step("start source", call=lambda: ran.append("restore"))]
    done = _wait(runner, _seq(runner, steps, rollback, rollback_after=1)["id"])
    assert done["status"] == "failed" and done["rolled_back"] is None and ran == []


def test_failed_rollback_is_reported(runner):
    fail = lambda: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
    done = _wait(
        runner,
        _seq(
            runner,
            [Step("s", call=lambda: None), Step("t", call=fail)],
            [Step("restore", call=fail)],
            rollback_after=1,
        )["id"],
    )
    assert done["rolled_back"] is False


def test_sequence_is_single_flight(runner, fast_poll):
    first = _seq(runner, [Step("slow", wait=lambda: False, timeout_s=0.3)])
    with pytest.raises(JobConflict):
        _seq(runner, [Step("x", call=lambda: None)])
    _wait(runner, first["id"])


def test_engine_history_counts_for_active_by_day(store):
    t = _ts("2026-09-29 12:00:00")
    store.add_history(
        ts=t,
        action="engine",
        from_name="qwen",
        to_name="comfyui",
        result="succeeded",
        duration_s=5,
        output_tail="",
        source="admin",
    )
    assert store.active_by_day(["2026-09-29"], tz_offset_min=-480) == {"2026-09-29": ["comfyui"]}


def test_submit_sequence_rejects_unknown_action_and_submit_rejects_engine(runner):
    with pytest.raises(ValueError, match="unsupported action"):
        runner.submit_sequence("list", name=None, from_name=None, to_name=None, steps=[])
    with pytest.raises(ValueError, match="unsupported action"):
        runner.submit("engine", None)
    assert runner.running() is None


# --- durable reservation, source, outcome, hooks (M1 §1) ---------------------


def test_reservation_is_persisted_before_work_starts(runner, fake_mlx, store):
    fake_mlx.set_delay(0.5)
    job = runner.submit("switch", "gemma", source="watchdog")
    row = store.get_job(job["id"])
    assert row["status"] == "running" and row["source"] == "watchdog"
    assert row["to_name"] == "gemma"
    _wait(runner, job["id"])


def test_source_and_rollback_outcome_are_persisted(runner, store, fast_poll):
    fail = lambda: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
    job = runner.submit_sequence(
        "engine",
        name=None,
        from_name="qwen",
        to_name="comfyui",
        steps=[Step("s", call=lambda: None), Step("t", call=fail)],
        rollback=[Step("restore", call=lambda: None)],
        rollback_after=1,
        source="watchdog",
    )
    _wait(runner, job["id"])
    row = store.get_job(job["id"])
    assert (row["status"], row["rolled_back"], row["source"]) == ("failed", 1, "watchdog")
    assert store.history()[0]["source"] == "watchdog"


def test_unknown_source_rejected(runner):
    with pytest.raises(ValueError, match="source"):
        runner.submit("stop", None, source="cron")
    assert runner.running() is None


def test_reservation_storage_failure_runs_nothing(runner, fake_mlx, store, monkeypatch):
    def broken(job):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "save_job", broken)
    with pytest.raises(jobs_mod.JobStorageError):
        runner.submit("stop", None)
    assert fake_mlx.calls() == [] and runner.running() is None
    monkeypatch.undo()
    _wait(runner, runner.submit("stop", None)["id"])  # the slot was released


def test_observed_engine_recorded_at_completion(fake_mlx, store):
    seen = {"engine": "none", "generation": None}
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen", observe=lambda: seen)
    job_id = _wait(r, r.submit("stop", None)["id"])["id"]
    assert json.loads(store.get_job(job_id)["observed_engine"]) == seen


def test_observe_failure_does_not_fail_the_job(fake_mlx, store):
    def boom():
        raise OSError("drive gone")

    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen", observe=boom)
    done = _wait(r, r.submit("stop", None)["id"])
    assert done["status"] == "succeeded" and store.get_job(done["id"])["observed_engine"] is None


def test_hooks_run_before_slot_release_and_errors_are_isolated(runner):
    calls = []

    def bad(job):
        raise RuntimeError("hook bug")

    def good(job):
        calls.append((job["status"], job["recorded"], runner.running() is not None))

    runner.add_hook(bad)
    runner.add_hook(good)
    done = _wait(runner, runner.submit("stop", None)["id"])
    assert done["status"] == "succeeded"
    assert calls == [("succeeded", True, True)]
    assert runner.running() is None


def test_hooks_see_unrecorded_completion(fake_mlx, store, monkeypatch):
    seen = []
    monkeypatch.setattr(store, "add_history", lambda **kw: (_ for _ in ()).throw(OSError("x")))
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    r.add_hook(seen.append)
    _wait(r, r.submit("stop", None)["id"])
    assert seen[0]["recorded"] is False and seen[0]["action"] == "stop"


def test_claim_blocks_other_submissions_until_released(runner):
    token = runner.claim()
    with pytest.raises(JobConflict):
        runner.claim()
    with pytest.raises(JobConflict):
        runner.submit("stop", None)
    runner.release(token)
    runner.release(token)  # idempotent
    _wait(runner, runner.submit("stop", None)["id"])


def test_claimed_submission_uses_the_claim(runner):
    token = runner.claim()
    job = runner.submit("stop", None, claim=token)
    _wait(runner, job["id"])
    with pytest.raises(JobConflict):  # a spent claim cannot launch a second job
        runner.submit("stop", None, claim=token)
    _wait(runner, runner.submit("stop", None)["id"])


def test_restart_marks_running_jobs_interrupted_without_replay(fake_mlx, store):
    store.save_job(
        {
            "id": "abc",
            "action": "switch",
            "name": "gemma",
            "from_name": "qwen",
            "to_name": "gemma",
            "source": "admin",
            "status": "running",
            "returncode": None,
            "started": 5.0,
            "finished": None,
            "rolled_back": None,
            "observed_engine": None,
            "output": "",
        }
    )
    r = JobRunner(fake_mlx.settings, store, current_name=lambda: "qwen")
    interrupted = r.reconcile_interrupted()
    assert [j["id"] for j in interrupted] == ["abc"]
    assert store.get_job("abc")["status"] == "interrupted"
    assert store.get_job("abc")["finished"] is not None
    (row,) = store.history()
    assert (row["action"], row["to_name"], row["result"]) == ("switch", "gemma", "interrupted")
    assert r.reconcile_interrupted() == []  # idempotent
    assert fake_mlx.calls() == [] and r.running() is None


# --- M2: a held job driven step by step (quality runs) ------------------------------


def test_held_job_owns_the_slot_until_finished(runner, fake_mlx, store):
    hooks = []
    runner.add_hook(lambda job: hooks.append((job["status"], job["recorded"], runner.running())))
    claim = runner.claim()
    job = runner.begin(
        "quality", name=None, from_name="qwen", to_name="qwen", source="benchmark", claim=claim
    )
    assert job["status"] == "running" and job["source"] == "benchmark"
    assert store.get_job(job["id"])["status"] == "running"  # persisted before any step
    with pytest.raises(JobConflict):
        runner.submit("stop", None)
    with pytest.raises(JobConflict):
        runner.claim()
    status, rc = runner.run_command(job["id"], "switch gemma", ["/bin/echo", "switched"], 5)
    assert (status, rc) == ("succeeded", 0)
    runner.note(job["id"], "case p128: complete")
    final = runner.finish(job["id"], "succeeded", rolled_back=True)
    assert final["status"] == "succeeded" and final["rolled_back"] is True
    assert runner.running() is None
    # the hook ran with the slot still held and the outcome recorded
    assert hooks == [("succeeded", True, hooks[0][2])] and hooks[0][2]["id"] == job["id"]
    row = store.get_job(job["id"])
    assert row["status"] == "succeeded" and row["rolled_back"] == 1
    assert "switched" in row["output"] and "case p128: complete" in row["output"]
    assert store.history()[0]["action"] == "quality"
    assert store.history()[0]["source"] == "benchmark"


def test_held_job_requires_its_claim(runner):
    with pytest.raises(JobConflict):
        runner.begin(
            "quality", name=None, from_name=None, to_name=None, source="benchmark", claim="x"
        )
    with pytest.raises(ValueError):
        runner.begin(
            "bogus", name=None, from_name=None, to_name=None, source="benchmark", claim=None
        )


def test_commands_only_run_for_the_held_job(runner):
    job = runner.begin(
        "quality", name=None, from_name=None, to_name=None, source="benchmark", claim=runner.claim()
    )
    with pytest.raises(ValueError):
        runner.run_command("other", "x", ["/bin/true"], 5)
    runner.finish(job["id"], "cancelled", rolled_back=None)
    with pytest.raises(ValueError):
        runner.run_command(job["id"], "x", ["/bin/true"], 5)
    with pytest.raises(ValueError):
        runner.finish(job["id"], "succeeded", rolled_back=None)


def test_held_command_timeout_is_enforced(runner):
    job = runner.begin(
        "quality", name=None, from_name=None, to_name=None, source="benchmark", claim=runner.claim()
    )
    t0 = time.monotonic()
    status, _rc = runner.run_command(job["id"], "sleep", ["/bin/sleep", "5"], 0.2)
    assert status == "timeout" and time.monotonic() - t0 < 4
    runner.finish(job["id"], "failed", rolled_back=False)


def test_held_job_storage_failure_still_releases_the_slot(runner, store, monkeypatch):
    seen = []
    runner.add_hook(lambda job: seen.append(job["recorded"]))
    job = runner.begin(
        "quality", name=None, from_name=None, to_name=None, source="benchmark", claim=runner.claim()
    )

    def broken(**kw):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "add_history", broken)
    final = runner.finish(job["id"], "succeeded", rolled_back=True)
    assert final["status"] == "failed" and seen == [False]
    assert runner.running() is None


def test_an_unexpected_error_while_running_steps_is_a_failed_job(runner, store, monkeypatch):
    def boom(job_id, steps):
        raise RuntimeError("bug")

    monkeypatch.setattr(runner, "_run_steps", boom)
    done = _wait(runner, runner.submit("stop", None)["id"])
    assert done["status"] == "failed" and runner.running() is None
    assert store.get_job(done["id"])["status"] == "failed"


def test_strata_engine_switch_labels_usage_days(store):
    for ts, frm, to in (("2026-10-15 09:00:00", "qwen", "strata"), ("2026-10-16 09:00:00", "strata", "qwen")):
        store.add_history(
            ts=_ts(ts),
            action="engine",
            from_name=frm,
            to_name=to,
            result="succeeded",
            duration_s=200,
            output_tail="",
            source="admin",
        )
    got = store.active_by_day(["2026-10-15", "2026-10-16", "2026-10-17"], tz_offset_min=-480)
    assert got == {"2026-10-15": ["strata"], "2026-10-16": ["strata", "qwen"], "2026-10-17": ["qwen"]}
