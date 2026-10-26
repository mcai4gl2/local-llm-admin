"""Synthetic native validation tests never load a model or touch a service."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops/splash-phase0"))
try:
    import bench
    import native_timing
    import native_validation as validation
finally:
    sys.path.pop(0)


def trace_and_request(backend="mlx"):
    req = (
        {
            "rendered_prompt": "Benchmark instance abc123. synthetic",
            "arguments": {"max_tokens": 4, "seed": 0, "temperature": 0.0, "enable_thinking": False},
        }
        if backend == "mlx"
        else {
            "prompt_tokens": [30, 31],
            "max_new_tokens": 4,
            "seed": 0,
            "sampling": {"temperature": 0.0},
            "thinking": False,
        }
    )
    companion = {
        "contract": "splash-native-request-1",
        "backend": backend,
        "request_sha256": bench.digest(req),
        "native_request": req,
    }
    trace = native_timing.NativeTrace.model_validate(
        {
            "contract": "splash-native-emission-1",
            "backend": backend,
            "runtime_sha256": "a" * 64,
            "adapter_sha256": "b" * 64,
            "request_sha256": bench.digest(req),
            "source": "engine_callback_entry",
            "clock": "monotonic_ns",
            "completion_tokens": 4,
            "completion_ids_sha256": bench.digest([1, 2, 3, 4]),
            "finish_reason": "length",
            "batches": [{"at_ns": 100, "token_ids": [1, 2]}, {"at_ns": 200, "token_ids": [3, 4]}],
        }
    )
    m = bench.Measurement(
        prompt_tokens=2,
        completion_tokens=4,
        cached_tokens=0,
        finish_reason="length",
        total_s=1,
        answer="synthetic",
    )
    return trace, companion, m


@pytest.mark.parametrize("backend", ["mlx", "splash"])
def test_native_review_reconciles_private_inputs_and_http_usage(backend):
    trace, request, m = trace_and_request(backend)
    result = validation.review_trace(
        trace,
        request,
        m,
        backend=backend,
        source_sha256="a" * 64,
        adapter_sha256="b" * 64,
        nonce="abc123",
        decoded_prompt="Benchmark instance abc123. synthetic",
        max_tokens=4,
        reasoning=False,
    )
    assert result["status"] == "accounted_not_qualified"
    assert result["native_gate"] == "unassessed"
    assert result["post_first_batch_tokens"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        "count",
        "finish",
        "cache",
        "error",
        "nonce",
        "source",
        "adapter",
        "input",
        "reasoning",
        "cap",
        "prompt_count",
    ],
)
def test_native_review_refuses_mismatches(bad):
    t, r, m = trace_and_request("splash")
    kw = dict(
        backend="splash",
        source_sha256="a" * 64,
        adapter_sha256="b" * 64,
        nonce="abc123",
        decoded_prompt="Benchmark instance abc123. synthetic",
        max_tokens=4,
        reasoning=False,
    )
    if bad == "count":
        m.completion_tokens = 3
    elif bad == "finish":
        m.finish_reason = "stop"
    elif bad == "cache":
        m.cached_tokens = 1
    elif bad == "error":
        m.error = "transport"
    elif bad == "nonce":
        kw["nonce"] = "other"
    elif bad == "source":
        kw["source_sha256"] = "c" * 64
    elif bad == "adapter":
        kw["adapter_sha256"] = "c" * 64
    elif bad == "input":
        r["native_request"]["seed"] = 1
    elif bad == "reasoning":
        kw["reasoning"] = True
    elif bad == "cap":
        kw["max_tokens"] = 5
    elif bad == "prompt_count":
        m.prompt_tokens = 3
    with pytest.raises(ValueError):
        validation.review_trace(t, r, m, **kw)


def samples(ratio=1.01):
    result = []
    for phase in range(4):
        for category in ["short", "2k", "8k"]:
            for rep in [1, 2]:
                block = 0 if phase < 2 else 1
                result.append(
                    {
                        "phase": phase,
                        "category": category,
                        "rep": rep,
                        "warmup": False,
                        "nonce": f"{block}-{category}-{rep}",
                        "body_sha256": "c" * 64,
                        "response_sha256": "d" * 64,
                        "measurement": bench.Measurement(
                            prompt_tokens=10,
                            completion_tokens=256,
                            cached_tokens=0,
                            finish_reason="length",
                            total_s=ratio if phase in (1, 2) else 1,
                            answer="synthetic",
                        ).model_dump(),
                    }
                )
    return result


def test_overhead_compares_abba_pairs_without_native_speed_claims():
    result = validation.compare_overhead(samples(), "mlx")
    assert result["decision"] == "eligible"
    assert result["median_wall_ratio"] == pytest.approx(1.01)
    assert result["ci95"] == pytest.approx([1.01, 1.01])
    assert result["native_gate"] == "unassessed"
    assert result["paired_requests"] == 12


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "duplicate",
        "body",
        "response",
        "nonce",
        "tokens",
        "prompt",
        "error",
        "reuse",
        "early",
    ],
)
def test_overhead_never_discards_invalid_samples(bad):
    rows = samples()
    if bad == "missing":
        rows.pop()
    elif bad == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif bad == "body":
        rows[6]["body_sha256"] = "e" * 64
    elif bad == "response":
        rows[6]["response_sha256"] = "e" * 64
    elif bad == "nonce":
        rows[6]["nonce"] = "changed"
    else:
        m = rows[0]["measurement"]
        if bad == "tokens":
            m["completion_tokens"] = 255
        elif bad == "prompt":
            m["prompt_tokens"] = 11
        elif bad == "error":
            m["error"] = "timeout"
        elif bad == "reuse":
            m["cached_tokens"] = 1
        elif bad == "early":
            m["finish_reason"] = "stop"
    result = validation.compare_overhead(rows, "mlx")
    assert result["decision"] == "inconclusive" and result["issues"]
    assert result["native_gate"] == "unassessed"


def test_excludes_only_declared_warmups_and_detects_overhead():
    rows = samples(1.10)
    warm = copy.deepcopy(rows[0])
    warm.update(rep=0, warmup=True)
    warm["measurement"]["total_s"] = 1000
    rows.append(warm)
    assert validation.compare_overhead(rows, "splash")["decision"] == "fail"


def test_category_regression_cannot_hide_in_overall_median():
    rows = samples(1.0)
    for row in rows:
        if row["phase"] in (1, 2) and row["category"] == "8k":
            row["measurement"]["total_s"] = 1.2
    report = validation.compare_overhead(rows, "mlx")
    assert report["median_wall_ratio"] == 1.0
    assert report["categories"]["8k"]["median_wall_ratio"] == 1.2
    assert report["decision"] == "fail"


@pytest.mark.parametrize(
    "bad", ["missing_duration", "bad_schema", "warmup_rep", "warmup_category", "duplicate_warmup"]
)
def test_invalid_duration_schema_and_warmup_markers_cannot_pass(bad):
    rows = samples()
    if bad == "missing_duration":
        rows[0]["measurement"]["total_s"] = None
    elif bad == "bad_schema":
        rows[0]["phase"] = True
    else:
        warm = copy.deepcopy(rows[0])
        warm.update(rep=0, warmup=True)
        if bad == "warmup_rep":
            warm["rep"] = 1
        elif bad == "warmup_category":
            warm["category"] = "8k"
        rows.append(warm)
        if bad == "duplicate_warmup":
            rows.append(copy.deepcopy(warm))
    report = validation.compare_overhead(rows, "mlx")
    assert report["decision"] == "inconclusive" and report["issues"]


def test_small_positive_median_with_wide_interval_remains_inconclusive():
    rows = samples(1.0)
    for row in rows:
        if row["phase"] in (1, 2) and row["rep"] == 2:
            row["measurement"]["total_s"] = 1.08
    report = validation.compare_overhead(rows, "mlx")
    assert report["median_wall_ratio"] == pytest.approx(1.04)
    assert report["ci95"][1] > 1.05
    assert report["decision"] == "inconclusive"


def test_reasoning_requires_observed_reasoning_and_final_answer():
    t, r, m = trace_and_request("mlx")
    r["native_request"]["arguments"]["enable_thinking"] = True
    r["request_sha256"] = bench.digest(r["native_request"])
    t.request_sha256 = r["request_sha256"]
    kw = dict(
        backend="mlx",
        source_sha256="a" * 64,
        adapter_sha256="b" * 64,
        nonce="abc123",
        decoded_prompt=r["native_request"]["rendered_prompt"],
        max_tokens=4,
        reasoning=True,
    )
    with pytest.raises(ValueError):
        validation.review_trace(t, r, m, **kw)
    m.reasoning = "synthetic reasoning"
    assert validation.review_trace(t, r, m, **kw)["status"] == "accounted_not_qualified"


def test_overhead_cli_is_private_exclusive_and_sanitizes_failures(tmp_path, capsys):
    import json

    src, out = tmp_path / "samples.json", tmp_path / "result.json"
    src.write_text(json.dumps(samples()))
    argv = ["--samples", str(src), "--backend", "mlx", "--out", str(out)]
    assert validation.main(argv) == 0
    assert out.stat().st_mode & 0o777 == 0o600
    saved = out.read_bytes()
    assert validation.main(argv) == 2 and out.read_bytes() == saved
    src.write_text("REDACTED_TEST_PAYLOAD")
    assert validation.main(argv) == 2
    assert "REDACTED_TEST_PAYLOAD" not in capsys.readouterr().out
    with pytest.raises(ValueError):
        validation.compare_overhead([], "unknown")


def test_boolean_phase_numbers_cannot_be_treated_as_real_launch_indices():
    rows = samples()
    for row in rows:
        if row["phase"] in (0, 1):
            row["phase"] = bool(row["phase"])
    report = validation.compare_overhead(rows, "mlx")
    assert report["decision"] == "inconclusive"
    assert "invalid_sample" in report["issues"]
