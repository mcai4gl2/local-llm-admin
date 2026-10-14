"""M2 §2: stored quality runs, cases, repetitions, baselines and retention."""

import sqlite3

import pytest

from local_llm_admin.jobs import JobStore
from local_llm_admin.quality_store import MAX_DETAIL, QualityStore
from local_llm_admin.storage import SCHEMA_VERSION, migrate


@pytest.fixture
def store(tmp_path):
    return QualityStore(tmp_path / "admin.sqlite")


def _run(store, run_id="r1", started=1000.0, **kw):
    fields = {
        "id": run_id,
        "job_id": "j1",
        "kind": "benchmark",
        "scope": "current",
        "model": None,
        "started": started,
        "runtime_version": "0.7.3",
        "runtime_fingerprint": "fp",
        "original": {"engine": "mlx", "name": "qwen"},
        "settings": {"temperature": 0},
    }
    store.create_run({**fields, **kw})
    return run_id


def _case(store, run_id="r1", key="p128", settings_hash="h1", cache="verified_cold", fp="f1"):
    return store.add_case(
        run_id,
        model="qwen",
        model_path="/m/Qwen",
        fingerprint=fp,
        case_key=key,
        kind="benchmark",
        settings_hash=settings_hash,
        cache_state=cache,
        load_s=4.5,
    )


def _reps(store, case_id, decodes, contaminated=False):
    store.add_measurement(case_id, 0, warmup=True, contaminated=False, error=None, data={})
    for i, d in enumerate(decodes, start=1):
        store.add_measurement(
            case_id,
            i,
            warmup=False,
            contaminated=contaminated,
            error=None,
            data={"backend_decode_tok_s": d, "ttft_s": 0.5, "peak_rss_bytes": None},
        )


def test_run_round_trip_survives_a_new_store_instance(tmp_path, store):
    _run(store)
    store.update_run("r1", progress={"model": "qwen", "step": "p128"})
    again = QualityStore(tmp_path / "admin.sqlite")
    run = again.get_run("r1")
    assert run["status"] == "running" and run["original"] == {"engine": "mlx", "name": "qwen"}
    assert run["progress"] == {"model": "qwen", "step": "p128"}
    assert run["cases"] == []
    assert again.list_runs(10)[0]["id"] == "r1"
    assert again.get_run("missing") is None


def test_measurements_keep_null_metrics_as_null(store):
    _run(store)
    cid = _case(store)
    store.add_measurement(
        cid, 1, warmup=False, contaminated=None, error=None, data={"backend_decode_tok_s": None}
    )
    case = store.get_run("r1", measurements=True)["cases"][0]
    m = case["measurements"][0]
    assert m["data"]["backend_decode_tok_s"] is None and m["contaminated"] is None


def test_completing_a_case_aggregates_and_sets_the_first_baseline(store):
    _run(store)
    cid = _case(store)
    _reps(store, cid, [40.0, 42.0, 38.0])
    summary = store.complete_benchmark_case(cid, run_controlled=True)
    assert summary["complete"] and summary["medians"]["backend_decode_tok_s"] == 40.0
    assert summary["regressions"] == [] and summary["baseline"] is None
    case = store.get_run("r1")["cases"][0]
    assert case["status"] == "complete" and case["summary"]["valid"] == 3
    base = store.baseline("f1", "p128", "h1", "verified_cold")
    assert base["run_id"] == "r1" and base["medians"]["backend_decode_tok_s"] == 40.0


def test_regression_against_the_latest_comparable_baseline(store):
    _run(store, "r1")
    c1 = _case(store, "r1")
    _reps(store, c1, [40.0, 40.0, 40.0])
    store.complete_benchmark_case(c1, run_controlled=True)
    _run(store, "r2", started=2000.0)
    c2 = _case(store, "r2")
    _reps(store, c2, [30.0, 30.0, 30.0])
    s = store.complete_benchmark_case(c2, run_controlled=True)
    assert [r["metric"] for r in s["regressions"]] == ["backend_decode_tok_s"]
    assert s["baseline"]["run_id"] == "r1"
    # the newer valid result becomes the baseline
    assert store.baseline("f1", "p128", "h1", "verified_cold")["run_id"] == "r2"


@pytest.mark.parametrize(
    "change",
    [
        {"settings_hash": "h2"},
        {"cache": "unknown"},
        {"fp": "f2"},  # another quantization or weights
        {"key": "p2k"},
    ],
)
def test_changed_settings_cache_label_or_weights_are_not_compared(store, change):
    _run(store, "r1")
    c1 = _case(store, "r1")
    _reps(store, c1, [40.0, 40.0, 40.0])
    store.complete_benchmark_case(c1, run_controlled=True)
    _run(store, "r2", started=2000.0)
    c2 = _case(store, "r2", **change)
    _reps(store, c2, [10.0, 10.0, 10.0])
    s = store.complete_benchmark_case(c2, run_controlled=True)
    assert s["baseline"] is None and s["regressions"] == []


def test_uncontrolled_results_neither_compare_nor_become_baselines(store):
    _run(store, "r1")
    c1 = _case(store, "r1")
    _reps(store, c1, [40.0, 40.0, 40.0])
    store.complete_benchmark_case(c1, run_controlled=True)
    _run(store, "r2", started=2000.0)
    c2 = _case(store, "r2")
    _reps(store, c2, [10.0, 10.0, 10.0])
    s = store.complete_benchmark_case(c2, run_controlled=False)
    assert s["regressions"] == [] and s["controlled"] is False
    assert store.baseline("f1", "p128", "h1", "verified_cold")["run_id"] == "r1"


