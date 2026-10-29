#!/usr/bin/env python3
"""Near-limit synthetic context inputs and strict private artifact review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal, Self

import bench
import context_inputs as inputs
from pydantic import Field, model_validator


class ContextCase(bench.StrictModel):
    case_id: bench.Identifier
    position: int
    seed: int
    records: int = Field(ge=64, le=4096)
    expected_value: str
    rendered_prompt_tokens: int = Field(ge=31744, le=32000)
    needle_fraction: float = Field(ge=0, le=1)
    body_sha256: bench.SHA256


def make_profile(
    source: bench.Manifest, cohort: str, series: int, counts: list[int]
) -> bench.Manifest:
    source = bench.Manifest.model_validate(source.model_dump())
    if (
        cohort not in {"stock-8bit", "modified-4bit"}
        or type(series) is not int
        or series <= 0
        or len(counts) != 6
    ):
        raise ValueError("Invalid context campaign identity or case count.")
    cases = []
    for (position, seed), records in zip(
        [(p, s) for p in inputs.POSITIONS for s in inputs.SEEDS], counts, strict=True
    ):
        cases.append(
            bench.Case(
                id=f"needle-{position}-{seed}",
                category="32k",
                kind="fresh",
                max_tokens=512,
                messages=inputs.make_messages(position, seed, records),
                primary=False,
            )
        )
    data = source.model_dump()
    data.update(
        id=f"context-{cohort}-{source.backend}-{series}",
        session=series,
        repeats=1,
        warmups=1,
        deadline_s=300.0,
        corpus_version="splash-context-1",
        cases=[c.model_dump() for c in cases],
        corpus_sha256=bench.digest([c.model_dump() for c in cases]),
        rate_metric="client_output_wall_tok_s",
    )
    return bench.Manifest.model_validate(data)


class ContextManifest(bench.StrictModel):
    contract: Literal["splash-context-1"]
    cohort: Literal["stock-8bit", "modified-4bit"]
    profile: bench.Manifest
    cases: list[ContextCase]
    tokenization_receipt_sha256: bench.SHA256
    reviewed_preflight_sha256: bench.SHA256 | None = None
    reviewed_at: float | None = None

    @model_validator(mode="after")
    def reviewed_contract(self) -> Self:
        p = self.profile
        if (
            p.context_tokens != 32768
            or p.cache_policy != "isolated"
            or p.concurrency != 1
            or p.settings.reasoning
            or p.settings.seed != 0
            or p.settings.temperature != 0
            or p.repeats != 1
            or p.deadline_s != 300
            or p.rate_metric != "client_output_wall_tok_s"
            or p.api_key_env is not None
            or p.corpus_version != "splash-context-1"
            or len(self.cases) != 6
            or len(p.cases) != 6
            or (self.reviewed_at is None) != (self.reviewed_preflight_sha256 is None)
        ):
            raise ValueError("Context profile does not match the frozen condition.")
        expected = [(position, seed) for position in inputs.POSITIONS for seed in inputs.SEEDS]
        if [(c.position, c.seed) for c in self.cases] != expected:
            raise ValueError("Missing, duplicate or reordered synthetic context cases.")
        for meta, case in zip(self.cases, p.cases, strict=True):
            messages = inputs.make_messages(meta.position, meta.seed, meta.records)
            canonical = bench.Case(
                id=f"needle-{meta.position}-{meta.seed}",
                category="32k",
                kind="fresh",
                max_tokens=512,
                messages=messages,
                primary=False,
            )
            if (
                case != canonical
                or meta.case_id != case.id
                or meta.expected_value != inputs.expected_value(meta.position, meta.seed)
                or abs(meta.needle_fraction - meta.position / 100) > 0.03
                or meta.body_sha256 != bench.digest(bench.request_body(p, case, 1))
                or meta.rendered_prompt_tokens + case.max_tokens > p.context_tokens
            ):
                raise ValueError(
                    "Synthetic input, token band, settings or native rendering receipt mismatch."
                )
        return self


def require_reviewed(manifest: ContextManifest, *, now: float) -> None:
    m = ContextManifest.model_validate(manifest.model_dump())
    if (
        m.reviewed_at is None
        or m.reviewed_preflight_sha256 is None
        or not -30 <= now - m.reviewed_at <= 900
    ):
        raise ValueError("A fresh reviewed preflight is required before context capture.")


class ContextSample(bench.StrictModel):
    case_id: bench.Identifier
    measurement: bench.Measurement


class ContextRun(bench.StrictModel):
    contract: Literal["splash-context-1"] = "splash-context-1"
    manifest: ContextManifest
    status: Literal["running", "complete", "failed", "interrupted"]
    warmup: bench.Measurement | None = None
    samples: list[ContextSample] = Field(default_factory=list)
    error: str | None = None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def correct_answer(answer: str, expected: str) -> bool:
    try:
        return bool(json.loads(answer, object_pairs_hook=_unique_object) == {"value": expected})
    except ValueError:
        return False


def capture_issues(
    m: bench.Measurement, *, prompt_tokens: int | None, warmup: bool = False
) -> list[str]:
    m = bench.Measurement.model_validate(m.model_dump())
    issues = []
    if m.error is not None:
        issues.append("request_error")
    if (
        m.cached_tokens != 0
        or not m.prompt_tokens
        or m.prompt_tokens < 0
        or (prompt_tokens is not None and m.prompt_tokens != prompt_tokens)
    ):
        issues.append("prompt_or_cache_mismatch")
    if (
        not m.completion_tokens
        or not 0 < m.completion_tokens <= (64 if warmup else 512)
        or m.finish_reason != ("length" if warmup else "stop")
        or (warmup and m.completion_tokens != 64)
        or m.reasoning
        or m.reasoning_tokens not in (None, 0)
    ):
        issues.append("completion_accounting")
    if m.ttft_s is None or m.total_s is None or m.ttft_s <= 0 or m.total_s < m.ttft_s:
        issues.append("invalid_latency")
    return issues


def compare_context(runs: list[ContextRun]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": "splash-context-review-1",
        "scope": "fixed_synthetic_context_smoke_only",
        "decision": "inconclusive",
        "native_gate": "unassessed",
        "quality_gate": "unassessed",
        "prompt_token_band": [31744, 32000],
        "cases_per_engine": 6,
        "issues": [],
        "engines": {},
    }
    try:
        checked = [ContextRun.model_validate(r.model_dump()) for r in runs]
    except ValueError:
        result["issues"] = ["invalid_artifact"]
        return result
    if len(checked) != 2 or {r.manifest.profile.backend for r in checked} != {"mlx", "splash"}:
        result["issues"] = ["missing_or_duplicate_engine"]
        return result
    keys = {
        bench.digest(
            {
                "cohort": r.manifest.cohort,
                "profile": r.manifest.profile.comparison_key(),
                "cases": [c.model_dump(exclude={"body_sha256"}) for c in r.manifest.cases],
            }
        )
        for r in checked
    }
    if len(keys) != 1:
        result["issues"] = ["incompatible_pair"]
        return result
    result["cohort"] = checked[0].manifest.cohort
    for run in checked:
        p = run.manifest.profile
        issues = result["issues"]
        if run.status != "complete" or run.error is not None:
            issues.append("incomplete_run")
        if run.warmup is None or capture_issues(run.warmup, prompt_tokens=None, warmup=True):
            issues.append("invalid_warmup")
        expected = {c.case_id: c for c in run.manifest.cases}
        if len(run.samples) != len(expected) or {s.case_id for s in run.samples} != set(expected):
            issues.append("missing_or_duplicate_samples")
        rows: list[dict[str, Any]] = []
        for sample in run.samples:
            meta = expected.get(sample.case_id)
            if meta is None:
                continue
            m = sample.measurement
            issues.extend(capture_issues(m, prompt_tokens=meta.rendered_prompt_tokens))
            rows.append(
                {
                    "case_id": meta.case_id,
                    "position": meta.position,
                    "correct": correct_answer(m.answer, meta.expected_value),
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                    "ttft_s": m.ttft_s,
                    "total_s": m.total_s,
                }
            )
        result["engines"][p.backend] = {
            "correct": sum(row["correct"] for row in rows),
            "cases": rows,
            "private_run_sha256": bench.digest(run.model_dump()),
        }
    result["issues"] = sorted(set(result["issues"]))
    if not result["issues"]:
        result["decision"] = (
            "pass" if all(r["correct"] == 6 for r in result["engines"].values()) else "fail"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs=2, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        runs = [ContextRun.model_validate_json(p.read_text()) for p in args.runs]
        report = compare_context(runs)
        bench.private_write(args.out, json.dumps(report, indent=2) + "\n", exclusive=True)
    except (ValueError, OSError, TypeError):
        print("Invalid context artifacts or unavailable output; inspect private evidence.")
        return 2
    print("Context smoke reviewed; native and representative quality gates remain unassessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
