"""M10: eval storage."""

import sqlite3
from typing import Any

import pytest

from local_llm_admin.eval_store import EvalStore
from local_llm_admin.eval_suites import parse_suite
from local_llm_admin.storage import SCHEMA_VERSION


def _s(version=1, answer="y", sid="t"):
    return parse_suite(
        {
            "id": sid,
            "version": version,
            "title": "T",
            "cases": [
                {
                    "id": "a",
                    "category": "x",
                    "prompt": "p",
                    "grader": {"type": "exact", "answer": answer},
                }
            ],
        }
    )


@pytest.fixture
def store(tmp_path):
    return EvalStore(tmp_path / "admin.sqlite")


def test_schema_version():
    assert SCHEMA_VERSION >= 14  # eval tables survive later additive migrations


def test_builtins_are_immutable_per_version(store):
    store.sync_builtins([_s()])
    store.sync_builtins([_s()])  # idempotent
    with pytest.raises(RuntimeError, match="bump the version"):
        store.sync_builtins([_s(answer="z")])
    store.sync_builtins([_s(version=2, answer="z")])
    assert store.suites()[0]["version"] == 2


def test_sync_builtins_refuses_a_custom_row_at_the_same_version(store):
    store.add_custom(_s(sid="c"))
    with pytest.raises(RuntimeError, match="bump the version"):
        store.sync_builtins([_s(sid="c")])


def test_custom_cannot_shadow_builtin(store):
    store.sync_builtins([_s()])
    with pytest.raises(ValueError):
        store.add_custom(_s())


def test_custom_gets_next_version_and_roundtrips(store):
    store.add_custom(_s(sid="c"))
    store.add_custom(_s(sid="c", answer="q"))
    assert store.suite("c") is not None and store.suite("c").version == 2
    assert store.suite("c", 1).version == 1
    assert store.suite("c", 9) is None and store.suite("nope") is None
    row = store.suites()[0]
    assert row["id"] == "c" and row["version"] == 2 and row["origin"] == "custom"
    assert row["cases"] == 1 and row["title"] == "T"
    assert row["content_hash"] == store.suite("c").content_hash()


def test_delete_custom_keeps_builtins(store):
    store.sync_builtins([_s()])
    store.add_custom(_s(sid="c"))
    store.add_custom(_s(sid="c"))
    assert store.delete_custom("c") == 2
    assert store.delete_custom("t") == 0
    assert [s["id"] for s in store.suites()] == ["t"]


def _row(store, run, model, case, score, state="graded", category="x", suite="t",
         suite_hash="sh", settings_hash="h", **extra):  # fmt: skip
    return store.add_result(
        run_id=run,
        quality_case_id=1,
        model=model,
        fingerprint="fp-" + model,
        settings_hash=settings_hash,
        suite_id=suite,
        suite_hash=suite_hash,
        case_id=case,
        category=category,
        response="r",
        score=score,
        passed=int(score == 1.0) if score is not None else None,
        grade_state=state,
        **extra,
    )


def test_finalize_excludes_judge_errors_and_promotes(store):
    _row(store, "r1", "m", "a", 1.0)
    _row(store, "r1", "m", "b", 0.0)
    _row(store, "r1", "m", "c", None, state="judge_error")
    summary = store.finalize("r1", "m", promote=True)
    assert summary["n"] == 2 and summary["excluded"] == 1 and summary["mean"] == 0.5
    assert store.scorecard()[0]["suites"]["t"]["mean"] == 0.5


def test_finalize_by_category_and_judge_flags(store):
    _row(store, "r1", "m", "a", 1.0, category="x")
    _row(store, "r1", "m", "b", 0.0, category="y", judge="j1", self_judged=1)
    _row(store, "r1", "m", "c", None, state="ungraded")
    _row(store, "r1", "m", "d", None, state="failed")
    s = store.finalize("r1", "m", promote=True)
    assert s["by_category"] == {"x": 1.0, "y": 0.0}
    assert s["excluded"] == 2 and s["judge"] == "j1" and s["self_judged"] is True
    card = store.scorecard()[0]
    assert card["fingerprint"] == "fp-m" and card["model"] == "m"
    assert card["suites"]["t"]["run_id"] == "r1" and card["suites"]["t"]["n"] == 2


def test_unpromoted_run_does_not_replace_latest(store):
    _row(store, "r1", "m", "a", 1.0)
    store.finalize("r1", "m", promote=True)
    _row(store, "r2", "m", "a", 0.0)
    store.finalize("r2", "m", promote=False)
    assert store.scorecard()[0]["suites"]["t"]["mean"] == 1.0


