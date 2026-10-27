"""Separate frozen replay/cache correctness pilot; no native speed or adoption pass."""

from __future__ import annotations

import json
import math
import statistics
from typing import Any, Literal, Self, cast

import bench
import context_inputs
import context_validation
from pydantic import Field, model_validator

STEPS = ("prime", "repeat", "extend")
POSITIONS = (10, 50, 90)
Step = Literal["prime", "repeat", "extend"]


def expected(case_id: str) -> str:
    if case_id not in {f"needle-{p}-0" for p in POSITIONS}:
        raise ValueError("Unknown fixed replay trajectory.")
    return context_inputs.expected_value(int(case_id.split("-")[1]), 0)


def namespace(profile: bench.Manifest, case_id: str) -> str:
    return bench.digest(
        [
            "splash-replay-1",
            profile.id.rsplit("-", 1)[0],
            profile.session,
            profile.model.weights_sha256,
            case_id,
        ]
    )[:24]


def make_profile(
    source: bench.Manifest, cohort: str, context: str, session: int, records: list[int]
) -> bench.Manifest:
    if (
        cohort not in {"stock-8bit", "modified-4bit"}
        or context not in {"8k", "32k"}
        or len(records) != 3
    ):
        raise ValueError("Invalid fixed replay identity.")
    data = source.model_dump()
    # The final backend component is excluded from namespace derivation.
    data.update(
        id=f"replay-{cohort}-{context}-{source.backend}",
        session=session,
        context_tokens=32768,
        kv_precision="bf16-kv-fp32-recurrent",
        settings={"temperature": 0.0, "seed": 0, "reasoning": False},
        cache_policy="replay",
        concurrency=1,
        repeats=1,
        warmups=1,
        deadline_s=150.0 if context == "8k" else 300.0,
        corpus_version="splash-replay-1",
        rate_metric="client_output_wall_tok_s",
    )
    # Namespace depends only on frozen identity, not corpus contents or backend.
    identity = source.model_copy(update={"id": data["id"], "session": session})
    cases = []
    for position, count in zip(POSITIONS, records, strict=True):
        case_id = f"needle-{position}-0"
        messages = [
            {"role": "system", "content": f"Benchmark instance {namespace(identity, case_id)}."},
            *context_inputs.make_messages(position, 0, count),
        ]
        cases.append(
            bench.Case(
                id=case_id,
                category=cast(Literal["8k", "32k"], context),
                kind="replay",
                max_tokens=128,
                messages=messages,
                primary=False,
            )
        )
    data.update(
        cases=[c.model_dump() for c in cases],
        corpus_sha256=bench.digest([c.model_dump() for c in cases]),
    )
    return bench.Manifest.model_validate(data)


def request(
    profile: bench.Manifest, case: bench.Case, step: str
) -> tuple[dict[str, Any], dict[str, str]]:
    if step not in STEPS:
        raise ValueError("Unknown replay step.")
    body = bench.request_body(profile, case, 1)
    if step == "extend":
        body["messages"].extend(
            [
                {
                    "role": "assistant",
                    "content": json.dumps({"value": expected(case.id)}, separators=(",", ":")),
                },
                {
                    "role": "user",
                    "content": f"Using the same records, return the value for entry {case.id} in the same JSON object. Return only that object.",
                },
            ]
        )
    salt = namespace(profile, case.id)
    if profile.backend == "splash":
        body["cache_salt"] = salt
        return body, {}
    return body, {"X-APC-Tenant": salt}


def warmup_request(profile: bench.Manifest) -> tuple[dict[str, Any], dict[str, str]]:
    case = profile.cases[0].model_copy(deep=True)
    salt = namespace(profile, "warmup")
    case.messages = [
        {"role": "system", "content": f"Benchmark instance {salt}."},
        {"role": "user", "content": "Reply exactly READY."},
    ]
    case.max_tokens = 32
    body = bench.request_body(profile, case, 1)
    if profile.backend == "splash":
        body["cache_salt"] = salt
        return body, {}
    return body, {"X-APC-Tenant": salt}


class ReplayRequest(bench.StrictModel):
    case_id: bench.Identifier
    step: Step
    body: dict[str, Any]
    headers: dict[str, str]
    body_sha256: bench.SHA256
    prompt_tokens: int = Field(gt=0)
    shared_prefix_tokens: int = Field(ge=0)


