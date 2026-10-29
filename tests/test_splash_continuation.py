"""Offline continuation budgets must preserve pairs, failures and frozen conditions."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.test_splash_phase0 import bench, runs_for
from tests.test_splash_phase0 import manifest_data as manifest_data

SPEC = importlib.util.spec_from_file_location(
    "splash_continuation",
    Path(__file__).resolve().parents[1] / "experiments/splash/continuation.py",
)
assert SPEC and SPEC.loader
continuation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = continuation
sys.path.insert(0, str(SPEC.origin.rsplit("/", 1)[0]))
try:
    SPEC.loader.exec_module(continuation)
finally:
    sys.path.pop(0)


def first_pair(data):
    return runs_for(data)[:2]


def test_planner_counterbalances_and_never_splits_a_pair(manifest_data):
    original = first_pair(manifest_data)
    before = bench.digest([r.model_dump() for r in original])
    plan = continuation.plan_windows(
        {"stock-8bit": original},
        window_s=700.0, setup_s=60.0, restore_s=60.0, slowdown=1.25,
    )
    assert plan["authorization"] == "required"
    assert plan["remaining_pairs"] == 2
    assert len(plan["windows"]) == 2
    for window, session, order in zip(
        plan["windows"], [2, 3], [["splash", "mlx"], ["mlx", "splash"]], strict=True
    ):
        assert window["budgeted_total_s"] <= 700
        assert window["restore_reserve_s"] == 60
        assert [r["backend"] for r in window["runs"]] == order
        assert {r["session"] for r in window["runs"]} == {session}
        assert {r["request_count"] for r in window["runs"]} == {18}
        assert {r["cohort"] for r in window["runs"]} == {"stock-8bit"}
        assert all(len(r["source_sha256"]) == 64 for r in window["runs"])
    assert bench.digest([r.model_dump() for r in original]) == before


def test_budget_uses_slowest_case_including_warmup(manifest_data):
    pair = first_pair(manifest_data)
    pair[0].samples[0].measurement.total_s = 40.0
    plan = continuation.plan_windows(
        {"stock": pair}, window_s=3000, setup_s=50, restore_s=80, slowdown=1.5,
    )
    mlx = next(r for r in plan["windows"][0]["runs"] if r["backend"] == "mlx")
    expected = (40 * 6 + 10.24 * 12) * 1.5
    assert mlx["budgeted_request_s"] == pytest.approx(expected)
    assert plan["windows"][0]["budgeted_total_s"] == pytest.approx(
        sum(r["budgeted_request_s"] for r in plan["windows"][0]["runs"]) + 130
    )


def test_two_cohorts_remain_separate_and_sessions_are_ordered(manifest_data):
    a = first_pair(manifest_data)
    b = copy.deepcopy(a)
    for run in b:
        run.manifest.model.weights_sha256 = "9" * 64
    plan = continuation.plan_windows(
        {"stock": a, "modified": b},
        window_s=1200, setup_s=60, restore_s=60, slowdown=1.25,
    )
    assert len(plan["windows"]) == 2
    assert [{r["session"] for r in w["runs"]} for w in plan["windows"]] == [{2}, {3}]
    assert all(len(w["runs"]) == 4 for w in plan["windows"])


@pytest.mark.parametrize("bad", [
    "error", "warmup_error", "partial", "duplicate", "missing_backend",
    "unpaired_prompt", "weights", "runtime", "order", "gap", "warmup",
    "early_stop", "cache", "missing_duration", "changed_settings", "duplicate_id",
])
def test_rejects_unusable_evidence_instead_of_dropping_it(manifest_data, bad):
    runs = first_pair(manifest_data)
    if bad == "error":
        runs[0].samples[-1].measurement.error = "timeout"
    elif bad == "warmup_error":
        runs[0].samples[0].measurement.error = "timeout"
    elif bad == "partial":
        runs[0].status = "interrupted"
    elif bad == "duplicate":
        runs.append(copy.deepcopy(runs[0]))
    elif bad == "missing_backend":
        runs.pop()
    elif bad == "unpaired_prompt":
        runs[1].samples[-1].measurement.prompt_tokens += 1
    elif bad == "weights":
        runs[1].manifest.model.weights_sha256 = "9" * 64
    elif bad == "runtime":
        runs += runs_for(manifest_data)[2:4]
        runs[-1].manifest.runtime.sha256 = "9" * 64
    elif bad == "order":
        runs[1].manifest.order = "splash-first"
    elif bad == "gap":
        runs += runs_for(manifest_data)[4:]
    elif bad == "warmup":
        runs[0].samples[0].warmup = False
    elif bad == "early_stop":
        runs[0].samples[-1].measurement.finish_reason = "stop"
    elif bad == "cache":
        runs[0].samples[-1].measurement.cached_tokens = 1
    elif bad == "missing_duration":
        runs[0].samples[0].measurement.total_s = None
    elif bad == "changed_settings":
        runs[1].manifest.settings.temperature = 0.5
    elif bad == "duplicate_id":
        runs[1].manifest.id = runs[0].manifest.id
    with pytest.raises(ValueError):
        continuation.plan_windows({"stock": runs}, window_s=10000)


def test_refuses_budget_that_cannot_hold_a_whole_pair(manifest_data):
    with pytest.raises(ValueError, match="fit"):
        continuation.plan_windows(
            {"stock": first_pair(manifest_data)},
            window_s=200, setup_s=60, restore_s=60, slowdown=1.25,
        )


@pytest.mark.parametrize("options", [
    {"window_s": 0}, {"slowdown": 0.99}, {"slowdown": float("nan")},
    {"setup_s": -1}, {"restore_s": 0}, {"window_s": float("inf")},
])
def test_rejects_invalid_budget_parameters(manifest_data, options):
    with pytest.raises(ValueError):
        continuation.plan_windows({"stock": first_pair(manifest_data)}, **options)


def test_complete_matrix_schedules_nothing(manifest_data):
    plan = continuation.plan_windows({"stock": runs_for(manifest_data)})
    assert plan["remaining_pairs"] == 0 and plan["windows"] == []
    assert plan["adoption_decision"] == "unassessed"


def test_refuses_empty_input_and_unsafe_labels():
    for inputs in [{}, {"stock": []}, {"private/path": []}]:
        with pytest.raises(ValueError):
            continuation.plan_windows(inputs)


def test_cli_private_exclusive_output_and_sanitized_errors(tmp_path, manifest_data, monkeypatch, capsys):
    results = tmp_path / "results"
    results.mkdir()
    for r in first_pair(manifest_data):
        (results / f"{r.manifest.id}.json").write_text(r.model_dump_json())
    output = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", [
        "continuation.py", "--cohort", "stock", str(results), "--out", str(output),
    ])
    assert continuation.main() == 0
    plan = json.loads(output.read_text())
    assert plan["remaining_pairs"] == 2
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    assert continuation.main() == 2
    assert output.read_bytes() == before
    assert str(tmp_path) not in capsys.readouterr().out


def test_cli_rejects_duplicate_cohort_without_writing(tmp_path, manifest_data, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    for r in first_pair(manifest_data):
        (results / f"{r.manifest.id}.json").write_text(r.model_dump_json())
    out = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", [
        "continuation.py", "--cohort", "stock", str(results),
        "--cohort", "stock", str(results), "--out", str(out),
    ])
    assert continuation.main() == 2
    assert not out.exists()