def test_promote_replaces_latest_for_the_same_key(store):
    _row(store, "r1", "m", "a", 1.0)
    store.finalize("r1", "m", promote=True)
    _row(store, "r2", "m", "a", 0.0)
    store.finalize("r2", "m", promote=True)
    assert store.scorecard()[0]["suites"]["t"]["mean"] == 0.0
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT run_id FROM eval_scores").fetchall() == [("r2",)]


def test_finalize_with_no_graded_rows_writes_nothing(store):
    _row(store, "r1", "m", "a", None, state="failed")
    _row(store, "r1", "m", "b", None, state="judge_error")
    s = store.finalize("r1", "m", promote=True)
    assert s["n"] == 0 and s["excluded"] == 2
    assert s["mean"] is None and s["lo"] is None and s["hi"] is None
    assert s["by_category"] == {}
    assert store.scorecard() == []
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM eval_scores").fetchone()[0] == 0


def test_finalize_with_no_rows_at_all(store):
    s = store.finalize("none", "m", promote=True)
    assert s["n"] == 0 and s["excluded"] == 0 and s["mean"] is None


def test_finalize_without_fingerprint_does_not_promote(store):
    store.add_result(
        run_id="r1",
        quality_case_id=1,
        model="m",
        fingerprint=None,
        settings_hash="h",
        suite_id="t",
        suite_hash="sh",
        case_id="a",
        category="x",
        score=1.0,
        passed=1,
        grade_state="graded",
    )
    assert store.finalize("r1", "m", promote=True)["n"] == 1
    assert store.scorecard() == []


def test_finalize_rejects_more_than_one_suite(store):
    _row(store, "r1", "m", "a", 1.0, suite="t")
    _row(store, "r1", "m", "a", 1.0, suite="u")
    with pytest.raises(ValueError, match="more than one suite"):
        store.finalize("r1", "m", promote=False)


def test_scorecard_keeps_newest_score_per_suite(store, monkeypatch):
    times = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr("local_llm_admin.eval_store.time.time", lambda: next(times))
    _row(store, "r1", "m", "a", 1.0, suite="t")
    store.finalize("r1", "m", promote=True)
    _row(store, "r2", "m", "a", 0.0, suite="u")
    store.finalize("r2", "m", promote=True)
    _row(store, "r3", "m", "a", 0.0, suite="t")
    store.finalize("r3", "m", promote=True)
    card = store.scorecard()
    assert len(card) == 1 and card[0]["suites"]["t"]["run_id"] == "r3"
    assert set(card[0]["suites"]) == {"t", "u"}


def test_add_result_bounds_and_validates(store):
    rid = _row(store, "r1", "m", "a", 1.0, tool_calls=[{"n": 1}])
    _row(store, "r1", "m", "b", 1.0)
    store.add_result(
        run_id="r1",
        quality_case_id=1,
        model="m",
        settings_hash="h",
        suite_id="t",
        suite_hash="sh",
        case_id="big",
        category="x",
        response="é" * 70000,
        grade_detail="d" * 5000,
        grade_state="graded",
        score=1.0,
        passed=1,
    )
    rows = {r["case_id"]: r for r in store.results("r1")}
    assert rows["a"]["id"] == rid and rows["a"]["tool_calls"] == '[{"n": 1}]'
    assert len(rows["big"]["response"].encode()) <= 64 * 1024
    assert len(rows["big"]["grade_detail"]) == 2048
    assert len(store.results("r1", "m")) == 3 and store.results("r1", "other") == []
    with pytest.raises(ValueError, match="unknown"):
        store.add_result(bogus=1)


def test_judging_flow(store):
    a = _row(store, "r1", "m", "a", None, state="needs_judge")
    _row(store, "r1", "m", "b", 1.0)
    pending = store.needs_judge("r1")
    assert [p["id"] for p in pending] == [a] and pending[0]["response"] == "r"
    store.set_judged(
        a, score=1.0, passed=True, detail="x" * 3000, judge="j", self_judged=False, state="graded"
    )
    assert store.needs_judge("r1") == []
    row = next(r for r in store.results("r1") if r["id"] == a)
    assert row["grade_state"] == "graded" and row["score"] == 1.0 and row["passed"] == 1
    assert row["judge"] == "j" and row["self_judged"] == 0 and len(row["grade_detail"]) == 2048
    assert row["judged_at"] is not None
    store.set_judged(
        a, score=None, passed=None, detail=None, judge="j", self_judged=True, state="judge_error"
    )
    assert store.results("r1")[0]["passed"] is None


def test_case_scores_only_graded(store):
    _row(store, "r1", "m", "a", 1.0)
    _row(store, "r1", "m", "b", None, state="failed")
    _row(store, "r1", "m", "c", 0.5, suite="u")
    assert store.case_scores("r1", "m", "t") == {"a": 1.0}


