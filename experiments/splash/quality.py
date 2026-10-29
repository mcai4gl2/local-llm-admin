#!/usr/bin/env python3
"""Isolated deterministic quality capture. No lifecycle actions or speed qualification."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

import bench
import httpx
from pydantic import Field, model_validator

from local_llm_admin import eval_graders, eval_suites
from local_llm_admin.eval_suites import (
    EvalCase,
    Judge,
    Suite,
    SuiteError,
    ToolCall,
    messages_for,
    parse_suite,
)


def grader_digest() -> str:
    """Pin both grading code and its interpretation of the suite's spec types."""
    return bench.digest(
        {
            name: hashlib.sha256(Path(module.__file__ or "").read_bytes()).hexdigest()
            for name, module in [
                ("graders", eval_graders),
                ("suites", eval_suites),
                ("quality", sys.modules[__name__]),
                ("benchmark_contract", bench),
            ]
        }
    )


class QualityManifest(bench.StrictModel):
    contract: Literal["splash-quality-1"]
    id: bench.Identifier
    # Existing timing manifest is immutable launch-profile provenance; suite supplies quality cases.
    profile: bench.Manifest
    suite: Suite
    suite_sha256: bench.SHA256
    grader_sha256: bench.SHA256
    preflight_sha256: bench.SHA256 | None
    preflight_captured_at: float | None = None
    request_deadline_s: float = Field(gt=0, le=300)
    required_case_ids: list[str]

    @model_validator(mode="after")
    def frozen(self) -> Self:
        try:
            suite = parse_suite(self.suite.model_dump())
        except SuiteError as exc:
            raise ValueError("invalid quality suite") from exc
        if suite.needle is not None or any(isinstance(c.grader, Judge) for c in suite.cases):
            raise ValueError("freeze explicit deterministic cases; no dynamic needle or judge")
        if any(
            c.max_tokens <= 0 or c.max_tokens >= self.profile.context_tokens for c in suite.cases
        ):
            raise ValueError("quality output limit does not fit context")
        input_keys = [
            bench.digest({"messages": messages_for(c), "tools": c.tools}) for c in suite.cases
        ]
        if len(set(input_keys)) != len(input_keys):
            raise ValueError("duplicate case inputs cannot count as independent quality cases")
        if self.suite_sha256 != bench.digest(suite.model_dump()):
            raise ValueError("quality suite hash mismatch")
        if self.grader_sha256 != grader_digest():
            raise ValueError("grader source changed")
        if len(set(self.required_case_ids)) != len(self.required_case_ids) or not set(
            self.required_case_ids
        ) <= {c.id for c in suite.cases}:
            raise ValueError("invalid required case IDs")
        if self.preflight_sha256 == "0" * 64:
            raise ValueError("placeholder preflight is not evidence")
        if self.profile.cache_policy != "isolated" or self.profile.concurrency != 1:
            raise ValueError("quality pilot requires isolated fresh sequential requests")
        return self

    def comparison_key(self) -> str:
        return bench.digest(
            {
                "contract": self.contract,
                "profile": self.profile.comparison_key(),
                "suite": self.suite_sha256,
                "grader": self.grader_sha256,
                "deadline": self.request_deadline_s,
                "required": sorted(self.required_case_ids),
            }
        )


class QualitySample(bench.StrictModel):
    case_id: str
    measurement: bench.Measurement
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class QualityRun(bench.StrictModel):
    contract: Literal["splash-quality-1"] = "splash-quality-1"
    manifest: QualityManifest
    status: Literal["running", "complete", "failed", "interrupted"]
    samples: list[QualitySample] = Field(default_factory=list)
    error: str | None = None


def translated_case(manifest: QualityManifest, case: EvalCase) -> bench.Case:
    return bench.Case(
        id=bench.digest([manifest.suite_sha256, case.id])[:24],
        category="other",
        kind="fresh",
        max_tokens=case.max_tokens,
        messages=messages_for(case),
        primary=False,
    )


def quality_body(manifest: QualityManifest, case: EvalCase) -> dict[str, Any]:
    body = bench.request_body(manifest.profile, translated_case(manifest, case), 1)
    body["stream"] = False
    body.pop("stream_options")
    if case.tools:
        body.update(tools=copy.deepcopy(case.tools), tool_choice="auto")
    return body