class ReplayManifest(bench.StrictModel):
    contract: Literal["splash-replay-1"]
    cohort: Literal["stock-8bit", "modified-4bit"]
    context: Literal["8k", "32k"]
    profile: bench.Manifest
    records: list[int]
    requests: list[ReplayRequest]
    warmup_body: dict[str, Any]
    warmup_headers: dict[str, str]
    warmup_prompt_tokens: int = Field(gt=0, le=1024)
    tokenization_receipt_sha256: bench.SHA256
    reviewed_at: float | None = None
    reviewed_preflight_sha256: bench.SHA256 | None = None

    @model_validator(mode="after")
    def frozen_contract(self) -> Self:
        p = self.profile
        canonical = make_profile(p, self.cohort, self.context, p.session, self.records)
        if p != canonical or p.api_key_env is not None:
            raise ValueError("Replay profile differs from the fixed condition.")
        if (self.reviewed_at is None) != (self.reviewed_preflight_sha256 is None):
            raise ValueError("Incomplete readiness proof.")
        if (self.warmup_body, self.warmup_headers) != warmup_request(p):
            raise ValueError("Replay warmup differs from frozen input.")
        schedule = [(c.id, step) for c in p.cases for step in STEPS]
        if [(q.case_id, q.step) for q in self.requests] != schedule:
            raise ValueError("Missing, duplicate or reordered replay requests.")
        low, high = (7936, 8192) if self.context == "8k" else (31744, 32000)
        for case, group in zip(p.cases, [self.requests[i : i + 3] for i in (0, 3, 6)], strict=True):
            prime = group[0]
            for q in group:
                if (q.body, q.headers) != request(p, case, q.step) or q.body_sha256 != bench.digest(
                    q.body
                ):
                    raise ValueError("Replay body, fixed history or namespace mismatch.")
                if not low <= q.prompt_tokens <= high or q.prompt_tokens + 128 > p.context_tokens:
                    raise ValueError("Replay token band exceeds the frozen condition.")
                if q.step == "prime":
                    valid_prefix = q.shared_prefix_tokens == 0
                elif q.step == "repeat":
                    valid_prefix = (
                        q.prompt_tokens == prime.prompt_tokens
                        and q.shared_prefix_tokens == prime.prompt_tokens
                    )
                else:
                    valid_prefix = (
                        prime.prompt_tokens - 16
                        <= q.shared_prefix_tokens
                        <= min(prime.prompt_tokens, q.prompt_tokens)
                    )
                if not valid_prefix:
                    raise ValueError("Invalid frozen shared-prefix token count.")
        return self


def require_reviewed(manifest: ReplayManifest, *, now: float) -> None:
    m = ReplayManifest.model_validate(manifest.model_dump())
    if (
        m.reviewed_at is None
        or m.reviewed_preflight_sha256 is None
        or not -30 <= now - m.reviewed_at <= 900
    ):
        raise ValueError("Fresh independently reviewed readiness is required.")


class ReplaySample(bench.StrictModel):
    case_id: bench.Identifier
    step: Step
    body_sha256: bench.SHA256
    headers_sha256: bench.SHA256
    measurement: bench.Measurement


class ReplayRun(bench.StrictModel):
    contract: Literal["splash-replay-1"] = "splash-replay-1"
    manifest: ReplayManifest
    status: Literal["running", "complete", "failed", "interrupted"]
    started_at: float = Field(gt=0)
    warmup: bench.Measurement | None = None
    samples: list[ReplaySample] = Field(default_factory=list)
    error: str | None = None


def capture_issues(
    manifest: ReplayManifest, value: bench.Measurement, req: ReplayRequest | None = None
) -> list[str]:
    issues = []
    prompt = manifest.warmup_prompt_tokens if req is None else req.prompt_tokens
    cap = 32 if req is None else 128
    if value.error is not None:
        issues.append("request_error")
    if (
        value.prompt_tokens != prompt
        or type(value.completion_tokens) is not int
        or not 0 < value.completion_tokens <= cap
    ):
        issues.append("invalid_token_accounting")
    if type(value.cached_tokens) is not int or not 0 <= value.cached_tokens <= prompt:
        issues.append("unknown_or_invalid_reuse")
    elif req is None or req.step == "prime":
        if value.cached_tokens != 0:
            issues.append("unexpected_cold_reuse")
    elif value.cached_tokens < math.ceil(req.shared_prefix_tokens * 0.8):
        issues.append("insufficient_shared_prefix_reuse")
    if value.finish_reason != "stop":
        issues.append("truncated_or_invalid_finish")
    if value.reasoning or value.reasoning_tokens not in (None, 0):
        issues.append("unexpected_reasoning")
    if not all(bench.positive(v) for v in (value.ttft_s, value.first_answer_s, value.total_s)):
        issues.append("invalid_latency")
    elif (
        value.ttft_s is not None
        and value.first_answer_s is not None
        and value.total_s is not None
        and not value.ttft_s <= value.first_answer_s <= value.total_s
    ):
        issues.append("invalid_latency")
    correct = (
        value.answer.strip() == "READY"
        if req is None
        else context_validation.correct_answer(value.answer, expected(req.case_id))
    )
    if not correct:
        issues.append("incorrect_answer")
    return issues


