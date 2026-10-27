"""Frozen diagnostic contracts; no remote control and no raw public answers."""

from __future__ import annotations

import json
import statistics
from typing import Any, Literal

import bench
from pydantic import Field, ValidationError

EXPECTED: dict[str, Any] = {
    "inventory": 255,
    "schedule": 11,
    "trace": 6,
    "ordering": 3,
    "probability": "3/5",
    "allocation": 16,
    "nulls": [4, 3, 2, 9],
    "calendar": "Wednesday",
}
PROMPTS = {
    "inventory": "A shop buys 17 boxes of 12 items for 4 credits per item. It sells exactly three quarters of all items for 7 credits each. Unsold items have no credited value. What is revenue minus the cost of all purchased items?",
    "schedule": "Tasks run as soon as possible with unlimited workers. A takes 4 hours. B takes 3 hours and starts after A. C takes 5 hours and starts after A. D takes 2 hours and starts after both B and C. Starting A at time zero, when does D finish?",
    "trace": "Execute this Python: total=0; then for x in [2,5,1,4], add 2*x to total if x is even, otherwise subtract x. What is final total?",
    "ordering": "Alex, Blair and Casey each occupy one distinct day numbered 1,2,3. Alex is before Blair. Casey is not on day 3. Blair is not on day 2. Which day does Blair occupy?",
    "probability": "A bag has 3 red and 2 blue balls. Draw 2 uniformly without replacement. What is the probability of exactly one red? Give value as a reduced fraction string a/b.",
    "allocation": "Choose a subset of four indivisible items, at most one each: (weight,value)=(3,5),(4,7),(5,9),(6,10). Capacity is 9. What is the maximum total value?",
    "nulls": "SQL column v contains rows 2,NULL,2,5. COUNT(*) counts rows, COUNT(v) counts non-null values, COUNT(DISTINCT v) counts distinct non-null values, SUM(v) ignores NULL. Give [COUNT(*),COUNT(v),COUNT(DISTINCT v),SUM(v)] as value.",
    "calendar": "Day 0 is Monday. Days advance Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday and repeat. What weekday is day 100? Give the English weekday name as value.",
}


def cases() -> list[bench.Case]:
    result = [
        bench.Case(
            id="warmup",
            category="short",
            kind="fresh",
            max_tokens=32,
            messages=[{"role": "user", "content": 'Return exactly {"value":"READY"}.'}],
            primary=False,
        )
    ]
    for name, prompt in PROMPTS.items():
        result.append(
            bench.Case(
                id=name,
                category="other",
                kind="fresh",
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                        + ' Return only a JSON object with exactly one key "value" in your final answer.',
                    }
                ],
                primary=True,
            )
        )
    return result


def capture_issues(m: bench.Measurement, cap: int, deadline: float) -> list[str]:
    issues = []
    if m.error is not None:
        issues.append("capture_error")
    if (
        m.prompt_tokens is None
        or not 0 < m.prompt_tokens <= 32768 - cap
        or m.completion_tokens is None
        or not 0 < m.completion_tokens <= cap
        or m.cached_tokens != 0
    ):
        issues.append("invalid_usage_or_reuse")
    if m.reasoning_tokens is not None and (
        m.completion_tokens is None or not 0 <= m.reasoning_tokens <= m.completion_tokens
    ):
        issues.append("invalid_reasoning_count")
    first_answer = m.first_answer_s
    if first_answer is None and m.finish_reason == "length" and not m.answer:
        first_answer = m.total_s
    if (
        m.ttft_s is None
        or first_answer is None
        or m.total_s is None
        or not 0 < m.ttft_s <= first_answer <= m.total_s <= deadline
    ):
        issues.append("invalid_timestamps")
    return issues


def correct(m: bench.Measurement, expected: Any, mode: bool) -> bool:
    if m.finish_reason != "stop" or bool(m.reasoning.strip()) != mode:
        return False
    if not mode and m.reasoning_tokens not in (None, 0):
        return False

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate_key")
            result[k] = v
        return result

    try:
        answer = json.loads(m.answer, object_pairs_hook=unique)
        # Canonical bytes distinguish booleans from integers, including nested arrays.
        return bench.digest(answer) == bench.digest({"value": expected})
    except ValueError:
        return False


