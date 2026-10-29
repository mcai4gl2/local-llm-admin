#!/usr/bin/env python3
"""Strict review for the separately fixed four-window MLX overhead audit."""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from pathlib import Path
from typing import Any

import bench
import native_timing
import native_validation as pilot
from pydantic import Field


class ModelPins(bench.StrictModel):
    weights_sha256: bench.SHA256
    tokenizer_sha256: bench.SHA256
    template_sha256: bench.SHA256


class OverheadSample(pilot.NativeSample):
    model_pins: ModelPins
    window: int = Field(ge=0, le=3)
    body: dict[str, Any]
    headers: dict[str, str]
    native_trace: native_timing.NativeTrace | None
    native_request: dict[str, Any] | None
    decoded_prompt: str | None


def _stats(ratios: dict[tuple[int, int, str, int], float], categories: list[str]) -> dict[str, Any]:
    median = statistics.median(
        v for (_, _, category, _), v in ratios.items() if category in categories
    )
    rng = random.Random(0)
    estimates = []
    for _ in range(10000):
        draw = []
        for window in rng.choices(range(4), k=4):
            for pair in rng.choices(range(2), k=2):
                for category in rng.choices(categories, k=len(categories)):
                    for rep in rng.choices((1, 2), k=2):
                        draw.append(ratios[(window, pair, category, rep)])
        estimates.append(statistics.median(draw))
    estimates.sort()
    interval = [estimates[249], estimates[9749]]
    return {
        "median_wall_ratio": median,
        "ci95": interval,
        "decision": "fail"
        if median > 1.05
        else ("eligible" if interval[1] <= 1.05 else "inconclusive"),
    }


def _body_valid(sample: OverheadSample) -> bool:
    body = sample.body
    keys = {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "seed",
        "stream",
        "stream_options",
        "enable_thinking",
    }
    messages = body.get("messages")
    options = body.get("stream_options")
    return bool(
        set(body) == keys
        and isinstance(body.get("model"), str)
        and body["model"]
        and type(body.get("max_tokens")) is int
        and body["max_tokens"] == 256
        and type(body.get("seed")) is int
        and body["seed"] == 0
        and type(body.get("temperature")) in (int, float)
        and body["temperature"] == 0
        and body.get("enable_thinking") is False
        and body.get("stream") is True
        and isinstance(options, dict)
        and set(options) == {"include_usage"}
        and options["include_usage"] is True
        and isinstance(messages, list)
        and len(messages) >= 2
        and len(messages) % 2 == 0
        and messages[0] == {"role": "system", "content": f"Benchmark instance {sample.nonce}."}
        and all(
            isinstance(message, dict)
            and set(message) == {"role", "content"}
            and message["role"] == ("user" if index % 2 == 0 else "assistant")
            and isinstance(message["content"], str)
            and message["content"]
            for index, message in enumerate(messages[1:])
        )
        and sample.headers == {"X-APC-Tenant": sample.nonce}
    )


