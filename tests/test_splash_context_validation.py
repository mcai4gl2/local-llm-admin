"""Synthetic near-limit review; no model or service is loaded by these tests."""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

from tests.test_splash_phase0 import manifest_data as manifest_data

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/splash"))
try:
    import bench
    import context_inputs as inputs
    import context_validation as context
finally:
    sys.path.pop(0)


def manifest(data, backend="mlx"):
    source = copy.deepcopy(data)
    source.update(
        backend=backend,
        cache_policy="isolated",
        rate_metric="client_output_wall_tok_s",
        draft=None
        if backend == "mlx"
        else {"checkpoint": "example/draft", "revision": "frozen", "sha256": "f" * 64},
    )
    profile = context.make_profile(
        bench.Manifest.model_validate(source), "stock-8bit", 301, [1000] * 6
    )
    cases = []
    for case in profile.cases:
        position, seed = map(int, case.id.split("-")[1:])
        cases.append(
            {
                "case_id": case.id,
                "position": position,
                "seed": seed,
                "records": 1000,
                "expected_value": inputs.expected_value(position, seed),
                "rendered_prompt_tokens": 31800,
                "needle_fraction": position / 100,
                "body_sha256": bench.digest(bench.request_body(profile, case, 1)),
            }
        )
    return context.ContextManifest.model_validate(
        {
            "contract": "splash-context-1",
            "cohort": "stock-8bit",
            "profile": profile.model_dump(),
            "cases": cases,
            "tokenization_receipt_sha256": "e" * 64,
            "reviewed_preflight_sha256": "a" * 64,
            "reviewed_at": time.time(),
        }
    )


def run(data, backend="mlx"):
    m = manifest(data, backend)
    return context.ContextRun(
        manifest=m,
        status="complete",
        warmup=bench.Measurement(
            prompt_tokens=30,
            completion_tokens=64,
            cached_tokens=0,
            finish_reason="length",
            ttft_s=1,
            total_s=2,
            answer="synthetic warmup",
        ),
        samples=[
            context.ContextSample(
                case_id=c.case_id,
                measurement=bench.Measurement(
                    prompt_tokens=c.rendered_prompt_tokens,
                    completion_tokens=20,
                    cached_tokens=0,
                    finish_reason="stop",
                    ttft_s=10,
                    total_s=12,
                    answer=json.dumps({"value": c.expected_value}),
                ),
            )
            for c in m.cases
        ],
    )


def test_generator_is_deterministic_distinct_bounded_and_places_one_value():
    messages = [inputs.make_messages(p, s, 1000) for p in [10, 50, 90] for s in [0, 1]]
    assert len({json.dumps(m) for m in messages}) == 6
    assert messages[0] == inputs.make_messages(10, 0, 1000)
    for m, (p, s) in zip(messages, [(p, s) for p in [10, 50, 90] for s in [0, 1]], strict=True):
        text = m[0]["content"]
        assert text.count(inputs.expected_value(p, s)) == 1
        fraction = text.index(inputs.expected_value(p, s)) / len(text)
        assert abs(fraction - p / 100) < 0.03


@pytest.mark.parametrize(
    "args",
    [(True, 0, 1000), (10, True, 1000), (0, 0, 1000), (10, 2, 1000), (10, 0, 10), (10, 0, 5000)],
)
def test_generator_refuses_unbounded_or_invalid_inputs(args):
    with pytest.raises(ValueError):
        inputs.make_messages(*args)


def test_full_context_pair_is_only_a_fixed_context_smoke(manifest_data):
    runs = [run(manifest_data, b) for b in ["mlx", "splash"]]
    result = context.compare_context(runs)
    assert result["decision"] == "pass" and result["cases_per_engine"] == 6
    assert result["native_gate"] == "unassessed" and result["quality_gate"] == "unassessed"
    assert result["prompt_token_band"] == [31744, 32000]
    assert result["scope"] == "fixed_synthetic_context_smoke_only"
    assert "synthetic warmup" not in json.dumps(result) and "result-" not in json.dumps(result)
    a, b = runs[0].manifest.profile, runs[1].manifest.profile
    assert (
        bench.request_headers(a, a.cases[0], 1)["X-APC-Tenant"]
        == bench.request_body(b, b.cases[0], 1)["cache_salt"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        "band",
        "overflow",
        "fraction",
        "records",
        "body",
        "expected",
        "bool",
        "missing",
        "duplicate",
        "thinking",
        "deadline",
        "cache",
        "concurrency",
    ],
)
def test_manifest_refuses_unreviewed_context_claims(manifest_data, bad):
    d = manifest(manifest_data).model_dump()
    if bad == "band":
        d["cases"][0]["rendered_prompt_tokens"] = 31743
    elif bad == "overflow":
        d["cases"][0]["rendered_prompt_tokens"] = 32768
    elif bad == "fraction":
        d["cases"][0]["needle_fraction"] = 0.5
    elif bad == "records":
        d["cases"][0]["records"] = 900
    elif bad == "body":
        d["cases"][0]["body_sha256"] = "f" * 64
    elif bad == "expected":
        d["cases"][0]["expected_value"] = "fabricated"
    elif bad == "bool":
        d["cases"][0]["seed"] = False
    elif bad == "missing":
        d["cases"].pop()
    elif bad == "duplicate":
        d["cases"][1] = copy.deepcopy(d["cases"][0])
    elif bad == "thinking":
        d["profile"]["settings"]["reasoning"] = True
    elif bad == "deadline":
        d["profile"]["deadline_s"] = 301.0
    elif bad == "cache":
        d["profile"]["cache_policy"] = "disabled"
    elif bad == "concurrency":
        d["profile"]["concurrency"] = 2
    with pytest.raises(ValueError):
        context.ContextManifest.model_validate(d)


