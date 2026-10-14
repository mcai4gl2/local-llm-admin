"""M6 Phase 4: decision request records and availability samples (no request content)."""

import sqlite3
from types import SimpleNamespace

import pytest

from local_llm_admin.alerts import AlertStore
from local_llm_admin.decision_store import DecisionStore, request_summary
from local_llm_admin.jobs import JobStore
from local_llm_admin.sidecars import SidecarMonitor, SidecarRegistry, write_intent

DAY = 86400.0
T0 = 1_790_000_000.0  # 2026-09-21 14:13:20 UTC = 22:13:20 HKT


@pytest.fixture
def store(tmp_path):
    return DecisionStore(tmp_path / "admin.sqlite")


def test_tables_exist_after_migration(store, tmp_path):
    with sqlite3.connect(tmp_path / "admin.sqlite") as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"decision_requests", "decision_availability"} <= names


def test_request_summary_counts_and_types_only():
    body = {
        "state": "SECRET STATE",
        "questions": {
            "a": {"type": "noul", "instructions": "SECRET Q"},
            "b": {"type": "choice", "criteria": {"x": "y", "z": "w"}},
            "c": {"type": "choice", "criteria": {"x": "y", "z": "w"}},
            "d": {"type": "weird"},
        },
        "steps": 2,
        "samples": 3,
    }
    s = request_summary(body)
    assert s == {"n_questions": 4, "types": "choice,noul", "steps": 2, "samples": 3}
    assert "SECRET" not in repr(s)


@pytest.mark.parametrize(
    "body", [None, "text", {"questions": "x"}, {"questions": {"a": 1}}, {"steps": "2"}]
)
def test_request_summary_of_malformed_bodies(body):
    s = request_summary(body)
    assert set(s) == {"n_questions", "types", "steps", "samples"} and s["steps"] is None


def _add(store, ts, latency, status="ok", code=None, tokens=200, source="admin-ui"):
    store.add_request(
        ts=ts,
        source=source,
        summary={"n_questions": 3, "types": "noul", "steps": None, "samples": None},
        input_tokens=tokens,
        latency_ms=latency,
        model_ms=None,
        status=status,
        error_code=code,
    )


def test_recent_newest_first_and_bounded(store):
    for i in range(5):
        _add(store, T0 + i, 100 + i)
    rows = store.recent(limit=3)
    assert [r["latency_ms"] for r in rows] == [104, 103, 102]
    assert set(rows[0]) >= {"ts", "source", "n_questions", "types", "status", "error_code"}


def test_daily_percentiles_errors_and_ops_timezone(store):
    # +2 h crosses midnight in HKT but not in UTC
    for i, lat in enumerate([100, 200, 300, 400, 1000]):
        _add(store, T0 + i, lat)
    _add(store, T0 + 10, 50, status="error", code=503, tokens=None)
    _add(store, T0 + 7200, 80)
    days = store.daily(T0 - DAY, T0 + 2 * DAY, tz_offset_min=-480)
    assert [d["day"] for d in days] == ["2026-09-21", "2026-09-22"]
    assert len(store.daily(T0 - DAY, T0 + 2 * DAY, tz_offset_min=0)) == 1
    d1 = days[0]
    assert d1["requests"] == 6 and d1["errors"] == 1 and d1["input_tokens"] == 1000
    assert d1["p50_ms"] == 300 and d1["p95_ms"] == 1000  # successful reads only
    assert days[1]["requests"] == 1 and days[1]["p50_ms"] == 80


def test_daily_with_only_errors_has_no_percentiles(store):
    _add(store, T0, 10, status="error", code=504)
    (d,) = store.daily(T0 - 10, T0 + 10, tz_offset_min=0)
    assert d["p50_ms"] is None and d["errors"] == 1


def test_availability_counts_only_intended_running(store):
    for i, st in enumerate(["ready", "ready", "ready", "loading", "down", "stopped", "unknown"]):
        store.add_sample(T0 + i * 60, st)
    a = store.availability(T0 - 1, T0 + 3600)
    assert a["counts"] == {"ready": 3, "loading": 1, "down": 1, "stopped": 1, "unknown": 1}
    assert a["ratio"] == pytest.approx(3 / 5)
    assert store.availability(T0 + 10_000, T0 + 20_000)["ratio"] is None