def compare_overhead(
    samples: list[dict[str, Any]],
    *,
    source_sha256: str,
    adapter_sha256: str,
    model_pins: dict[str, str],
) -> dict[str, Any]:
    if not all(re.fullmatch(r"[a-f0-9]{64}", s) for s in [source_sha256, adapter_sha256]):
        raise ValueError("Invalid pinned source identity.")
    expected_model = ModelPins.model_validate(model_pins)
    report: dict[str, Any] = {
        "contract": "splash-expanded-overhead-1",
        "backend": "mlx",
        "cohort": "modified-4bit",
        "decision": "inconclusive",
        "native_gate": "unassessed",
        "scope": "collector_overhead_only",
        "threshold": 1.05,
        "issues": [],
        "windows": 4,
        "uncertainty": "10000 seed-0 hierarchical bootstrap draws over four independent ABBA windows, paired launches, categories and repetitions; 95% percentile bounds.",
        "source_sha256": source_sha256,
        "adapter_sha256": adapter_sha256,
    }
    try:
        parsed = [OverheadSample.model_validate(s) for s in samples]
    except ValueError:
        report["issues"] = ["invalid_artifact"]
        return report
    keys = [(s.window, s.phase, s.category, s.rep) for s in parsed]
    expected = {
        (w, p, c, r)
        for w in range(4)
        for p in range(4)
        for c in ("short", "2k", "8k")
        for r in range(3)
    }
    if len(keys) != 144 or set(keys) != expected:
        report["issues"] = ["missing_or_duplicate_samples"]
        return report
    issues = report["issues"]
    nonce_groups: dict[str, tuple[Any, ...]] = {}
    grouped_nonces: dict[tuple[Any, ...], str] = {}
    category_inputs: dict[str, str] = {}
    accounted = 0
    for s in parsed:
        m = s.measurement
        if s.model_pins != expected_model:
            issues.append("model_identity_mismatch")
        if s.warmup != (s.rep == 0):
            issues.append("invalid_warmup")
        if (
            m.error is not None
            or m.cached_tokens != 0
            or m.completion_tokens != 256
            or m.finish_reason != "length"
            or not m.prompt_tokens
            or m.prompt_tokens <= 0
            or m.total_s is None
            or m.ttft_s is None
            or m.ttft_s <= 0
            or m.total_s < m.ttft_s
            or m.reasoning
            or m.reasoning_tokens not in (None, 0)
        ):
            issues.append("invalid_capture")
        if not _body_valid(s):
            issues.append("invalid_request_settings")
        if s.body_sha256 != bench.digest(s.body) or s.response_sha256 != bench.digest(
            {"answer": m.answer, "reasoning": m.reasoning}
        ):
            issues.append("artifact_digest_mismatch")
        if _body_valid(s):
            canonical = dict(s.body)
            canonical["messages"] = s.body["messages"][1:]
            fingerprint = bench.digest(canonical)
            if s.category in category_inputs and category_inputs[s.category] != fingerprint:
                issues.append("category_input_drift")
            category_inputs[s.category] = fingerprint
        pair = 0 if s.phase < 2 else 1
        group = (
            ("warmup", s.window, s.phase, s.category)
            if s.rep == 0
            else ("pair", s.window, pair, s.category, s.rep)
        )
        if (s.nonce in nonce_groups and nonce_groups[s.nonce] != group) or (
            group in grouped_nonces and grouped_nonces[group] != s.nonce
        ):
            issues.append("nonce_reuse_or_pair_mismatch")
        nonce_groups[s.nonce] = group
        grouped_nonces[group] = s.nonce
        if s.phase in (1, 2):
            if s.native_trace is None or s.native_request is None or s.decoded_prompt is None:
                issues.append("missing_native_accounting")
            else:
                try:
                    review = pilot.review_trace(
                        s.native_trace,
                        s.native_request,
                        m,
                        backend="mlx",
                        source_sha256=source_sha256,
                        adapter_sha256=adapter_sha256,
                        nonce=s.nonce,
                        decoded_prompt=s.decoded_prompt,
                        max_tokens=256,
                        reasoning=False,
                    )
                    if m.total_s is None or review["elapsed_s"] > m.total_s:
                        raise ValueError("Native interval exceeds request.")
                    accounted += 1
                except ValueError:
                    issues.append("native_accounting_mismatch")
        elif any(
            value is not None for value in (s.native_trace, s.native_request, s.decoded_prompt)
        ):
            issues.append("unexpected_original_trace")
    indexed = dict(zip(keys, parsed, strict=True))
    ratios: dict[tuple[int, int, str, int], float] = {}
    for window in range(4):
        for pair, (original, instrumented) in enumerate(((0, 1), (3, 2))):
            for category in ("short", "2k", "8k"):
                for rep in (1, 2):
                    a, b = (
                        indexed[(window, original, category, rep)],
                        indexed[(window, instrumented, category, rep)],
                    )
                    if (
                        a.body_sha256 != b.body_sha256
                        or a.response_sha256 != b.response_sha256
                        or a.nonce != b.nonce
                        or a.measurement.prompt_tokens != b.measurement.prompt_tokens
                    ):
                        issues.append("incompatible_pair")
                    at, bt = a.measurement.total_s, b.measurement.total_s
                    if at is not None and bt is not None and at > 0:
                        ratios[(window, pair, category, rep)] = bt / at
    if issues:
        report["issues"] = sorted(set(issues))
        return report
    report.update(_stats(ratios, ["short", "2k", "8k"]))
    report["categories"] = {c: _stats(ratios, [c]) for c in ("short", "2k", "8k")}
    decisions = [report["decision"]] + [r["decision"] for r in report["categories"].values()]
    report["decision"] = (
        "fail"
        if "fail" in decisions
        else ("eligible" if all(d == "eligible" for d in decisions) else "inconclusive")
    )
    report.update(
        paired_requests=48,
        excluded_warmups=48,
        accounted_native_traces=accounted,
        private_samples_sha256=bench.digest(
            [
                s.model_dump()
                for s in sorted(parsed, key=lambda s: (s.window, s.phase, s.category, s.rep))
            ]
        ),
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        design = json.loads(args.design.read_text())
        if (
            design["contract"] != "splash-overhead-design-2"
            or design["analysis"]["threshold"] != 1.05
        ):
            raise ValueError("Unexpected frozen design.")
        report = compare_overhead(
            json.loads(args.samples.read_text()),
            source_sha256=design["source_pins"]["source_sha256"],
            adapter_sha256=design["source_pins"]["adapter_sha256"],
            model_pins=design["model_pins"],
        )
        bench.private_write(args.out, json.dumps(report, indent=2) + "\n", exclusive=True)
    except (ValueError, OSError, TypeError, KeyError):
        print("Invalid expanded audit evidence or unavailable output; inspect private artifacts.")
        return 2
    print("Expanded collector overhead reviewed; native speed remains unassessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
