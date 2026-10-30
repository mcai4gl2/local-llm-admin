"""Generation-bound native snapshots; never synthetic inference requests."""

from __future__ import annotations

import copy
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.backend_types import EngineObservation, OwnedProcess
from local_llm_admin.engine_telemetry import EngineTelemetryStore
from local_llm_admin.splash_telemetry import bind_reading


def observation(
    profile: str = "stock", fingerprint: str = "fp-a", native: int = 101, started: float = 2.0
) -> EngineObservation:
    return EngineObservation(
        "splash",
        profile,
        {"pid": 100, "started": 1.0, "profile_fingerprint": fingerprint},
        "ready",
        True,
        "verified",
        "verified",
        (
            OwnedProcess(100, 1.0, "/runtime/python", "controller"),
            OwnedProcess(native, started, "/runtime/native", "native"),
        ),
    )


def document() -> dict[str, Any]:
    return {
        "instance": {"id": "instance-a", "pid": 100, "model": "example/model"},
        "transport": {"restarts": 0, "status_stale": False, "status_age_ms": 0},
        "requests": {"submitted": 9, "completed": 7, "cancelled": 1, "failed": 1},
        "metrics": {"prefill_input_tokens": 120, "decode_output_tokens": 60},
    }


def reading(**kw: Any) -> dict[str, Any]:
    obs = observation(**kw)
    value = bind_reading(obs, obs, document(), model="example/model")
    assert value is not None
    return value


def test_epoch_binds_profile_fingerprint_and_complete_bundle() -> None:
    a = reading()
    assert a["source"] == "splash" and a["profile"] == "stock"
    for kw in ({"profile": "modified"}, {"fingerprint": "fp-b"}, {"native": 102}, {"started": 3.0}):
        assert reading(**kw)["epoch"] != a["epoch"]
    assert "executable" not in str(a)
    doc = document()
    doc["transport"]["restarts"] = 1
    obs = observation()
    assert bind_reading(obs, obs, doc, model="example/model")["epoch"] != a["epoch"]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "changed",
        "no-native",
        "stale",
        "missing-transport",
        "bool-restart",
        "pid",
        "model",
        "instance",
        "age",
    ],
)
def test_ambiguous_or_stale_readings_are_not_attributed(mutation: str) -> None:
    before, after, doc = observation(), observation(), document()
    if mutation == "unknown":
        after = replace(after, ownership="unknown")
    if mutation == "changed":
        after = observation(native=102)
    if mutation == "no-native":
        before = after = replace(after, processes=after.processes[:1])
    if mutation == "stale":
        doc["transport"]["status_stale"] = True
    if mutation == "missing-transport":
        del doc["transport"]
    if mutation == "bool-restart":
        doc["transport"]["restarts"] = True
    if mutation == "pid":
        doc["instance"]["pid"] = 101
    if mutation == "model":
        doc["instance"]["model"] = "example/other"
    if mutation == "instance":
        doc["instance"]["id"] = ""
    if mutation == "age":
        doc["transport"]["status_age_ms"] = float("nan")
    assert bind_reading(before, after, doc, model="example/model") is None


def test_store_replay_order_profiles_and_history_are_separate(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    a = reading()
    assert store.record(a, now=100) == 1
    assert store.record(a, now=101) == 0
    assert store.record(a, now=99) == 0
    assert store.coverage(True, now=102)["last_poll"] == 101
    assert store.record(reading(profile="modified"), now=102) == 1
    assert store.record(reading(fingerprint="fp-b"), now=103) == 1
    rows = store.readings(0, 200)
    assert len(rows) == 3 and len({r["epoch"] for r in rows}) == 3
    assert len(store.readings(0, 200, profile="modified")) == 1
    assert len(store.readings(0, 200, fingerprint="fp-b")) == 1
    assert EngineTelemetryStore(store.path).record(reading(), now=104) == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM inference_requests").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM token_usage_daily").fetchone()[0] == 0