def test_partial_failure_is_incomplete_and_not_a_baseline(store):
    _run(store)
    cid = _case(store)
    _reps(store, cid, [40.0], contaminated=False)
    store.add_measurement(cid, 2, warmup=False, contaminated=False, error="http 500", data={})
    s = store.complete_benchmark_case(cid, run_controlled=True)
    assert s["complete"] is False
    assert store.get_run("r1")["cases"][0]["status"] == "incomplete"
    assert store.baseline("f1", "p128", "h1", "verified_cold") is None


def test_failed_benchmark_case_is_distinct_from_incompatibility(store):
    _run(store)
    bench = _case(store)
    store.fail_case(bench, "failed", "model server is unreachable")
    compat = store.add_case(
        "r1",
        model="qwen",
        model_path="/m/Qwen",
        fingerprint="f1",
        case_key="tools",
        kind="compatibility",
        settings_hash=None,
        cache_state=None,
        load_s=None,
    )
    store.set_compatibility(compat, "fail", "tool call arguments are not valid JSON")
    cases = {c["case_key"]: c for c in store.get_run("r1")["cases"]}
    assert cases["p128"]["status"] == "failed" and cases["p128"]["kind"] == "benchmark"
    assert cases["tools"]["status"] == "fail" and cases["tools"]["kind"] == "compatibility"


def test_error_details_are_bounded(store):
    _run(store)
    cid = _case(store)
    store.fail_case(cid, "failed", "x" * 10_000)
    assert len(store.get_run("r1")["cases"][0]["detail"]) == MAX_DETAIL
    store.update_run("r1", error="y" * 10_000)
    assert len(store.get_run("r1")["error"]) == MAX_DETAIL


def test_set_compatibility_rejects_unknown_outcomes(store):
    _run(store)
    cid = _case(store)
    with pytest.raises(ValueError):
        store.set_compatibility(cid, "maybe", "")


def test_latest_compatibility_per_model_and_case(store):
    for run_id, started, outcome in (("r1", 1000.0, "fail"), ("r2", 2000.0, "pass")):
        _run(store, run_id, started=started, kind="compatibility")
        cid = store.add_case(
            run_id,
            model="qwen",
            model_path="/m/Qwen",
            fingerprint="f1",
            case_key="tools",
            kind="compatibility",
            settings_hash=None,
            cache_state=None,
            load_s=None,
        )
        store.set_compatibility(cid, outcome, "")
    latest = store.latest_compatibility()
    assert latest["qwen"]["tools"]["status"] == "pass"
    assert latest["qwen"]["tools"]["run_id"] == "r2"


def test_interrupted_runs_are_reconciled_not_resumed(store):
    _run(store)
    cid = _case(store)
    rows = store.reconcile_interrupted(now=5000.0)
    assert [r["id"] for r in rows] == ["r1"]
    run = store.get_run("r1")
    assert run["status"] == "interrupted" and run["finished"] == 5000.0
    assert run["cases"][0]["status"] == "interrupted"
    assert store.reconcile_interrupted(now=6000.0) == []
    assert cid


def test_retention_prunes_old_details_but_keeps_baselines_and_jobs(tmp_path, store):
    jobs = JobStore(tmp_path / "admin.sqlite")
    jobs.save_job(
        {
            "id": "j1",
            "action": "quality",
            "name": None,
            "from_name": None,
            "to_name": None,
            "source": "benchmark",
            "status": "succeeded",
            "returncode": None,
            "started": 1000.0,
            "finished": 1100.0,
            "rolled_back": None,
            "observed_engine": None,
            "output": "",
        }
    )
    _run(store, "old", started=1000.0)
    c = _case(store, "old")
    _reps(store, c, [40.0, 40.0, 40.0])
    store.complete_benchmark_case(c, run_controlled=True)
    store.update_run("old", status="succeeded", finished=1100.0)
    _run(store, "new", started=100 * 86400.0)
    removed = store.prune(now=100 * 86400.0, days=90)
    assert removed == 1
    assert store.get_run("old") is None and store.get_run("new") is not None
    assert store.baseline("f1", "p128", "h1", "verified_cold")["run_id"] == "old"
    assert jobs.get_job("j1") is not None
    # a running run is never pruned, however old
    _run(store, "stuck", started=1.0)
    assert store.prune(now=100 * 86400.0, days=90) == 0


def test_migration_upgrades_a_v3_database_twice(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    from local_llm_admin import storage

    for step in storage.MIGRATIONS[:3]:
        step(conn)
    conn.execute("PRAGMA user_version = 3")
    migrate(conn)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"quality_runs", "quality_cases", "quality_measurements", "quality_baselines"} <= tables
    conn.close()


def test_update_run_validates_fields_and_stores_controlled_as_bool(store):
    _run(store)
    with pytest.raises(ValueError):
        store.update_run("r1", kind="other")
    store.update_run("r1", controlled=False, restoration={"outcome": "restored"})
    run = store.get_run("r1")
    assert run["controlled"] is False and run["restoration"] == {"outcome": "restored"}


def test_cache_state_can_be_set_after_the_first_response(store):
    _run(store)
    cid = _case(store, cache=None)
    store.set_cache_state(cid, "verified_cold")
    assert store.get_run("r1")["cases"][0]["cache_state"] == "verified_cold"


def test_unknown_cache_state_neither_compares_nor_becomes_a_baseline(store):
    for run_id, started, decode in (("r1", 1000.0, 40.0), ("r2", 2000.0, 10.0)):
        _run(store, run_id, started=started)
        cid = _case(store, run_id, cache="unknown")
        _reps(store, cid, [decode] * 3)
        s = store.complete_benchmark_case(cid, run_controlled=True)
        assert s["complete"] and s["baseline"] is None and s["regressions"] == []
        assert s["cache_state"] == "unknown"
    assert store.baseline("f1", "p128", "h1", "unknown") is None
