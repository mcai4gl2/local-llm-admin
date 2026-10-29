"""Replay qualification must prove frozen histories, real reuse and correct replies."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.test_splash_phase0 import manifest_data as manifest_data

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/splash"))
try:
    spec = importlib.util.spec_from_file_location(
        "splash_replay_validation", ROOT / "experiments/splash/replay_validation.py"
    )
    assert spec and spec.loader
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
finally:
    sys.path.pop(0)

bench = replay.bench


def frozen(data, backend="mlx", context="8k"):
    source = bench.Manifest.model_validate(data)
    if backend == "splash":
        raw = source.model_dump()
        raw.update(
            backend="splash",
            draft={"checkpoint": "draft", "revision": "pinned", "sha256": "a" * 64},
        )
        source = bench.Manifest.model_validate(raw)
    profile = replay.make_profile(source, "stock-8bit", context, 801, [400, 400, 400])
    requests = []
    for case in profile.cases:
        for step in replay.STEPS:
            body, headers = replay.request(profile, case, step)
            requests.append(
                replay.ReplayRequest(
                    case_id=case.id,
                    step=step,
                    body=body,
                    headers=headers,
                    body_sha256=bench.digest(body),
                    prompt_tokens=(8000 if context == "8k" else 31850)
                    + (40 if step == "extend" else 0),
                    shared_prefix_tokens=0
                    if step == "prime"
                    else (
                        (8000 if context == "8k" else 31850)
                        if step == "repeat"
                        else (7990 if context == "8k" else 31840)
                    ),
                )
            )
    body, headers = replay.warmup_request(profile)
    return replay.ReplayManifest(
        contract="splash-replay-1",
        cohort="stock-8bit",
        context=context,
        profile=profile,
        records=[400, 400, 400],
        requests=requests,
        warmup_body=body,
        warmup_headers=headers,
        warmup_prompt_tokens=30,
        tokenization_receipt_sha256="b" * 64,
        reviewed_at=1000.0,
        reviewed_preflight_sha256="c" * 64,
    )


def measurement(m, req=None):
    value = "READY" if req is None else json.dumps({"value": replay.expected(req.case_id)})
    return bench.Measurement(
        ttft_s=1.0,
        first_answer_s=1.0,
        total_s=2.0,
        prompt_tokens=m.warmup_prompt_tokens if req is None else req.prompt_tokens,
        completion_tokens=8,
        cached_tokens=0 if req is None or req.step == "prime" else req.shared_prefix_tokens,
        reasoning_tokens=0,
        finish_reason="stop",
        answer=value,
    )


def complete(m):
    return replay.ReplayRun(
        manifest=m,
        status="complete",
        started_at=1001.0,
        warmup=measurement(m),
        samples=[
            replay.ReplaySample(
                case_id=q.case_id,
                step=q.step,
                body_sha256=q.body_sha256,
                headers_sha256=bench.digest(q.headers),
                measurement=measurement(m, q),
            )
            for q in m.requests
        ],
    )


def test_frozen_request_schedule_has_real_namespaces_and_fixed_history(manifest_data):
    m = frozen(manifest_data)
    assert len(m.requests) == 9
    assert [r.step for r in m.requests] == list(replay.STEPS) * 3
    for a, b, c in zip(m.requests[::3], m.requests[1::3], m.requests[2::3], strict=True):
        assert a.body == b.body
        assert a.headers == b.headers == c.headers
        assert c.body["messages"][:-2] == a.body["messages"]
        assert json.loads(c.body["messages"][-2]["content"]) == {
            "value": replay.expected(a.case_id)
        }
    assert len({r.headers["X-APC-Tenant"] for r in m.requests}) == 3
    splash = frozen(manifest_data, "splash")
    assert all(not r.headers for r in splash.requests)
    assert len({r.body["cache_salt"] for r in splash.requests}) == 3
    assert m.profile.comparison_key() == splash.profile.comparison_key()


@pytest.mark.parametrize(
    "bad",
    [
        "order",
        "missing",
        "duplicate",
        "body",
        "headers",
        "hash",
        "history",
        "band",
        "prefix",
        "settings",
        "records",
    ],
)
def test_manifest_refuses_changed_frozen_conditions(manifest_data, bad):
    raw = frozen(manifest_data).model_dump()
    if bad == "order":
        raw["requests"].reverse()
    elif bad == "missing":
        raw["requests"].pop()
    elif bad == "duplicate":
        raw["requests"][1] = copy.deepcopy(raw["requests"][0])
    elif bad == "body":
        raw["requests"][0]["body"]["max_tokens"] = 256
    elif bad == "headers":
        raw["requests"][1]["headers"] = {}
    elif bad == "hash":
        raw["requests"][0]["body_sha256"] = "0" * 64
    elif bad == "history":
        raw["requests"][2]["body"]["messages"][-2]["content"] = "different"
    elif bad == "band":
        raw["requests"][0]["prompt_tokens"] = 6000
    elif bad == "prefix":
        raw["requests"][1]["shared_prefix_tokens"] = 9000
    elif bad == "settings":
        raw["profile"]["settings"]["reasoning"] = True
    else:
        raw["records"][0] += 1
    with pytest.raises(ValueError):
        replay.ReplayManifest.model_validate(raw)


@pytest.mark.parametrize(
    "bad",
    [
        "error",
        "empty_error",
        "wrong",
        "extra_json",
        "duplicate_json",
        "missing_cache",
        "cold_cache",
        "small_reuse",
        "over_cache",
        "count",
        "truncate",
        "reasoning",
        "time",
        "missing_answer_time",
    ],
)
def test_capture_rejects_bad_accounting_or_correctness(manifest_data, bad):
    m = frozen(manifest_data)
    q = m.requests[0 if bad == "cold_cache" else 1]
    v = measurement(m, q)
    if bad == "error":
        v.error = "timeout"
    elif bad == "empty_error":
        v.error = ""
    elif bad == "wrong":
        v.answer = '{"value":"wrong"}'
    elif bad == "extra_json":
        v.answer = json.dumps({"value": replay.expected(q.case_id), "extra": True})
    elif bad == "duplicate_json":
        v.answer = '{"value":"wrong","value":' + json.dumps(replay.expected(q.case_id)) + "}"
    elif bad == "missing_cache":
        v.cached_tokens = None
    elif bad == "cold_cache":
        v.cached_tokens = 1
    elif bad == "small_reuse":
        v.cached_tokens = 1
    elif bad == "over_cache":
        v.cached_tokens = q.prompt_tokens + 1
    elif bad == "count":
        v.prompt_tokens += 1
    elif bad == "truncate":
        v.finish_reason = "length"
    elif bad == "reasoning":
        v.reasoning = "hidden reasoning"
    elif bad == "time":
        v.ttft_s = 3.0
    else:
        v.first_answer_s = None
    assert replay.capture_issues(m, v, q)


def test_pair_pass_is_scoped_and_contains_no_private_text(manifest_data):
    expected = [frozen(manifest_data, backend) for backend in ("mlx", "splash")]
    result = replay.compare_runs([complete(m) for m in expected], expected)
    assert result["replay_gate"] == "pass"
    assert result["adoption_decision"] == "inconclusive"
    assert result["long_requests"] == 18 and result["cached_requests"] == 12
    text = json.dumps(result)
    assert "messages" not in text and "READY" not in text and "result-" not in text
    assert "backend_decode_tok_s" not in text


@pytest.mark.parametrize(
    "bad",
    [
        "partial",
        "missing",
        "duplicate",
        "reorder",
        "body",
        "header",
        "profile",
        "missing_backend",
        "missing_warmup",
        "stale_review",
        "expired_review",
        "future_review",
    ],
)
def test_incompatible_or_incomplete_runs_never_pass(manifest_data, bad):
    expected = [frozen(manifest_data, backend) for backend in ("mlx", "splash")]
    runs = [complete(m.model_copy(deep=True)) for m in expected]
    if bad == "partial":
        runs[0].status = "interrupted"
    elif bad == "missing":
        runs[0].samples.pop()
    elif bad == "duplicate":
        runs[0].samples[1] = runs[0].samples[0]
    elif bad == "reorder":
        runs[0].samples.reverse()
    elif bad == "body":
        runs[0].samples[0].body_sha256 = "0" * 64
    elif bad == "header":
        runs[0].samples[0].headers_sha256 = "0" * 64
    elif bad == "profile":
        runs[0].manifest.profile.model.weights_sha256 = "0" * 64
    elif bad == "missing_backend":
        runs.pop()
    elif bad == "missing_warmup":
        runs[0].warmup = None
    elif bad == "expired_review":
        runs[0].started_at = 2000.0
    elif bad == "future_review":
        runs[0].started_at = 900.0
    else:
        runs[0].manifest.reviewed_at = None
        runs[0].manifest.reviewed_preflight_sha256 = None
    assert replay.compare_runs(runs, expected)["replay_gate"] != "pass"


def test_failed_samples_are_retained_and_fail_scoped_gate(manifest_data):
    expected = [frozen(manifest_data, backend) for backend in ("mlx", "splash")]
    runs = [complete(m) for m in expected]
    runs[0].samples[1].measurement.cached_tokens = 0
    assert replay.compare_runs(runs, expected)["replay_gate"] == "fail"


def test_review_requires_fresh_proof(manifest_data):
    m = frozen(manifest_data)
    replay.require_reviewed(m, now=1100)
    with pytest.raises(ValueError):
        replay.require_reviewed(m, now=2000)


@pytest.mark.parametrize("backend", ["mlx", "splash"])
def test_32k_profile_and_exact_reuse_threshold(manifest_data, backend):
    m = frozen(manifest_data, backend, "32k")
    q = m.requests[1]
    v = measurement(m, q)
    v.cached_tokens = (q.shared_prefix_tokens * 4 + 4) // 5
    assert not replay.capture_issues(m, v, q)
    v.cached_tokens -= 1
    assert "insufficient_shared_prefix_reuse" in replay.capture_issues(m, v, q)


@pytest.mark.parametrize("bad", ["error", "wrong", "cache", "reasoning", "cap"])
def test_bad_warmup_fails_before_long_requests(manifest_data, bad):
    m = frozen(manifest_data)
    v = measurement(m)
    if bad == "error":
        v.error = "timeout"
    elif bad == "wrong":
        v.answer = "wrong"
    elif bad == "cache":
        v.cached_tokens = 1
    elif bad == "reasoning":
        v.reasoning_tokens = 1
    else:
        v.completion_tokens = 33
    assert replay.capture_issues(m, v)


def test_incompatible_expected_models_and_extraneous_runs_do_not_pass(manifest_data):
    expected_manifests = [frozen(manifest_data, b) for b in ("mlx", "splash")]
    runs = [complete(m) for m in expected_manifests]
    assert (
        replay.compare_runs(runs + [runs[0]], expected_manifests)["replay_gate"] == "inconclusive"
    )
    assert replay.compare_runs(runs, expected_manifests[:1])["replay_gate"] == "inconclusive"
    other = copy.deepcopy(manifest_data)
    other["model"]["weights_sha256"] = "0" * 64
    assert (
        replay.compare_runs(runs, [expected_manifests[0], frozen(other, "splash")])["replay_gate"]
        == "inconclusive"
    )


def test_partial_run_with_visible_error_cannot_hide_failure(manifest_data):
    expected_manifests = [frozen(manifest_data, b) for b in ("mlx", "splash")]
    runs = [complete(m) for m in expected_manifests]
    runs[0].status = "failed"
    runs[0].error = "REDACTED_TEST_PAYLOAD"
    runs[0].samples = runs[0].samples[:1]
    report = replay.compare_runs(runs, expected_manifests)
    assert report["replay_gate"] == "fail"
    assert "REDACTED_TEST_PAYLOAD" not in json.dumps(report)


def test_invalid_identity_or_step_is_refused(manifest_data):
    m = frozen(manifest_data)
    with pytest.raises(ValueError):
        replay.expected("unknown")
    with pytest.raises(ValueError):
        replay.request(m.profile, m.profile.cases[0], "unknown")
    with pytest.raises(ValueError):
        replay.make_profile(m.profile, "unknown", "8k", 801, [400] * 3)


@pytest.mark.parametrize("bad", ["half_proof", "warmup_body", "warmup_headers"])
def test_incomplete_review_or_changed_warmup_is_invalid(manifest_data, bad):
    raw = frozen(manifest_data).model_dump()
    if bad == "half_proof":
        raw["reviewed_at"] = None
    elif bad == "warmup_body":
        raw["warmup_body"]["max_tokens"] = 64
    else:
        raw["warmup_headers"] = {}
    with pytest.raises(ValueError):
        replay.ReplayManifest.model_validate(raw)


def test_valid_but_different_tokenization_receipt_is_incompatible(manifest_data):
    expected_manifests = [frozen(manifest_data, b) for b in ("mlx", "splash")]
    runs = [complete(m.model_copy(deep=True)) for m in expected_manifests]
    runs[0].manifest.tokenization_receipt_sha256 = "0" * 64
    assert "frozen_manifest_mismatch" in replay.compare_runs(runs, expected_manifests)["issues"]
