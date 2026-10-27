"""Fail-closed review of the separately frozen reasoning/lifecycle milestones."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops/splash-phase0"))
import bench  # noqa: E402
import reasoning_lifecycle as review  # noqa: E402


def profile(mode: bool = True) -> bench.Manifest:
    data = json.loads((ROOT / "ops/splash-phase0/manifest.example.json").read_text())
    data.update(
        id="reason-test",
        backend="mlx",
        draft=None,
        repeats=1,
        cache_policy="isolated",
        concurrency=1,
        context_tokens=32768,
        rate_metric="client_output_wall_tok_s",
        deadline_s=240.0,
    )
    data["settings"].update(reasoning=mode, temperature=0.0, seed=0)
    data["cases"] = [c.model_dump() for c in review.cases()]
    data["corpus_sha256"] = bench.digest(data["cases"])
    return bench.Manifest.model_validate(data)


def measurement(*, mode: bool = True, value: Any = 255) -> bench.Measurement:
    return bench.Measurement(
        ttft_s=0.1,
        first_answer_s=0.3,
        total_s=0.5,
        prompt_tokens=100,
        completion_tokens=30,
        cached_tokens=0,
        reasoning_tokens=10 if mode else None,
        finish_reason="stop",
        answer=json.dumps({"value": value}),
        reasoning="work" if mode else "",
    )


def run(mode: bool = True) -> bench.Run:
    p = profile(mode)
    samples = []
    for c in p.cases:
        m = measurement(mode=mode, value=review.EXPECTED.get(c.id, "READY"))
        if c.id == "warmup":
            m.reasoning = ""
            m.reasoning_tokens = None
        samples.append(bench.Sample(case_id=c.id, rep=1, warmup=c.id == "warmup", measurement=m))
    return bench.Run(manifest=p, status="complete", samples=samples)


def test_complete_reasoning_and_off() -> None:
    for mode in [False, True]:
        r = run(mode)
        result = review.reasoning_report(r, r.manifest)
        assert result["gate"] == "pass"
        assert result["correct"] == 8
        assert result["reasoning_token_coverage"] == (8 if mode else 0)
        assert "work" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "reorder", "failed", "error", "manifest", "warmup", "rep"]
)
def test_incomplete_reasoning(mutation: str) -> None:
    r = run()
    expected = r.manifest.model_copy(deep=True)
    if mutation == "missing":
        r.samples.pop()
    elif mutation == "duplicate":
        r.samples[-1] = r.samples[1]
    elif mutation == "reorder":
        r.samples.reverse()
    elif mutation == "failed":
        r.status = "failed"
    elif mutation == "error":
        r.error = ""
    elif mutation == "manifest":
        r.manifest.session += 1
    elif mutation == "warmup":
        r.samples[1].warmup = True
    else:
        r.samples[1].rep = 2
    assert review.reasoning_report(r, expected)["gate"] == "inconclusive"


@pytest.mark.parametrize(
    "field,value",
    [
        ("error", ""),
        ("cached_tokens", 1),
        ("cached_tokens", None),
        ("prompt_tokens", 0),
        ("completion_tokens", 0),
        ("completion_tokens", 2049),
        ("ttft_s", None),
        ("ttft_s", -1.0),
        ("first_answer_s", 0.6),
        ("total_s", None),
        ("reasoning_tokens", 31),
        ("reasoning_tokens", -1),
    ],
)
def test_bad_capture_is_inconclusive(field: str, value: Any) -> None:
    r = run()
    setattr(r.samples[1].measurement, field, value)
    assert review.reasoning_report(r, r.manifest)["gate"] == "inconclusive"


@pytest.mark.parametrize(
    "answer",
    [
        "{}",
        '{"value":255,"extra":1}',
        '{"value":255,"value":255}',
        '{"value":true}',
        '```json\n{"value":255}\n```',
        '{"value":0}',
    ],
)
def test_wrong_answers_fail(answer: str) -> None:
    r = run()
    r.samples[1].measurement.answer = answer
    assert review.reasoning_report(r, r.manifest)["gate"] == "fail"


@pytest.mark.parametrize(
    "mode,reasoning,tokens", [(True, "", 0), (False, "unexpected", 1), (False, "", 1)]
)
def test_mode_must_take_effect(mode: bool, reasoning: str, tokens: int) -> None:
    r = run(mode)
    r.samples[1].measurement.reasoning = reasoning
    r.samples[1].measurement.reasoning_tokens = tokens
    assert review.reasoning_report(r, r.manifest)["gate"] == "fail"


def test_truncation_and_wrong_warmup_fail() -> None:
    r = run()
    r.samples[1].measurement.finish_reason = "length"
    assert review.reasoning_report(r, r.manifest)["gate"] == "fail"
    r = run()
    r.samples[0].measurement.answer = '{"value":"wrong"}'
    assert review.reasoning_report(r, r.manifest)["gate"] == "fail"


def lifecycle(backend: str = "splash") -> dict[str, Any]:
    cycles = []
    for i in range(4):
        cycles.append(
            dict(
                index=i,
                generation=f"g{i}",
                ready_s=10.0,
                inference=measurement(mode=False, value="READY").model_dump(),
                stop_s=2.0,
                generations_gone=True,
                listener_closed=True,
                baseline_available=20 * 1024**3,
                reclaimed_available=[19 * 1024**3] * 3,
                reclaimed_offsets_s=[2.0, 4.0, 6.0],
            )
        )
    return dict(
        backend=backend,
        status="complete",
        cycles=cycles,
        idle=dict(
            kind="automatic" if backend == "splash" else "manual",
            same_generation=True,
            observed_s=21.0 if backend == "splash" else 25.0,
            release_s=22.0,
            released=True,
            restoring=False,
            loaded_before=True,
            loaded_after=True,
            restores_before=0,
            restores_after=1,
            memory_before=30 * 1024**3,
            memory_after=3 * 1024**3,
            inference=measurement(mode=False, value="READY").model_dump(),
        ),
    )


def test_lifecycle_splash_and_mlx() -> None:
    for backend in ["mlx", "splash"]:
        result = review.lifecycle_report(lifecycle(backend), backend)
        assert result["gate"] == "pass"
        assert result["automatic_idle_gate"] == ("unsupported" if backend == "mlx" else "pass")


@pytest.mark.parametrize(
    "mutation", ["missing", "generation", "order", "failed", "backend", "no_idle", "wrong_kind"]
)
def test_lifecycle_incomplete(mutation: str) -> None:
    d = lifecycle()
    if mutation == "missing":
        d["cycles"].pop()
    elif mutation == "generation":
        d["cycles"][1]["generation"] = "g0"
    elif mutation == "order":
        d["cycles"].reverse()
    elif mutation == "failed":
        d["status"] = "failed"
    elif mutation == "backend":
        d["backend"] = "mlx"
    elif mutation == "no_idle":
        d.pop("idle")
    else:
        d["idle"]["kind"] = "manual"
    assert review.lifecycle_report(d, "splash")["gate"] == "inconclusive"


@pytest.mark.parametrize(
    "field,value",
    [("ready_s", 121.0), ("stop_s", 91.0), ("generations_gone", False), ("listener_closed", False)],
)
def test_lifecycle_failure(field: str, value: Any) -> None:
    d = lifecycle()
    d["cycles"][0][field] = value
    assert review.lifecycle_report(d, "splash")["gate"] == "fail"


@pytest.mark.parametrize(
    "field,value",
    [
        ("released", False),
        ("restoring", True),
        ("loaded_before", False),
        ("loaded_after", False),
        ("same_generation", False),
        ("observed_s", 19.0),
        ("release_s", 91.0),
        ("memory_after", 20 * 1024**3),
        ("restores_after", 0),
    ],
)
def test_idle_failure(field: str, value: Any) -> None:
    d = lifecycle()
    d["idle"][field] = value
    assert review.lifecycle_report(d, "splash")["gate"] == "fail"


def test_shared_host_memory_inconclusive() -> None:
    d = lifecycle()
    d["cycles"][0]["reclaimed_available"] = [1, 1, 1]
    assert review.lifecycle_report(d, "splash")["gate"] == "inconclusive"
    d = lifecycle()
    d["cycles"][0]["reclaimed_offsets_s"] = [2.0, 3.0, 4.0]
    assert review.lifecycle_report(d, "splash")["gate"] == "inconclusive"


@pytest.mark.parametrize("value", [None, {}, {"backend": "splash"}, {"cycles": "secret"}])
def test_malformed_lifecycle_is_safe(value: Any) -> None:
    result = review.lifecycle_report(value, "splash")
    assert result["gate"] == "inconclusive"
    assert "secret" not in json.dumps(result)


def test_invalid_lifecycle_capture_is_inconclusive() -> None:
    d = lifecycle()
    d["cycles"][0]["inference"]["cached_tokens"] = 1
    assert review.lifecycle_report(d, "splash")["gate"] == "inconclusive"


def test_reasoning_only_truncation_is_a_measured_failure() -> None:
    r = run()
    m = r.samples[1].measurement
    m.finish_reason = "length"
    m.answer = ""
    m.first_answer_s = None
    assert not review.capture_issues(m, 2048, 240.0)
    report = review.reasoning_report(r, r.manifest)
    assert report["gate"] == "fail"
    assert report["first_answer_s"]["count"] == 7


def test_input_output_budget_must_fit_context() -> None:
    r = run()
    r.samples[1].measurement.prompt_tokens = 32700
    assert review.reasoning_report(r, r.manifest)["gate"] == "inconclusive"