def reasoning_report(run: bench.Run, expected: bench.Manifest) -> dict[str, Any]:
    result: dict[str, Any] = {"gate": "inconclusive", "adoption_decision": "inconclusive"}
    schedule = [(c.id, 1, c.id == "warmup") for c in cases()]
    if (
        run.status != "complete"
        or run.error is not None
        or run.manifest.model_dump() != expected.model_dump()
        or [(s.case_id, s.rep, s.warmup) for s in run.samples] != schedule
        or [c.model_dump() for c in expected.cases] != [c.model_dump() for c in cases()]
    ):
        return result
    issues = [
        issue
        for s, c in zip(run.samples, expected.cases, strict=True)
        for issue in capture_issues(s.measurement, c.max_tokens, 30.0 if s.warmup else 240.0)
    ]
    if issues:
        result["issues"] = sorted(set(issues))
        return result
    mode = expected.settings.reasoning
    warmup_ok = correct(run.samples[0].measurement, "READY", False)
    measured = run.samples[1:]
    outcomes = [correct(s.measurement, EXPECTED[s.case_id], mode) for s in measured]
    result.update(
        gate="pass" if warmup_ok and all(outcomes) else "fail",
        correct=sum(outcomes),
        cases=len(measured),
        warmup_pass=warmup_ok,
        reasoning_token_coverage=sum(s.measurement.reasoning_tokens is not None for s in measured),
        failed_case_ids=[s.case_id for s, ok in zip(measured, outcomes, strict=True) if not ok],
        truncations=sum(s.measurement.finish_reason == "length" for s in measured),
    )
    for field in ["ttft_s", "first_answer_s", "total_s"]:
        values = [
            float(getattr(s.measurement, field))
            for s in measured
            if getattr(s.measurement, field) is not None
        ]
        result[field] = {
            "count": len(values),
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }
    result["reasoning_tokens"] = [s.measurement.reasoning_tokens for s in measured]
    return result


class Cycle(bench.StrictModel):
    index: int = Field(ge=0, le=3)
    generation: str = Field(min_length=1)
    ready_s: float = Field(gt=0)
    inference: bench.Measurement
    stop_s: float = Field(gt=0)
    generations_gone: bool
    listener_closed: bool
    baseline_available: int = Field(gt=0)
    reclaimed_available: list[int] = Field(min_length=3, max_length=3)
    reclaimed_offsets_s: list[float] = Field(min_length=3, max_length=3)


class Idle(bench.StrictModel):
    kind: Literal["automatic", "manual"]
    same_generation: bool
    observed_s: float = Field(ge=0)
    release_s: float = Field(ge=0)
    released: bool
    restoring: bool
    loaded_before: bool
    loaded_after: bool
    restores_before: int = Field(ge=0)
    restores_after: int = Field(ge=0)
    memory_before: int = Field(gt=0)
    memory_after: int = Field(ge=0)
    inference: bench.Measurement


class Lifecycle(bench.StrictModel):
    backend: Literal["mlx", "splash"]
    status: Literal["complete", "failed", "running"]
    cycles: list[Cycle]
    idle: Idle


def lifecycle_report(raw: Any, backend: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "gate": "inconclusive",
        "automatic_idle_gate": "unsupported" if backend == "mlx" else "inconclusive",
        "adoption_decision": "inconclusive",
    }
    try:
        d = Lifecycle.model_validate(raw)
    except ValidationError:
        return result
    if (
        d.status != "complete"
        or d.backend != backend
        or [c.index for c in d.cycles] != list(range(4))
        or len({c.generation for c in d.cycles}) != 4
        or d.idle.kind != ("automatic" if backend == "splash" else "manual")
    ):
        return result
    captures = [c.inference for c in d.cycles] + [d.idle.inference]
    if any(capture_issues(m, 128, 90.0) for m in captures):
        return result
    idle = d.idle
    idle_ok = (
        idle.same_generation
        and idle.released
        and not idle.restoring
        and idle.loaded_before
        and idle.loaded_after
        and idle.release_s <= 90
        and idle.observed_s >= (20 if backend == "splash" else 25)
        and idle.memory_after <= idle.memory_before / 2
        and (backend == "mlx" or idle.restores_after > idle.restores_before)
        and correct(idle.inference, "READY", False)
    )
    functional = idle_ok and all(
        c.ready_s <= 120
        and c.stop_s <= 90
        and c.generations_gone
        and c.listener_closed
        and correct(c.inference, "READY", False)
        for c in d.cycles
    )
    reclaim = all(
        all(
            v >= c.baseline_available - max(2 * 1024**3, c.baseline_available * 0.1)
            for v in c.reclaimed_available
        )
        and c.reclaimed_offsets_s[0] >= 0
        and c.reclaimed_offsets_s[-1] <= 90
        and all(
            a < b for a, b in zip(c.reclaimed_offsets_s, c.reclaimed_offsets_s[1:], strict=False)
        )
        and c.reclaimed_offsets_s[-1] - c.reclaimed_offsets_s[0] >= 4
        for c in d.cycles
    )
    result.update(
        gate="fail" if not functional else "pass" if reclaim else "inconclusive",
        automatic_idle_gate=("pass" if idle_ok else "fail")
        if backend == "splash"
        else "unsupported",
        functional_pass=functional,
        host_memory_recovered=reclaim,
        ready_s=[c.ready_s for c in d.cycles],
        stop_s=[c.stop_s for c in d.cycles],
        first_inference_s=[c.inference.total_s for c in d.cycles],
        idle_release_s=idle.release_s,
        resume_s=idle.inference.total_s,
        released_memory_fraction=1 - idle.memory_after / idle.memory_before,
    )
    return result