def response_sample(case: EvalCase, payload: Any, expected_model: str) -> QualitySample:
    """Validate transport/accounting separately from whether an answer is correct."""
    sample = QualitySample(case_id=case.id, measurement=bench.Measurement())
    m = sample.measurement
    if not isinstance(payload, dict):
        raise bench.StreamFailure("invalid_response")
    if payload.get("model") != expected_model:
        raise bench.StreamFailure("model_mismatch")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise bench.StreamFailure("invalid_choices")
    choice = choices[0]
    if (
        type(choice.get("index")) is not int
        or choice.get("index") != 0
        or choice.get("finish_reason")
        not in {
            "stop",
            "length",
            "tool_calls",
        }
    ):
        raise bench.StreamFailure("invalid_finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise bench.StreamFailure("invalid_message")
    for field in ["content", "reasoning_content", "reasoning"]:
        if message.get(field) is not None and not isinstance(message[field], str):
            raise bench.StreamFailure("invalid_message")
    m.answer = message.get("content") or ""
    m.reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    m.finish_reason = choice["finish_reason"]
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    if not isinstance(calls, list):
        raise bench.StreamFailure("invalid_tool_calls")
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise bench.StreamFailure("invalid_tool_calls")
        fn = call.get("function")
        if (
            not isinstance(fn, dict)
            or not isinstance(fn.get("name"), str)
            or not fn["name"]
            or not isinstance(fn.get("arguments"), str)
        ):
            raise bench.StreamFailure("invalid_tool_calls")
        try:
            args = json.loads(fn["arguments"])
        except ValueError:
            raise bench.StreamFailure("invalid_tool_calls") from None
        if not isinstance(args, dict):
            raise bench.StreamFailure("invalid_tool_calls")
    if (m.finish_reason == "tool_calls") != bool(calls):
        raise bench.StreamFailure("invalid_tool_calls")
    sample.tool_calls = calls
    if not m.answer and not calls:
        raise bench.StreamFailure("empty_answer")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise bench.StreamFailure("missing_token_counts")
    bench.apply_usage(m, usage, case.max_tokens)
    if m.cached_tokens != 0:
        raise bench.StreamFailure("nonzero_or_unknown_cache")
    return sample


def private_directory(path: Path, *, exclusive: bool = False) -> None:
    """Create every new ancestor privately without changing existing directory permissions."""
    if not path.parent.exists():
        private_directory(path.parent)
    path.mkdir(mode=0o700, exist_ok=not exclusive)


def save_run(path: Path, run: QualityRun) -> None:
    temporary = path.with_suffix(".tmp")
    bench.private_write(temporary, run.model_dump_json(indent=2) + "\n")
    temporary.replace(path)


async def run_quality(
    manifest: QualityManifest,
    out: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> QualityRun:
    manifest = QualityManifest.model_validate(manifest.model_dump())
    if (
        manifest.preflight_sha256 is None
        or manifest.preflight_captured_at is None
        or not -30 <= time.time() - manifest.preflight_captured_at <= 900
    ):
        raise ValueError("fresh reviewed preflight required; draft or stale capture cannot run")
    private_directory(out)
    path = out / (manifest.id + ".json")
    run = QualityRun(manifest=manifest, status="running")
    bench.private_write(path, run.model_dump_json(indent=2) + "\n", exclusive=True)
    if client is None:
        async with httpx.AsyncClient(
            base_url=manifest.profile.base_url,
            trust_env=False,
            timeout=httpx.Timeout(manifest.request_deadline_s, connect=5),
        ) as http:
            return await collect_quality(run, path, http)
    return await collect_quality(run, path, client)


async def collect_quality(run: QualityRun, path: Path, client: httpx.AsyncClient) -> QualityRun:
    manifest = run.manifest
    profile = manifest.profile
    headers: dict[str, str] = {}
    current: EvalCase | None = None
    try:
        if profile.api_key_env:
            key = os.environ.get(profile.api_key_env)
            if not key:
                raise bench.StreamFailure("missing_api_key_environment")
            headers["Authorization"] = "Bearer " + key
        async with asyncio.timeout(min(30, manifest.request_deadline_s)):
            models = await client.get("/v1/models", headers=headers)
            if models.status_code != 200:
                raise bench.StreamFailure("model_discovery_failed")
            try:
                ids = {row["id"] for row in models.json()["data"]}
            except (ValueError, KeyError, TypeError):
                raise bench.StreamFailure("invalid_model_list") from None
            if profile.request_model not in ids:
                raise bench.StreamFailure("requested_model_not_served")
        for case in manifest.suite.cases:
            current = case
            started = time.monotonic()
            try:
                async with asyncio.timeout(manifest.request_deadline_s):
                    resp = await client.post(
                        "/v1/chat/completions",
                        json=quality_body(manifest, case),
                        headers={
                            **headers,
                            **bench.request_headers(profile, translated_case(manifest, case), 1),
                        },
                    )
                    if resp.status_code != 200:
                        raise bench.StreamFailure(f"http_{resp.status_code}")
                    try:
                        payload = resp.json()
                    except ValueError:
                        raise bench.StreamFailure("invalid_response") from None
                    sample = response_sample(case, payload, profile.response_model)
            except bench.StreamFailure as exc:
                sample = QualitySample(
                    case_id=case.id, measurement=bench.Measurement(error=str(exc))
                )
            except (TimeoutError, httpx.TimeoutException):
                sample = QualitySample(
                    case_id=case.id, measurement=bench.Measurement(error="timeout")
                )
            except httpx.HTTPError:
                sample = QualitySample(
                    case_id=case.id, measurement=bench.Measurement(error="transport")
                )
            sample.measurement.total_s = time.monotonic() - started
            run.samples.append(sample)
            current = None
            save_run(path, run)
            if sample.measurement.error or sample.measurement.finish_reason == "length":
                run.status, run.error = "failed", "quality_response_failed"
                return run
        run.status = "complete"
    except asyncio.CancelledError:
        run.status = "interrupted"
        if current:
            run.samples.append(
                QualitySample(case_id=current.id, measurement=bench.Measurement(error="cancelled"))
            )
        raise
    except bench.StreamFailure as exc:
        run.status, run.error = "failed", str(exc)
    except (TimeoutError, httpx.TimeoutException):
        run.status, run.error = "failed", "preflight_timeout"
    except httpx.HTTPError:
        run.status, run.error = "failed", "preflight_transport"
    finally:
        save_run(path, run)
    return run


def wilson(k: int, n: int) -> tuple[float, float]:
    # Two 97.5% Wilson intervals give a conservative 95% simultaneous paired difference.
    z = 2.241402727604947
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0, center - radius), min(1, center + radius)


def score_pair(baseline: list[bool], candidate: list[bool]) -> dict[str, Any]:
    if not baseline or len(baseline) != len(candidate):
        raise ValueError("need complete paired scores")
    n = len(baseline)
    wins = sum(b and not a for a, b in zip(baseline, candidate, strict=True))
    losses = sum(a and not b for a, b in zip(baseline, candidate, strict=True))
    wlo, whi = wilson(wins, n)
    llo, lhi = wilson(losses, n)
    diff = (wins - losses) / n
    lower, upper = wlo - lhi, whi - llo
    decision = (
        "fail" if diff < -0.02 else ("pass" if n >= 10 and lower >= -0.02 else "inconclusive")
    )
    return {
        "cases": n,
        "baseline_score": sum(baseline) / n,
        "candidate_score": sum(candidate) / n,
        "mean_difference": diff,
        "difference_ci95": [lower, upper],
        "decision": decision,
        "wins": wins,
        "losses": losses,
        "ties": n - wins - losses,
        "uncertainty_method": "Approximate 95% Bonferroni Wilson intervals on paired binary wins/losses; cases are sampling units.",
    }


def compare_quality(runs: list[QualityRun]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "contract": "splash-quality-1",
        "decision": "inconclusive",
        "issues": [],
        "samples": {
            "measured": sum(len(r.samples) for r in runs),
            "errors": sum(bool(s.measurement.error) for r in runs for s in r.samples),
            "truncated": sum(
                s.measurement.finish_reason == "length" for r in runs for s in r.samples
            ),
        },
        "raw_runs": [{"id": r.manifest.id, "sha256": bench.digest(r.model_dump())} for r in runs],
    }
    try:
        checked = [QualityRun.model_validate(r.model_dump()) for r in runs]
    except ValueError:
        report["issues"] = ["invalid_manifest_or_run"]
        return report
    if (
        len(checked) != 2
        or {r.manifest.profile.backend for r in checked} != {"mlx", "splash"}
        or len({r.manifest.id for r in checked}) != 2
    ):
        report["issues"] = ["need_one_distinct_pair"]
        return report
    if (
        len({r.manifest.comparison_key() for r in checked}) != 1
        or len({(r.manifest.profile.session, r.manifest.profile.order) for r in checked}) != 1
    ):
        report["issues"] = ["incompatible_pair"]
        return report
    if any(
        r.status != "complete" or r.error or r.manifest.preflight_sha256 is None for r in checked
    ):
        report["issues"] = ["incomplete_run"]
        if any(
            r.error
            or any(
                s.measurement.error or s.measurement.finish_reason == "length" for s in r.samples
            )
            for r in checked
        ):
            report["decision"] = "fail"
        return report
    indexed = {r.manifest.profile.backend: r for r in checked}
    suite = checked[0].manifest.suite
    expected = {c.id for c in suite.cases}
    if any(
        len(r.samples) != len(expected) or {s.case_id for s in r.samples} != expected
        for r in checked
    ):
        report["issues"] = ["missing_or_duplicate_cases"]
        return report
    samples = {backend: {s.case_id: s for s in run.samples} for backend, run in indexed.items()}
    for case in suite.cases:
        a, b = samples["mlx"][case.id], samples["splash"][case.id]
        if a.measurement.prompt_tokens != b.measurement.prompt_tokens:
            report["issues"].append("paired_prompt_count_mismatch")
        for s in (a, b):
            m = s.measurement
            try:
                if m.error or m.finish_reason == "length":
                    raise bench.StreamFailure("invalid_or_failed_response")
                response_sample(
                    case,
                    {
                        "model": "stored-response",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": m.finish_reason,
                                "message": {
                                    "role": "assistant",
                                    "content": m.answer,
                                    "reasoning_content": m.reasoning,
                                    "tool_calls": s.tool_calls,
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": m.prompt_tokens,
                            "completion_tokens": m.completion_tokens,
                            "prompt_tokens_details": {"cached_tokens": m.cached_tokens},
                        },
                    },
                    "stored-response",
                )
            except bench.StreamFailure:
                report["issues"].append("invalid_or_failed_response")
    if report["issues"]:
        report["issues"] = sorted(set(report["issues"]))
        report["decision"] = "fail"
        return report
    scores: dict[str, list[bool]] = {"mlx": [], "splash": []}
    for backend in ("mlx", "splash"):
        for case in suite.cases:
            s = samples[backend][case.id]
            g = eval_graders.grade_response(
                case.grader,
                s.measurement.answer,
                s.measurement.finish_reason,
                len(s.measurement.reasoning),
                s.tool_calls,
                thinking=indexed[backend].manifest.profile.settings.reasoning,
            )
            passed = g.get("passed") == 1
            if (
                isinstance(case.grader, ToolCall)
                and case.grader.name is not None
                and len(s.tool_calls) != 1
            ):
                passed = False
            scores[backend].append(passed)
            if (
                backend == "splash"
                and case.id in checked[0].manifest.required_case_ids
                and not passed
            ):
                report["issues"].append("required_case_failed")
    report["per_case"] = [
        {
            "id": case.id,
            "category": case.category,
            "mlx_pass": scores["mlx"][i],
            "splash_pass": scores["splash"][i],
        }
        for i, case in enumerate(suite.cases)
    ]
    report["categories"] = {
        category: {
            "cases": sum(c.category == category for c in suite.cases),
            **{
                backend + "_score": sum(
                    scores[backend][i] for i, c in enumerate(suite.cases) if c.category == category
                )
                / sum(c.category == category for c in suite.cases)
                for backend in ("mlx", "splash")
            },
        }
        for category in sorted({c.category for c in suite.cases})
    }
    report.update(score_pair(scores["mlx"], scores["splash"]))
    if report["issues"]:
        report["decision"] = "fail"
    report["scope"] = (
        "Deterministic fixed-suite binary pass rate; no speed, broad quality or adoption verdict."
    )
    return report


def prepare_pair(
    sources: list[bench.Run],
    suite: Suite,
    out: Path,
    *,
    cohort: str,
    deadline_s: float = 60.0,
    window_s: float = 7200.0,
) -> dict[str, Any]:
    """Prepare one whole cohort pair; timeout-based bounds do not predict actual duration."""
    allowed = {
        "insufficient_repetitions_or_sessions",
        "sessions_not_counterbalanced",
        "insufficient_p95_samples",
        "client_wall_rate_is_not_native_decode",
    }
    source_report = bench.compare_runs(sources)
    if (
        len(sources) != 2
        or {r.manifest.backend for r in sources} != {"mlx", "splash"}
        or set(source_report["issues"]) - allowed
    ):
        raise ValueError("need a valid complete source timing pair")
    if cohort not in {"stock-8bit", "modified-4bit"}:
        raise ValueError("unsupported cohort label")
    if not bench.positive(window_s):
        raise ValueError("invalid window")
    manifests = [
        QualityManifest(
            contract="splash-quality-1",
            id=f"{cohort}-quality-{r.manifest.backend}-1",
            profile=r.manifest.model_copy(deep=True),
            suite=suite.model_copy(deep=True),
            suite_sha256=bench.digest(suite.model_dump()),
            grader_sha256=grader_digest(),
            preflight_sha256=None,
            request_deadline_s=deadline_s,
            required_case_ids=[c.id for c in suite.cases if c.category == "tools"],
        )
        for r in sources
    ]
    cost = 2 * len(suite.cases) * deadline_s + 900 + 600
    if cost > window_s:
        raise ValueError("whole quality pair cannot fit with setup and restoration reserves")
    worksheet = {
        "contract": "splash-quality-1",
        "authorization": "required",
        "cohort": cohort,
        "suite_sha256": manifests[0].suite_sha256,
        "grader_sha256": grader_digest(),
        "requests_per_backend": len(suite.cases),
        "request_deadline_s": deadline_s,
        "setup_and_switch_reserve_s": 900,
        "restore_reserve_s": 600,
        "budgeted_total_s": cost,
        "maximum_s": window_s,
        "backend_order": ["mlx", "splash"]
        if sources[0].manifest.order == "mlx-first"
        else ["splash", "mlx"],
        "source_runs": [
            {"id": r.manifest.id, "sha256": bench.digest(r.model_dump())} for r in sources
        ],
        "requires": [
            "fresh reviewed preflight per launch",
            "effective profile and real readiness",
            "separate live approval",
            "independent host resource/deadline supervisor",
            "original-service restoration and independent verification",
        ],
        "native_gate": "unassessed",
    }
    private_directory(out, exclusive=True)
    for m in manifests:
        bench.private_write(
            out / (m.id + ".draft.json"), m.model_dump_json(indent=2) + "\n", exclusive=True
        )
    bench.private_write(
        out / "worksheet.json", json.dumps(worksheet, indent=2) + "\n", exclusive=True
    )
    return worksheet


def freeze_manifest(draft: QualityManifest, preflight: dict[str, Any]) -> QualityManifest:
    captured = preflight.get("captured_at")
    if (
        preflight.get("complete") is not True
        or preflight.get("issues") != []
        or preflight.get("serving_ready") is not True
        or not preflight.get("review")
        or not isinstance(captured, (int, float))
        or isinstance(captured, bool)
        or not -30 <= time.time() - captured <= 900
    ):
        raise ValueError("fresh independently reviewed preflight required")
    data = draft.model_dump()
    data.update(preflight_sha256=bench.digest(preflight), preflight_captured_at=captured)
    return QualityManifest.model_validate(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--mlx-run", type=Path, required=True)
    prep.add_argument("--splash-run", type=Path, required=True)
    prep.add_argument("--cohort", choices=["stock-8bit", "modified-4bit"], required=True)
    prep.add_argument("--suite", type=Path, default=Path(__file__).with_name("quality-suite.json"))
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--deadline-seconds", type=float, default=60.0)
    prep.add_argument("--window-minutes", type=float, default=120.0)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--draft", type=Path, required=True)
    freeze.add_argument("--preflight", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--results", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            sources = [
                bench.Run.model_validate_json(p.read_text())
                for p in (args.mlx_run, args.splash_run)
            ]
            worksheet = prepare_pair(
                sources,
                parse_suite(json.loads(args.suite.read_text())),
                args.out,
                cohort=args.cohort,
                deadline_s=args.deadline_seconds,
                window_s=args.window_minutes * 60,
            )
            print(
                f"Prepared one whole quality pair: {worksheet['budgeted_total_s'] / 60:.2f} budgeted minutes; live authorization required."
            )
        elif args.command == "freeze":
            draft = QualityManifest.model_validate_json(args.draft.read_text())
            frozen = freeze_manifest(draft, json.loads(args.preflight.read_text()))
            bench.private_write(args.out, frozen.model_dump_json(indent=2) + "\n", exclusive=True)
            print(
                "Manifest frozen to reviewed preflight; live authorization and supervisor still required."
            )
        elif args.command == "run":
            manifest = QualityManifest.model_validate_json(args.manifest.read_text())
            result = asyncio.run(run_quality(manifest, args.out))
            print(
                f"Quality capture {result.status}; inspect private evidence before qualification."
            )
            return (
                0
                if result.status == "complete"
                and all(
                    not s.measurement.error
                    and s.measurement.finish_reason in {"stop", "tool_calls"}
                    for s in result.samples
                )
                else 1
            )
        else:
            paths = sorted(args.results.glob("*.json"))
            if not paths:
                raise ValueError("missing evidence")
            report = compare_quality([QualityRun.model_validate_json(p.read_text()) for p in paths])
            bench.private_write(args.out, json.dumps(report, indent=2) + "\n", exclusive=True)
            print(
                f"Fixed-suite quality decision: {report['decision']}; other adoption gates remain separate."
            )
        return 0
    except (ValueError, OSError, SuiteError):
        print("Invalid evidence, budget, preflight or output; inspect private artifacts locally.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