def test_regressions_gaps_errors_and_freshness_are_explicit(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    a = reading()
    store.record(a, now=100)
    bad = copy.deepcopy(a)
    bad["counters"]["completed"] = 1
    assert store.record(bad, now=101) == 0
    assert store.coverage(True, now=102)["error"] == "counter_regression"
    store.failure("unavailable", now=103)
    cov = store.coverage(True, now=104)
    assert cov["last_poll"] == 100 and cov["last_attempt"] == 103
    assert cov["last_ingest"] == cov["last_new_data"] == 100
    assert cov["error"] == "unavailable" and cov["coverage"] == "aggregate_snapshots"
    assert store.coverage(True, now=1000)["stale"] is True
    assert store.coverage(False, now=1000)["coverage"] == "disabled"
    assert len(store.readings(0, 200)) == 1


def test_readings_and_cursor_commit_together_and_retention_is_bounded(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    with sqlite3.connect(store.path) as c:
        c.execute(
            "CREATE TRIGGER fail_status BEFORE INSERT ON kv BEGIN SELECT RAISE(ABORT, 'failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.record(reading(), now=100)
    assert store.readings(0, 200) == []
    with sqlite3.connect(store.path) as c:
        c.execute("DROP TRIGGER fail_status")
    store.record(reading(), now=100)
    assert store.prune(101, limit=1) == 1
    assert store.readings(0, 200) == []


def test_late_failure_codes_and_missing_usage_do_not_erase_good_cursor(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    store.record(reading(), now=100)
    store.failure("unavailable", now=99)
    assert store.coverage(True, now=101)["error"] is None
    store.failure("private error", now=101)
    assert store.coverage(True, now=102)["error"] == "unavailable"


def test_long_admin_gap_stays_visible_after_success(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    store.record(reading(), now=100)
    store.record(reading(), now=1000)
    assert store.coverage(True, now=1001)["gaps"][-1]["reason"] == "observation_gap"


def test_missing_field_does_not_erase_monotonic_regression_baseline(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "a.sqlite")
    full = reading()
    store.record(full, now=100)
    missing = copy.deepcopy(full)
    missing["counters"]["completed"] = None
    assert store.record(missing, now=101) == 1
    regressed = copy.deepcopy(full)
    regressed["counters"]["completed"] = 1
    assert store.record(regressed, now=102) == 0
    assert store.coverage(True, now=103)["error"] == "counter_regression"


def test_snapshot_cursor_is_stable_for_equal_timestamps_and_catalog_is_not_page_bound(tmp_path: Path) -> None:
    store = EngineTelemetryStore(tmp_path / "admin.sqlite")
    first = reading()
    store.record(first, now=100)
    other = reading(profile="modified", fingerprint="fp-b")
    store.record(other, now=101)
    # The cursor must distinguish IDs, including externally restored equal timestamps.
    with store._conn() as conn:
        conn.execute("UPDATE engine_readings SET observed=100")
    page = store.page(0, 200, limit=1)
    assert len(page["readings"]) == 1
    assert page["next"] is not None
    assert {p["profile"] for p in page["profiles"]} == {"stock", "modified"}
    older = store.page(0, 200, limit=1, before=page["next"])
    assert older["next"] is None
    assert older["readings"][0]["id"] != page["readings"][0]["id"]
    assert store.page(0, 200, profile="stock", limit=1)["readings"][0]["profile"] == "stock"
    assert store.page(0, 200, epoch=first["epoch"])["readings"][0]["epoch"] == first["epoch"]
    with pytest.raises(ValueError, match="cursor"):
        store.page(0, 200, before="invalid")


@pytest.mark.parametrize("cursor", ["nan:1", "inf:1", "100:0", "100:-1", "100:1:2"])
def test_snapshot_cursor_rejects_nonfinite_and_invalid_ids(tmp_path: Path, cursor: str) -> None:
    store = EngineTelemetryStore(tmp_path / "admin.sqlite")
    with pytest.raises(ValueError, match="cursor"):
        store.page(0, 200, before=cursor)
