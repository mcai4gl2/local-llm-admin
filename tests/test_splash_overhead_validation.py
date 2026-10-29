"""Expanded audit tests use synthetic token records, never a running model."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/splash"))
try:
    import bench
    import overhead_validation as audit
finally:
    sys.path.pop(0)


def rows(ratio=1.01):
    result = []
    for window in range(4):
        for phase in range(4):
            for category in ["short", "2k", "8k"]:
                for rep in range(3):
                    block = 0 if phase < 2 else 1
                    nonce = f"w{window}-{'warm-' + str(phase) if rep == 0 else 'pair-' + str(block)}-{category}-{rep}"
                    rendered = f"Benchmark instance {nonce}. Explain {category}."
                    body = {
                        "model": "example/qwen",
                        "messages": [
                            {"role": "system", "content": f"Benchmark instance {nonce}."},
                            {"role": "user", "content": f"Explain {category}."},
                        ],
                        "max_tokens": 256,
                        "temperature": 0.0,
                        "seed": 0,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                        "enable_thinking": False,
                    }
                    m = bench.Measurement(
                        prompt_tokens=10,
                        completion_tokens=256,
                        cached_tokens=0,
                        finish_reason="length",
                        ttft_s=0.1,
                        total_s=ratio if phase in [1, 2] else 1.0,
                        answer="synthetic",
                    )
                    req = {
                        "rendered_prompt": rendered,
                        "arguments": {
                            "max_tokens": 256,
                            "temperature": 0.0,
                            "seed": 0,
                            "enable_thinking": False,
                        },
                    }
                    companion = {
                        "contract": "splash-native-request-1",
                        "backend": "mlx",
                        "request_sha256": bench.digest(req),
                        "native_request": req,
                    }
                    trace = {
                        "contract": "splash-native-emission-1",
                        "backend": "mlx",
                        "runtime_sha256": "a" * 64,
                        "adapter_sha256": "b" * 64,
                        "request_sha256": bench.digest(req),
                        "source": "engine_callback_entry",
                        "clock": "monotonic_ns",
                        "completion_tokens": 256,
                        "completion_ids_sha256": bench.digest(list(range(256))),
                        "finish_reason": "length",
                        "batches": [
                            {"at_ns": 100, "token_ids": [0]},
                            {"at_ns": 200, "token_ids": list(range(1, 256))},
                        ],
                    }
                    instrumented = phase in [1, 2]
                    result.append(
                        {
                            "model_pins": {
                                "weights_sha256": "c" * 64,
                                "tokenizer_sha256": "d" * 64,
                                "template_sha256": "e" * 64,
                            },
                            "window": window,
                            "phase": phase,
                            "category": category,
                            "rep": rep,
                            "warmup": rep == 0,
                            "nonce": nonce,
                            "body": body,
                            "headers": {"X-APC-Tenant": nonce},
                            "body_sha256": bench.digest(body),
                            "response_sha256": bench.digest(
                                {"answer": m.answer, "reasoning": m.reasoning}
                            ),
                            "measurement": m.model_dump(),
                            "native_trace": trace if instrumented else None,
                            "native_request": companion if instrumented else None,
                            "decoded_prompt": rendered if instrumented else None,
                        }
                    )
    return result


def review(samples):
    return audit.compare_overhead(
        samples,
        source_sha256="a" * 64,
        adapter_sha256="b" * 64,
        model_pins={
            "weights_sha256": "c" * 64,
            "tokenizer_sha256": "d" * 64,
            "template_sha256": "e" * 64,
        },
    )


def test_fixed_expanded_schedule_requires_all_windows_and_traces():
    report = review(rows())
    assert report["decision"] == "eligible" and report["paired_requests"] == 48
    assert report["excluded_warmups"] == 48 and report["accounted_native_traces"] == 72
    assert report["median_wall_ratio"] == pytest.approx(1.01)
    assert report["ci95"] == pytest.approx([1.01, 1.01])
    assert report["native_gate"] == "unassessed" and report["windows"] == 4
    assert "synthetic" not in json.dumps(report) and "example/qwen" not in json.dumps(report)


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "duplicate",
        "window",
        "phase",
        "bool",
        "warmup_marker",
        "warmup_error",
        "warmup_trace",
        "trace",
        "trace_count",
        "trace_ids",
        "source",
        "adapter",
        "companion",
        "settings",
        "body_digest",
        "body",
        "response_digest",
        "response",
        "prompt",
        "reuse",
        "nonce",
        "finish",
        "error",
        "ttft",
        "elapsed",
        "original_trace",
        "cross_nonce",
    ],
)
def test_expanded_review_never_selects_eligible_subset(bad):
    data = rows()
    original = data[0]
    inst = next(r for r in data if r["phase"] == 1 and r["rep"] == 1)
    if bad == "missing":
        data.pop()
    elif bad == "duplicate":
        data.append(copy.deepcopy(data[0]))
    elif bad == "window":
        data[0]["window"] = 4
    elif bad == "phase":
        data[0]["phase"] = 4
    elif bad == "bool":
        data[0]["window"] = True
    elif bad == "warmup_marker":
        data[0]["warmup"] = False
    elif bad == "warmup_error":
        data[0]["measurement"]["error"] = "timeout"
    elif bad == "warmup_trace":
        next(r for r in data if r["phase"] == 1 and r["warmup"])["native_trace"] = None
    elif bad == "trace":
        inst["native_trace"] = None
    elif bad == "trace_count":
        inst["native_trace"]["completion_tokens"] = 255
    elif bad == "trace_ids":
        inst["native_trace"]["batches"][-1]["token_ids"][-1] = 999
    elif bad == "source":
        inst["native_trace"]["runtime_sha256"] = "c" * 64
    elif bad == "adapter":
        inst["native_trace"]["adapter_sha256"] = "c" * 64
    elif bad == "companion":
        inst["native_request"]["native_request"]["arguments"]["seed"] = 1
    elif bad == "settings":
        inst["body"]["enable_thinking"] = True
        inst["body_sha256"] = bench.digest(inst["body"])
    elif bad == "body_digest":
        inst["body_sha256"] = "c" * 64
    elif bad == "body":
        inst["body"]["messages"][-1]["content"] = "different"
        inst["body_sha256"] = bench.digest(inst["body"])
    elif bad == "response_digest":
        inst["response_sha256"] = "c" * 64
    elif bad == "response":
        inst["measurement"]["answer"] = "different"
        inst["response_sha256"] = bench.digest({"answer": "different", "reasoning": ""})
    elif bad == "prompt":
        inst["measurement"]["prompt_tokens"] = 11
    elif bad == "reuse":
        inst["measurement"]["cached_tokens"] = 1
    elif bad == "nonce":
        inst["nonce"] = "other"
    elif bad == "finish":
        inst["measurement"]["finish_reason"] = "stop"
    elif bad == "error":
        inst["measurement"]["error"] = ""
    elif bad == "ttft":
        inst["measurement"]["ttft_s"] = 0
    elif bad == "elapsed":
        inst["native_trace"]["batches"][-1]["at_ns"] = 10**12
    elif bad == "original_trace":
        original["native_trace"] = inst["native_trace"]
    elif bad == "cross_nonce":
        other = next(r for r in data if r["window"] == 1 and r["phase"] == 0 and r["rep"] == 1)
        other["nonce"] = inst["nonce"]
    report = review(data)
    assert report["decision"] == "inconclusive" and report["issues"]
    assert report["native_gate"] == "unassessed"


def test_rejects_measured_median_over_five_percent_without_rewriting_threshold():
    report = review(rows(1.10))
    assert report["decision"] == "fail" and report["threshold"] == 1.05


def test_a_category_bound_cannot_hide_behind_overall_median():
    data = rows()
    for row in data:
        if row["category"] == "8k" and row["window"] == 3 and row["phase"] in [1, 2]:
            row["measurement"]["total_s"] = 1.5
    report = review(data)
    assert report["median_wall_ratio"] < 1.05
    assert report["categories"]["8k"]["ci95"][1] > 1.05
    assert report["decision"] == "inconclusive"


def test_warmup_durations_are_excluded_but_accounting_is_required():
    data = rows()
    for row in data:
        if row["warmup"]:
            row["measurement"]["total_s"] = 1000.0
    report = review(data)
    assert report["decision"] == "eligible" and report["median_wall_ratio"] == pytest.approx(1.01)


def test_wrong_checkpoint_hashes_cannot_be_labeled_as_modified():
    data = rows()
    data[0]["model_pins"]["weights_sha256"] = "f" * 64
    report = review(data)
    assert report["decision"] == "inconclusive" and "model_identity_mismatch" in report["issues"]


@pytest.mark.parametrize(
    "bad",
    [
        "headers",
        "missing_warmup",
        "missing_companion",
        "missing_rendered",
        "invalid_latency",
        "bool_usage",
    ],
)
def test_capture_and_warmup_provenance_is_required(bad):
    data = rows()
    sample = next(r for r in data if r["phase"] == 1)
    if bad == "headers":
        sample["headers"] = {}
    elif bad == "missing_warmup":
        data.remove(sample)
    elif bad == "missing_companion":
        sample["native_request"] = None
    elif bad == "missing_rendered":
        sample["decoded_prompt"] = None
    elif bad == "invalid_latency":
        sample["measurement"]["total_s"] = 0.01
    else:
        sample["measurement"]["prompt_tokens"] = True
    report = review(data)
    assert report["decision"] == "inconclusive" and report["issues"]


def test_window_clustering_keeps_correlated_slow_launches_in_interval():
    data = rows()
    for row in data:
        if row["window"] == 3 and row["phase"] in [1, 2]:
            row["measurement"]["total_s"] = 1.10
    report = review(list(reversed(data)))
    assert report["median_wall_ratio"] == pytest.approx(1.01)
    assert report["ci95"][1] >= 1.09
    assert report["decision"] == "inconclusive"


def test_cli_uses_pinned_design_and_exclusive_private_output(tmp_path, monkeypatch, capsys):
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps(rows()))
    design = tmp_path / "design.json"
    design.write_text(
        json.dumps(
            {
                "contract": "splash-overhead-design-2",
                "analysis": {"threshold": 1.05},
                "source_pins": {"source_sha256": "a" * 64, "adapter_sha256": "b" * 64},
                "model_pins": {
                    "weights_sha256": "c" * 64,
                    "tokenizer_sha256": "d" * 64,
                    "template_sha256": "e" * 64,
                },
            }
        )
    )
    out = tmp_path / "review.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit", "--samples", str(samples), "--design", str(design), "--out", str(out)],
    )
    assert audit.main() == 0
    saved = out.read_bytes()
    assert json.loads(saved)["decision"] == "eligible"
    assert audit.main() == 2 and out.read_bytes() == saved
    samples.write_text("private-invalid-response")
    assert audit.main() == 2
    assert "private-invalid-response" not in capsys.readouterr().out


def test_invalid_source_pin_or_design_never_claims_eligibility(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        audit.compare_overhead(
            [], source_sha256="not-a-pin", adapter_sha256="b" * 64, model_pins={}
        )
    design = tmp_path / "design.json"
    design.write_text('{"contract":"other","analysis":{"threshold":1.05}}')
    samples = tmp_path / "samples.json"
    samples.write_text("[]")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--samples",
            str(samples),
            "--design",
            str(design),
            "--out",
            str(tmp_path / "out.json"),
        ],
    )
    assert audit.main() == 2


def test_accepts_frozen_multiturn_category_inputs():
    data = rows()
    for row in data:
        if row["category"] == "2k":
            row["body"]["messages"][1:1] = [
                {"role": "user", "content": "Earlier question."},
                {"role": "assistant", "content": "Earlier answer."},
            ]
            row["body_sha256"] = bench.digest(row["body"])
    report = review(data)
    assert report["decision"] == "eligible" and report["paired_requests"] == 48


@pytest.mark.parametrize(
    "history",
    [
        [],
        [{"role": "assistant", "content": "Wrong first role."}],
        [{"role": "user", "content": "Question."}, {"role": "assistant", "content": "Answer."}],
        [{"role": "system", "content": "Injected system."}],
        [{"role": "tool", "content": "Tool result."}],
        [{"role": "user", "content": ""}],
        [{"role": "user", "content": "Question.", "name": "extra"}],
        [
            {"role": "user", "content": "First."},
            {"role": "user", "content": "Second."},
            {"role": "user", "content": "Third."},
        ],
    ],
)
def test_rejects_malformed_or_unfinished_conversation(history):
    row = rows()[0]
    row["body"]["messages"][1:] = history
    assert not audit._body_valid(audit.OverheadSample.model_validate(row))
