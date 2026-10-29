"""Fresh native comparison tests use synthetic traces, never Mac performance data."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/splash"))
try:
    import bench
    import native_comparison as compare
finally:
    sys.path.pop(0)


def fixture():
    cases = [
        dict(
            id=c,
            category=c,
            kind="fresh",
            max_tokens=512 if c == "short" else 1024,
            messages=[{"role": "user", "content": f"Explain {c}."}],
            primary=True,
        )
        for c in ["short", "2k", "8k"]
    ]
    profiles = {}
    for backend in ["mlx", "splash"]:
        profiles[backend] = dict(
            schema_version=1,
            id=backend + "-source",
            session=1,
            order="mlx-first",
            backend=backend,
            base_url="http://127.0.0.1:18086",
            api_key_env=None,
            request_model="example/qwen",
            response_model="example/qwen",
            runtime={"version": "test-1", "sha256": "a" * 64},
            draft=(
                {"checkpoint": "example/draft", "revision": "frozen", "sha256": "b" * 64}
                if backend == "splash"
                else None
            ),
            model={
                "checkpoint": "example/qwen",
                "revision": "frozen",
                "weights_sha256": "c" * 64,
                "tokenizer_sha256": "d" * 64,
                "template_sha256": "e" * 64,
                "quantization": "4bit",
            },
            settings={"temperature": 0.0, "seed": 0, "reasoning": False},
            context_tokens=32768,
            kv_precision="bf16-kv-fp32-recurrent",
            cache_policy="isolated",
            concurrency=1,
            repeats=5,
            warmups=1,
            deadline_s=210.0,
            corpus_version="native-1",
            corpus_sha256=bench.digest(cases),
            cases=cases,
            rate_metric="backend_decode_tok_s",
            evidence={
                "preflight_sha256": "f" * 64,
                "cache_control": "isolated and reviewed",
                "native_timing_audit_sha256": "1" * 64,
            },
        )
    sources = {b: {"source_sha256": "2" * 64, "adapter_sha256": "3" * 64} for b in profiles}
    design = {
        "contract": "splash-native-comparison-design-1",
        "cohort": "modified-4bit",
        "boundary_audit_sha256": "1" * 64,
        "profiles": profiles,
        "sources": sources,
        "inputs": [],
    }
    proofs = {
        b: dict(
            cohort=design["cohort"],
            backend=b,
            evidence_sha256="4" * 64,
            source_sha256="2" * 64,
            adapter_sha256="3" * 64,
            runtime_sha256="a" * 64,
            model_pins={
                k: profiles[b]["model"][k]
                for k in ["weights_sha256", "tokenizer_sha256", "template_sha256"]
            },
            decision="eligible",
            threshold=1.05,
            median_wall_ratio=1.0,
            category_upper95={c: 1.01 for c in ["short", "2k", "8k"]},
        )
        for b in profiles
    }
    runs = []
    for session in range(1, 4):
        for backend in profiles:
            manifest = copy.deepcopy(profiles[backend])
            manifest.update(
                id=f"native-s{session}-{backend}",
                session=session,
                order="splash-first" if session == 2 else "mlx-first",
            )
            samples = []
            for rep in range(6):
                for case in cases:
                    nonce = bench.digest(
                        [
                            "splash-native-comparison-1",
                            design["cohort"],
                            "c" * 64,
                            manifest["corpus_sha256"],
                            session,
                            case["id"],
                            rep,
                        ]
                    )[:24]
                    body = bench.request_body(
                        bench.Manifest.model_validate(manifest),
                        bench.Case.model_validate(case),
                        rep,
                    )
                    body["messages"][0]["content"] = f"Benchmark instance {nonce}."
                    headers = {"X-APC-Tenant": nonce} if backend == "mlx" else {}
                    if backend == "splash":
                        body["cache_salt"] = nonce
                    prompt = f"Benchmark instance {nonce}. Explain {case['category']}."
                    count = {"short": 100, "2k": 2000, "8k": 8000}[case["category"]]
                    cap = case["max_tokens"]
                    request = (
                        {
                            "rendered_prompt": prompt,
                            "arguments": {
                                "max_tokens": cap,
                                "temperature": 0.0,
                                "seed": 0,
                                "enable_thinking": False,
                            },
                        }
                        if backend == "mlx"
                        else {
                            "prompt_tokens": [ord(c) for c in nonce]
                            + list(range(count - len(nonce))),
                            "sampling": {"temperature": 0.0},
                            "max_new_tokens": cap,
                            "thinking": False,
                            "seed": 0,
                        }
                    )
                    native_sha = (
                        hashlib.sha256(prompt.encode()).hexdigest()
                        if backend == "mlx"
                        else bench.digest(request["prompt_tokens"])
                    )
                    design["inputs"].append(
                        dict(
                            session=session,
                            backend=backend,
                            case_id=case["id"],
                            rep=rep,
                            nonce=nonce,
                            body=body,
                            headers=headers,
                            body_sha256=bench.digest(body),
                            native_prompt_sha256=native_sha,
                            decoded_prompt=prompt,
                            prompt_tokens=count,
                        )
                    )
                    first = 1 if backend == "mlx" else 7
                    rate = 20.0 if backend == "mlx" else 40.0
                    trace = dict(
                        contract="splash-native-emission-1",
                        backend=backend,
                        runtime_sha256="2" * 64,
                        adapter_sha256="3" * 64,
                        request_sha256=bench.digest(request),
                        source="engine_callback_entry",
                        clock="monotonic_ns",
                        completion_tokens=cap,
                        completion_ids_sha256=bench.digest(list(range(cap))),
                        finish_reason="length",
                        batches=[
                            {"at_ns": 100, "token_ids": list(range(first))},
                            {
                                "at_ns": 100 + int((cap - first) / rate * 1e9),
                                "token_ids": list(range(first, cap)),
                            },
                        ],
                    )
                    companion = dict(
                        contract="splash-native-request-1",
                        backend=backend,
                        request_sha256=bench.digest(request),
                        native_request=request,
                    )
                    m = bench.Measurement(
                        ttft_s=1.0,
                        first_answer_s=1.0,
                        total_s=80.0 if backend == "mlx" else 40.0,
                        prompt_tokens=count,
                        completion_tokens=cap,
                        cached_tokens=0,
                        finish_reason="length",
                        answer="synthetic",
                        backend_decode_tok_s=999.0,
                    )
                    samples.append(
                        dict(
                            case_id=case["id"],
                            rep=rep,
                            warmup=rep == 0,
                            body=body,
                            headers=headers,
                            measurement=m.model_dump(),
                            trace=trace,
                            companion=companion,
                        )
                    )
            runs.append(
                dict(
                    contract="splash-native-comparison-1",
                    cohort=design["cohort"],
                    manifest=manifest,
                    status="complete",
                    error=None,
                    samples=samples,
                )
            )
    return runs, design, proofs


def test_uses_explicit_batches_not_server_rate_and_keeps_adoption_pending():
    runs, design, proofs = fixture()
    before = copy.deepcopy(runs)
    r = compare.compare_native(runs, design, proofs)
    assert r["emission_speed_gate"] == "pass" and r["median_paired_ratio"] == pytest.approx(2.0)
    assert r["samples"] == {"measured": 90, "warmup": 18, "errors": 0, "early_stops": 0}
    assert r["accounted_native_traces"] == 108 and r["decision"] == "inconclusive"
    assert (
        r["ttft_gate"] == r["e2e_gate"] == "pass" and r["native_gpu_compute_gate"] == "unassessed"
    )
    assert r["ratio_ci95"] == pytest.approx([2.0, 2.0]) and runs == before
    text = json.dumps(r)
    assert "synthetic" not in text and "example/qwen" not in text


@pytest.mark.parametrize(
    "bad",
    [
        "missing_run",
        "duplicate_run",
        "missing_sample",
        "duplicate_sample",
        "error",
        "empty_error",
        "warmup",
        "warmup_error",
        "cap",
        "cache",
        "reasoning",
        "prompt",
        "body",
        "header",
        "trace_missing",
        "trace_ids",
        "source",
        "adapter",
        "companion",
        "elapsed",
        "input_sha",
        "decoded",
        "input_missing",
        "nonce",
        "settings",
        "runtime",
        "model",
        "order",
        "proof_missing",
        "proof_model",
        "proof_source",
        "proof_cohort",
        "proof_upper",
        "proof_category",
        "proof_nan",
        "proof_threshold",
    ],
)
def test_incomplete_or_incompatible_evidence_never_qualifies(bad):
    runs, design, proofs = fixture()
    s = runs[0]["samples"][3]
    if bad == "missing_run":
        runs.pop()
    elif bad == "duplicate_run":
        runs.append(copy.deepcopy(runs[0]))
    elif bad == "missing_sample":
        runs[0]["samples"].pop()
    elif bad == "duplicate_sample":
        runs[0]["samples"].append(copy.deepcopy(s))
    elif bad in ["error", "empty_error"]:
        s["measurement"]["error"] = "timeout" if bad == "error" else ""
    elif bad == "warmup":
        s["warmup"] = True
    elif bad == "warmup_error":
        runs[0]["samples"][0]["measurement"]["error"] = "timeout"
    elif bad == "cap":
        s["measurement"]["completion_tokens"] -= 1
    elif bad == "cache":
        s["measurement"]["cached_tokens"] = 1
    elif bad == "reasoning":
        s["measurement"]["reasoning"] = "thinking"
    elif bad == "prompt":
        s["measurement"]["prompt_tokens"] += 1
    elif bad == "body":
        s["body"]["seed"] = 1
    elif bad == "header":
        s["headers"] = {}
    elif bad == "trace_missing":
        s["trace"] = None
    elif bad == "trace_ids":
        s["trace"]["batches"][1]["token_ids"][-1] += 1
    elif bad == "source":
        s["trace"]["runtime_sha256"] = "7" * 64
    elif bad == "adapter":
        s["trace"]["adapter_sha256"] = "7" * 64
    elif bad == "companion":
        s["companion"]["native_request"]["arguments"]["seed"] = 1
    elif bad == "elapsed":
        s["measurement"]["total_s"] = 1.0
    elif bad == "input_sha":
        design["inputs"][3]["native_prompt_sha256"] = "7" * 64
    elif bad == "decoded":
        design["inputs"][3]["decoded_prompt"] = "different"
    elif bad == "input_missing":
        design["inputs"].pop()
    elif bad == "nonce":
        design["inputs"][3]["nonce"] = "different"
    elif bad == "settings":
        runs[0]["manifest"]["settings"]["reasoning"] = True
    elif bad == "runtime":
        runs[0]["manifest"]["runtime"]["sha256"] = "7" * 64
    elif bad == "model":
        runs[0]["manifest"]["model"]["weights_sha256"] = "7" * 64
    elif bad == "order":
        runs[1]["manifest"]["order"] = "splash-first"
    elif bad == "proof_missing":
        proofs.pop("mlx")
    elif bad == "proof_model":
        proofs["mlx"]["model_pins"]["weights_sha256"] = "7" * 64
    elif bad == "proof_source":
        proofs["mlx"]["source_sha256"] = "7" * 64
    elif bad == "proof_cohort":
        proofs["mlx"]["cohort"] = "stock-8bit"
    elif bad == "proof_upper":
        proofs["mlx"]["category_upper95"]["8k"] = 1.06
    elif bad == "proof_category":
        proofs["mlx"]["category_upper95"].pop("8k")
    elif bad == "proof_threshold":
        proofs["mlx"]["threshold"] = 1.10
    elif bad == "proof_nan":
        proofs["mlx"]["median_wall_ratio"] = float("nan")
    r = compare.compare_native(runs, design, proofs)
    assert (
        r["emission_speed_gate"] == "unassessed" and r["decision"] == "inconclusive" and r["issues"]
    )


def test_different_cross_engine_answers_are_allowed():
    runs, design, proofs = fixture()
    for r in runs:
        if r["manifest"]["backend"] == "splash":
            for s in r["samples"]:
                s["measurement"]["answer"] = "different valid answer"
    assert compare.compare_native(runs, design, proofs)["emission_speed_gate"] == "pass"


def test_boundary_overlap_is_inconclusive_without_extending_schedule():
    runs, design, proofs = fixture()
    for r in runs:
        if r["manifest"]["backend"] == "splash":
            ratio = {1: 1.1, 2: 1.2, 3: 1.4}[r["manifest"]["session"]]
            for s in r["samples"]:
                trace = s["trace"]
                remaining = trace["completion_tokens"] - len(trace["batches"][0]["token_ids"])
                trace["batches"][-1]["at_ns"] = 100 + int(remaining / (20 * ratio) * 1e9)
                s["measurement"]["total_s"] = 70.0
    r = compare.compare_native(runs, design, proofs)
    assert r["emission_speed_gate"] == "unassessed"
    assert r["median_paired_ratio"] == pytest.approx(1.2)
    assert r["ratio_ci95"][1] >= 1.25
    assert r["issues"] == ["confidence_interval_overlaps_boundary"]
    assert r["decision"] == "inconclusive"


def test_failed_measurement_gate_reports_failure_without_qualifying_adoption():
    runs, design, proofs = fixture()
    for run in runs:
        if run["manifest"]["backend"] == "splash":
            for sample in run["samples"]:
                sample["measurement"]["ttft_s"] = 2.0
                sample["measurement"]["first_answer_s"] = 2.0
    report = compare.compare_native(runs, design, proofs)
    assert report["emission_speed_gate"] == "pass"
    assert report["ttft_gate"] == "fail"
    assert report["decision"] == "fail"
    assert report["native_gpu_compute_gate"] == "unassessed"