@pytest.mark.parametrize(
    "bad",
    [
        "timeout",
        "reuse",
        "prompt",
        "count",
        "early",
        "no_usage",
        "reasoning",
        "ttft",
        "total",
        "warmup",
        "duplicate",
        "missing",
        "status",
    ],
)
def test_capture_errors_never_disappear(manifest_data, bad):
    a, b = run(manifest_data), run(manifest_data, "splash")
    m = a.samples[0].measurement
    if bad == "timeout":
        m.error = "timeout"
    elif bad == "reuse":
        m.cached_tokens = 1
    elif bad == "prompt":
        m.prompt_tokens = 31801
    elif bad == "count":
        m.completion_tokens = 513
    elif bad == "early":
        m.finish_reason = "length"
    elif bad == "no_usage":
        m.prompt_tokens = None
    elif bad == "reasoning":
        m.reasoning = "unrequested thought"
    elif bad == "ttft":
        m.ttft_s = 0
    elif bad == "total":
        m.total_s = 1
    elif bad == "warmup":
        a.warmup.error = "timeout"
    elif bad == "duplicate":
        a.samples.append(a.samples[0])
    elif bad == "missing":
        a.samples.pop()
    elif bad == "status":
        a.status = "failed"
    result = context.compare_context([a, b])
    assert result["decision"] == "inconclusive" and result["issues"]


@pytest.mark.parametrize(
    "answer", ['{"value":"wrong"}', "```json\n{}\n```", "{}", '{"value":"x","value":"y"}', "[]"]
)
def test_wrong_or_nonexact_json_is_a_visible_correctness_failure(manifest_data, answer):
    a, b = run(manifest_data), run(manifest_data, "splash")
    a.samples[0].measurement.answer = answer
    result = context.compare_context([a, b])
    assert result["decision"] == "fail"
    assert result["engines"]["mlx"]["correct"] == 5


@pytest.mark.parametrize("bad", ["backend", "cohort", "weights", "tokens"])
def test_pairing_never_substitutes_a_checkpoint_or_rendering(manifest_data, bad):
    a, b = run(manifest_data), run(manifest_data, "splash")
    if bad == "backend":
        b = a
    elif bad == "cohort":
        b.manifest.cohort = "modified-4bit"
    elif bad == "weights":
        b.manifest.profile.model.weights_sha256 = "f" * 64
    elif bad == "tokens":
        b.manifest.cases[0].rendered_prompt_tokens = 31801
    assert context.compare_context([a, b])["decision"] == "inconclusive"


def test_capture_requires_a_fresh_explicit_review(manifest_data):
    m = manifest(manifest_data)
    context.require_reviewed(m, now=m.reviewed_at)
    for at in [m.reviewed_at - 31, m.reviewed_at + 901]:
        with pytest.raises(ValueError):
            context.require_reviewed(m, now=at)
    m.reviewed_preflight_sha256 = None
    m.reviewed_at = None
    with pytest.raises(ValueError):
        context.require_reviewed(m, now=time.time())


def test_duplicate_expected_json_keys_are_refused(manifest_data):
    a, b = run(manifest_data), run(manifest_data, "splash")
    value = a.manifest.cases[0].expected_value
    a.samples[0].measurement.answer = '{"value":"' + value + '","value":"' + value + '"}'
    assert context.compare_context([a, b])["decision"] == "fail"


@pytest.mark.parametrize("bad", ["unknown", "warmup", "run_error", "empty_error"])
def test_remaining_failure_markers_are_visible(manifest_data, bad):
    a, b = run(manifest_data), run(manifest_data, "splash")
    if bad == "unknown":
        a.samples[0].case_id = "unknown"
    elif bad == "warmup":
        a.warmup = None
    elif bad == "run_error":
        a.error = "private response content"
    else:
        a.samples[0].measurement.error = ""
    result = context.compare_context([a, b])
    assert result["decision"] == "inconclusive"
    assert "private response content" not in json.dumps(result)


def test_profile_builder_refuses_unbounded_campaign_identity(manifest_data):
    source = bench.Manifest.model_validate(manifest_data)
    for cohort, series, counts in [
        ("other", 301, [1000] * 6),
        ("stock-8bit", True, [1000] * 6),
        ("stock-8bit", 0, [1000] * 6),
        ("stock-8bit", 301, [1000] * 5),
    ]:
        with pytest.raises(ValueError):
            context.make_profile(source, cohort, series, counts)


def test_review_cli_private_outputs_are_exclusive_and_errors_are_sanitized(
    manifest_data, tmp_path, monkeypatch, capsys
):
    files = []
    for backend in ["mlx", "splash"]:
        path = tmp_path / (backend + ".json")
        path.write_text(run(manifest_data, backend).model_dump_json())
        files.append(path)
    out = tmp_path / "review.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["context_validation.py", "--runs", *[str(p) for p in files], "--out", str(out)],
    )
    assert context.main() == 0
    assert json.loads(out.read_text())["decision"] == "pass"
    saved = out.read_bytes()
    assert context.main() == 2 and out.read_bytes() == saved
    files[0].write_text("private-invalid-input")
    assert context.main() == 2
    assert "private-invalid-input" not in capsys.readouterr().out


def test_nonfinite_capture_is_an_invalid_artifact(manifest_data):
    a, b = run(manifest_data), run(manifest_data, "splash")
    a.samples[0].measurement.ttft_s = float("nan")
    result = context.compare_context([a, b])
    assert result["decision"] == "inconclusive" and result["issues"] == ["invalid_artifact"]