def test_prune_drops_old_results_only(store):
    with sqlite3.connect(store.path) as c:
        for rid, started in (("old", 0.0), ("new", 90 * 86400.0)):
            c.execute(
                "INSERT INTO quality_runs (id, kind, scope, status, started)"
                " VALUES (?, 'eval', 's', 'done', ?)",
                (rid, started),
            )
    _row(store, "old", "m", "a", 1.0)
    _row(store, "new", "m", "a", 1.0)
    _row(store, "orphan", "m", "a", 1.0)  # its quality_runs row was already pruned
    store.finalize("old", "m", promote=True)
    assert store.prune(100 * 86400.0, 30) == 2
    assert store.results("old") == [] and len(store.results("new")) == 1
    assert store.results("orphan") == []
    assert store.scorecard()  # scores survive


def test_is_promoted_and_suite_by_hash(store):
    suite = _s()
    store.add_custom(suite)
    assert store.suite_by_hash("t", suite.content_hash()) is not None
    assert store.suite_by_hash("t", "nope") is None
    for case_id, score in (("a", 1.0),):
        store.add_result(
            run_id="r1", quality_case_id=1, model="m", fingerprint="fp", settings_hash="s", suite_id="t",
            suite_hash=suite.content_hash(), case_id=case_id, category="x", score=score,
            passed=1, grade_state="graded",
        )  # fmt: skip
    assert not store.is_promoted("r1", "fp", "t")
    store.finalize("r1", "m", promote=True)
    assert store.is_promoted("r1", "fp", "t")
    assert not store.is_promoted("r2", "fp", "t")


def test_update_grade(store):
    rid = store.add_result(
        run_id="r1", quality_case_id=1, model="m", settings_hash="s", suite_id="t", suite_hash="h", case_id="a", category="x",
        grade_state="graded", score=0.0, passed=0,
    )  # fmt: skip
    store.update_grade(rid, score=1.0, passed=True, state="graded", detail="ok")
    (row,) = store.results("r1")
    assert (row["score"], row["passed"], row["grade_detail"]) == (1.0, 1, "ok")


def test_scorecard_keeps_thinking_on_and_off_apart(store, monkeypatch):
    store.sync_builtins([_s()])
    times = iter([100.0, 200.0])
    monkeypatch.setattr("local_llm_admin.eval_store.time.time", lambda: next(times))
    digest = _s().content_hash()
    _row(store, "r1", "m", "a", 1.0, suite_hash=digest, thinking=0)
    store.finalize("r1", "m", promote=True)
    _row(store, "r2", "m", "a", 0.0, suite_hash=digest, thinking=1, settings_hash="h2",
         judge="j1", self_judged=1)  # fmt: skip
    store.finalize("r2", "m", promote=True)
    (card,) = store.scorecard()
    off, on = card["suites"]["t"], card["suites"]["t+thinking"]
    assert (off["mean"], off["thinking"], off["run_id"]) == (1.0, False, "r1")
    assert (on["mean"], on["thinking"], on["run_id"]) == (0.0, True, "r2")
    assert on["judge"] == "j1" and on["self_judged"] is True and off["self_judged"] is False
    assert off["suite_id"] == "t" and off["suite_version"] == 1 and off["suite_hash"] == digest


def test_scorecard_suite_version_is_null_for_an_unknown_hash(store):
    _row(store, "r1", "m", "a", 1.0)
    store.finalize("r1", "m", promote=True)
    assert store.scorecard()[0]["suites"]["t"]["suite_version"] is None


def test_scorecard_exposes_stored_run_identity_without_using_current_engine(store: Any) -> None:
    import json
    with store._conn() as conn:
        conn.execute("INSERT INTO quality_runs (id, kind, scope, status, started, original, settings, runtime_version, runtime_fingerprint) VALUES (?, 'eval', 'current', 'succeeded', 100, ?, ?, '1.4.0', 'runtime-a')", ("r1", json.dumps({"engine": "splash", "reference": "stock-8bit-32k", "loaded_context": 32768}), json.dumps({"thinking": True})))
    _row(store, "r1", "m", "a", 1.0)
    store.finalize("r1", "m", promote=True)
    cell = store.scorecard()[0]["suites"]["t"]
    assert cell["provenance"]["original"]["engine"] == "splash"
    assert cell["provenance"]["original"]["loaded_context"] == 32768
    assert cell["provenance"]["runtime_fingerprint"] == "runtime-a"
    with store._conn() as conn:
        conn.execute("DELETE FROM quality_runs WHERE id='r1'")
    assert store.scorecard()[0]["suites"]["t"]["provenance"]["original"] is None
