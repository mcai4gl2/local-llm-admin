#!/usr/bin/env python3
"""Strict native trace accounting and a separately scoped collection-overhead pilot."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import bench
import native_timing
from pydantic import Field


class NativeSample(bench.StrictModel):
    phase: int = Field(ge=0, le=3)
    category: Literal["short", "2k", "8k"]
    rep: int = Field(ge=0, le=2)
    warmup: bool
    nonce: str = Field(min_length=1)
    body_sha256: bench.SHA256
    response_sha256: bench.SHA256
    measurement: bench.Measurement


def review_trace(
    trace: native_timing.NativeTrace,
    companion: dict[str, Any],
    measurement: bench.Measurement,
    *,
    backend: str,
    source_sha256: str,
    adapter_sha256: str,
    nonce: str,
    decoded_prompt: str,
    max_tokens: int,
    reasoning: bool,
) -> dict[str, Any]:
    """Associate native rendered inputs, explicit IDs and independently captured HTTP usage."""
    trace = native_timing.NativeTrace.model_validate(trace.model_dump())
    m = bench.Measurement.model_validate(measurement.model_dump())
    request = companion.get("native_request")
    if (
        companion.get("contract") != "splash-native-request-1"
        or companion.get("backend") != backend
        or trace.backend != backend
        or not isinstance(request, dict)
        or companion.get("request_sha256") != trace.request_sha256
        or bench.digest(request) != trace.request_sha256
        or trace.runtime_sha256 != source_sha256
        or trace.adapter_sha256 != adapter_sha256
        or m.error is not None
        or m.cached_tokens != 0
        or m.completion_tokens != trace.completion_tokens
        or m.finish_reason != trace.finish_reason
        or not m.prompt_tokens
        or m.total_s is None
        or m.total_s <= 0
        or trace.completion_tokens > max_tokens
        or (m.finish_reason == "length" and trace.completion_tokens != max_tokens)
        or not nonce
        or decoded_prompt.count(f"Benchmark instance {nonce}.") != 1
    ):
        raise ValueError("Native input, runtime identity or HTTP accounting mismatch.")
    if backend == "mlx":
        args = request.get("arguments")
        if not isinstance(args, dict) or request.get("rendered_prompt") != decoded_prompt:
            raise ValueError("Invalid MLX native input.")
        cap, thinking, seed, temp = (
            args.get("max_tokens"),
            args.get("enable_thinking"),
            args.get("seed"),
            args.get("temperature"),
        )
    elif backend == "splash":
        args = request.get("sampling")
        tokens = request.get("prompt_tokens")
        if (
            not isinstance(args, dict)
            or not isinstance(tokens, list)
            or any(type(t) is not int or t < 0 for t in tokens)
            or len(tokens) != m.prompt_tokens
        ):
            raise ValueError("Invalid Splash native input.")
        cap, thinking, seed, temp = (
            request.get("max_new_tokens"),
            request.get("thinking"),
            request.get("seed"),
            args.get("temperature"),
        )
    else:
        raise ValueError("Unknown native backend.")
    if (
        type(cap) is not int
        or cap != max_tokens
        or thinking is not reasoning
        or type(seed) is not int
        or seed != 0
        or isinstance(temp, bool)
        or not isinstance(temp, (int, float))
        or temp != 0
        or (reasoning and (not m.reasoning.strip() or not m.answer.strip()))
    ):
        raise ValueError("Native generation settings or reasoning mismatch.")
    result = native_timing.inspect_trace(trace)
    result.update(
        status="accounted_not_qualified",
        native_gate="unassessed",
        companion_sha256=bench.digest(companion),
    )
    return result


def _stats(ratios: dict[tuple[int, str, int], float], categories: list[str]) -> dict[str, Any]:
    values = [v for (_, c, _), v in ratios.items() if c in categories]
    median = statistics.median(values)
    rng = random.Random(0)
    estimates = []
    # Resample whole launch-pair blocks and prompt categories, then paired repetitions.
    for _ in range(2000):
        draw = []
        for block in rng.choices([0, 1], k=2):
            for category in rng.choices(categories, k=len(categories)):
                for rep in rng.choices([1, 2], k=2):
                    draw.append(ratios[(block, category, rep)])
        estimates.append(statistics.median(draw))
    estimates.sort()
    lower, upper = estimates[49], estimates[1949]
    return {
        "median_wall_ratio": median,
        "ci95": [lower, upper],
        "decision": "fail" if median > 1.05 else ("eligible" if upper <= 1.05 else "inconclusive"),
    }


def compare_overhead(samples: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "contract": "splash-native-overhead-1",
        "backend": backend,
        "decision": "inconclusive",
        "native_gate": "unassessed",
        "issues": [],
        "threshold": 1.05,
        "scope": "Collector overhead eligibility only; no cross-engine native speed claim.",
        "uncertainty": "95% cluster bootstrap of launch-pair blocks, categories and paired repetitions.",
    }
    if backend not in {"mlx", "splash"}:
        raise ValueError("Unknown native backend.")
    try:
        parsed = [NativeSample.model_validate(s) for s in samples]
    except ValueError:
        report["issues"] = ["invalid_sample"]
        return report
    warmups = [s for s in parsed if s.warmup]
    if any(s.rep != 0 or s.category != "short" for s in warmups) or len(
        {s.phase for s in warmups}
    ) != len(warmups):
        report["issues"] = ["invalid_warmup"]
        return report
    rows = [s for s in parsed if not s.warmup]
    keys = [(s.phase, s.category, s.rep) for s in rows]
    expected = {(p, c, r) for p in range(4) for c in ("short", "2k", "8k") for r in (1, 2)}
    if len(keys) != len(expected) or set(keys) != expected:
        report["issues"] = ["missing_or_duplicate_samples"]
        return report
    indexed: dict[tuple[int, str, int], NativeSample] = dict(zip(keys, rows, strict=True))
    ratios: dict[tuple[int, str, int], float] = {}
    for block, (original, instrumented) in enumerate(((0, 1), (3, 2))):
        for category in ("short", "2k", "8k"):
            for rep in (1, 2):
                a, b = indexed[(original, category, rep)], indexed[(instrumented, category, rep)]
                am, bm = a.measurement, b.measurement
                if (
                    a.nonce != b.nonce
                    or a.body_sha256 != b.body_sha256
                    or a.response_sha256 != b.response_sha256
                    or am.prompt_tokens != bm.prompt_tokens
                ):
                    report["issues"].append("incompatible_pair")
                if any(
                    m.error
                    or m.cached_tokens != 0
                    or m.completion_tokens != 256
                    or m.finish_reason != "length"
                    or not m.prompt_tokens
                    or m.total_s is None
                    or m.total_s <= 0
                    for m in (am, bm)
                ):
                    report["issues"].append("invalid_capture")
                if am.total_s is not None and bm.total_s is not None and am.total_s > 0:
                    ratios[(block, category, rep)] = bm.total_s / am.total_s
    if report["issues"]:
        report["issues"] = sorted(set(report["issues"]))
        return report
    report.update(_stats(ratios, ["short", "2k", "8k"]))
    report["categories"] = {c: _stats(ratios, [c]) for c in ("short", "2k", "8k")}
    decisions = [report["decision"]] + [v["decision"] for v in report["categories"].values()]
    report["decision"] = (
        "fail"
        if "fail" in decisions
        else ("eligible" if all(d == "eligible" for d in decisions) else "inconclusive")
    )
    report["paired_requests"], report["excluded_warmups"] = len(ratios), len(warmups)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--backend", required=True, choices=["mlx", "splash"])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare_overhead(json.loads(args.samples.read_text()), args.backend)
        bench.private_write(args.out, json.dumps(result, indent=2) + "\n", exclusive=True)
    except (ValueError, OSError, TypeError):
        print("Invalid pilot evidence or unavailable output; inspect private artifacts.")
        return 2
    print("Collection overhead reviewed; native speed gate remains unassessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