def _frozen(m: ReplayManifest) -> dict[str, Any]:
    return m.model_dump(exclude={"reviewed_at", "reviewed_preflight_sha256"})


def compare_runs(runs: list[ReplayRun], expected_manifests: list[ReplayManifest]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "contract": "splash-replay-review-1",
        "replay_gate": "inconclusive",
        "adoption_decision": "inconclusive",
        "issues": [],
        "long_requests": sum(len(r.samples) for r in runs),
        "cached_requests": sum(s.step != "prime" for r in runs for s in r.samples),
        "scope": "Fixed synthetic replay correctness and reported shared-prefix reuse; descriptive client latency only.",
        "backends": {},
    }
    issues: set[str] = set()
    failed = False
    try:
        frozen = [ReplayManifest.model_validate(m.model_dump()) for m in expected_manifests]
        if len(frozen) != 2 or {m.profile.backend for m in frozen} != {"mlx", "splash"}:
            raise ValueError("invalid pair")
        if frozen[0].profile.comparison_key() != frozen[1].profile.comparison_key() or any(
            [(q.prompt_tokens, q.shared_prefix_tokens) for q in m.requests]
            != [(q.prompt_tokens, q.shared_prefix_tokens) for q in frozen[0].requests]
            for m in frozen[1:]
        ):
            raise ValueError("incompatible pair")
        if len(runs) != 2 or {r.manifest.profile.backend for r in runs} != {"mlx", "splash"}:
            issues.add("missing_or_duplicate_backend")
        by_backend = {m.profile.backend: m for m in frozen}
        for raw in runs:
            run = ReplayRun.model_validate(raw.model_dump())
            m = run.manifest
            require_reviewed(m, now=run.started_at)
            if _frozen(m) != _frozen(by_backend[m.profile.backend]):
                issues.add("frozen_manifest_mismatch")
                continue
            if run.status != "complete" or len(run.samples) != 9 or run.warmup is None:
                issues.add("incomplete_capture")
            if run.error is not None:
                failed = True
                issues.add("run_error")
            if run.warmup is not None:
                bad = capture_issues(m, run.warmup)
                failed |= bool(bad)
                issues.update(bad)
            keys = [(q.case_id, q.step) for q in m.requests]
            if [(s.case_id, s.step) for s in run.samples] != keys[: len(run.samples)]:
                issues.add("request_schedule_mismatch")
                continue
            for sample, req in zip(run.samples, m.requests, strict=False):
                if sample.body_sha256 != req.body_sha256 or sample.headers_sha256 != bench.digest(
                    req.headers
                ):
                    issues.add("request_input_mismatch")
                    continue
                bad = capture_issues(m, sample.measurement, req)
                failed |= bool(bad)
                issues.update(bad)
            if not issues:
                summary = {}
                for step in STEPS:
                    values = [s.measurement for s in run.samples if s.step == step]
                    ttfts: list[float] = []
                    totals: list[float] = []
                    cached: list[int] = []
                    for value in values:
                        assert (
                            value.ttft_s is not None
                            and value.total_s is not None
                            and value.cached_tokens is not None
                        )
                        ttfts.append(value.ttft_s)
                        totals.append(value.total_s)
                        cached.append(value.cached_tokens)
                    summary[step] = {
                        "requests": len(values),
                        "median_ttft_s": statistics.median(ttfts),
                        "min_ttft_s": min(ttfts),
                        "max_ttft_s": max(ttfts),
                        "median_total_s": statistics.median(totals),
                        "min_cached_tokens": min(cached),
                        "max_cached_tokens": max(cached),
                    }
                report["backends"][m.profile.backend] = summary
    except (ValueError, KeyError, TypeError):
        issues.add("invalid_or_incompatible_evidence")
    report["issues"] = sorted(issues)
    if failed:
        report.update(replay_gate="fail", adoption_decision="fail")
    elif not issues:
        report["replay_gate"] = "pass"
    return report