def test_prune_removes_old_rows(store):
    _add(store, T0, 1)
    _add(store, T0 + DAY, 2)
    store.add_sample(T0, "ready")
    store.add_sample(T0 + DAY, "ready")
    assert store.prune(T0 + 1) == 2
    assert len(store.recent()) == 1 and store.availability(0, T0 + 2 * DAY)["counts"]["ready"] == 1


# --- availability sampling in the monitor -------------------------------------------------


@pytest.fixture
def monitor(fake_decision):
    s = fake_decision.settings
    s.availability_interval_s = 60
    db = s.admin_data_dir / "admin.sqlite"
    jobs, store = JobStore(db), DecisionStore(db)
    mon = SidecarMonitor(
        s, SidecarRegistry(s, {"openjev": fake_decision.transport}), jobs, AlertStore(db), history=store
    )
    return mon, jobs, store


def _snap(wall, job_running=False):
    return SimpleNamespace(job_running=job_running, wall=wall)


def _states(store):
    with store._conn() as c:
        return [r[0] for r in c.execute("SELECT state FROM decision_availability ORDER BY id")]


async def test_monitor_samples_each_state_once_per_interval(monitor, fake_decision):
    mon, jobs, store = monitor
    await mon(_snap(T0), None)  # stopped, not intended
    await mon(_snap(T0 + 10), None)  # same interval: no new sample
    fake_decision.start()
    write_intent(jobs, "openjev", "running")
    await mon(_snap(T0 + 60), None)  # ready
    (fake_decision.state / "decision_ready").unlink()
    await mon(_snap(T0 + 120), None)  # running, health not answering
    import os
    import signal

    os.kill(int((fake_decision.root / "run/openjev.pid").read_text()), signal.SIGKILL)
    import time

    time.sleep(0.2)
    await mon(_snap(T0 + 180), None)  # intended running, gone
    await mon(_snap(T0 + 240, job_running=True), None)  # during a job: never "down"
    await mon(_snap(T0 + 100), None)  # clock went back: samples again
    # stopped, ready, health down, process gone, job (never "down"), clock back
    assert _states(store) == ["stopped", "ready", "loading", "down", "loading", "down"]


async def test_monitor_without_store_still_alerts(fake_decision):
    s = fake_decision.settings
    db = s.admin_data_dir / "admin.sqlite"
    mon = SidecarMonitor(s, SidecarRegistry(s), JobStore(db), AlertStore(db))
    await mon(_snap(T0), None)


async def test_monitor_prunes_daily(monitor, fake_decision):
    mon, jobs, store = monitor
    _add(store, T0 - 200 * DAY, 1)
    await mon(_snap(T0), None)
    assert store.recent() == []


def test_unknown_sample_state_refused(store):
    with pytest.raises(ValueError):
        store.add_sample(T0, "great")


def test_availability_is_per_sidecar(tmp_path):
    store = DecisionStore(tmp_path / "a.sqlite")
    store.add_sample(T0, "ready")
    store.add_sample(T0, "down", sidecar="qwen-tts")
    assert store.availability(0, T0 + 1)["counts"]["ready"] == 1
    tts = store.availability(0, T0 + 1, sidecar="qwen-tts")
    assert tts["counts"]["down"] == 1 and tts["ratio"] == 0


def test_ok_read_without_latency_is_counted_not_timed(store):
    _add(store, T0, None)
    (d,) = store.daily(T0 - 1, T0 + 1, tz_offset_min=0)
    assert d["requests"] == 1 and d["errors"] == 0 and d["p50_ms"] is None


def test_usage_labels_refused_and_unknown_clients(store):
    s = {"n_questions": 1, "types": "noul", "steps": None, "samples": None}
    store.add_request(
        ts=T0,
        source="gateway",
        summary=s,
        input_tokens=None,
        latency_ms=1.0,
        model_ms=None,
        status="error",
        error_code=401,
    )
    store.add_request(
        ts=T0,
        source="gateway",
        summary=s,
        input_tokens=5,
        latency_ms=None,
        model_ms=None,
        status="ok",
        error_code=None,
        client_id="77",
    )
    rows = {r["client"]: r for r in store.usage(T0 - 1, T0 + 1, 0, "client", {})}
    assert rows["(no key)"]["errors"] == 1
    assert rows["client 77"]["samples"] == 1 and rows["client 77"]["p50_ms"] is None
