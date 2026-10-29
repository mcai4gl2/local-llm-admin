#!/usr/bin/env python3
"""Private, checkpointed HTTP measurements for the gated Splash experiment.

This tool never starts/stops engines or changes host configuration. Stream events
are framing, never tokens. Only audited native timings can assess the decode gate;
output/wall time is a separately named client metric. See README.md before use.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import random
import statistics
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")]
CONTRACT_VERSION: Literal["splash-phase0-2"] = "splash-phase0-2"


def digest(value: Any) -> str:
    """Canonical JSON digest used for frozen corpora and comparison contracts."""
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Runtime(StrictModel):
    version: str = Field(min_length=1)
    sha256: SHA256


class Draft(StrictModel):
    checkpoint: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    sha256: SHA256


class ModelAsset(StrictModel):
    checkpoint: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    weights_sha256: SHA256
    tokenizer_sha256: SHA256
    template_sha256: SHA256
    quantization: str = Field(min_length=1)


class Settings(StrictModel):
    temperature: float = Field(ge=0, le=2)
    seed: int | None
    reasoning: bool


class Evidence(StrictModel):
    preflight_sha256: SHA256
    cache_control: str = Field(min_length=1)
    native_timing_audit_sha256: SHA256 | None = None


class Case(StrictModel):
    id: Identifier
    category: Literal["short", "2k", "8k", "32k", "long", "tools", "other"]
    kind: Literal["fresh", "replay"]
    max_tokens: int = Field(gt=0, le=32768)
    messages: list[dict[str, Any]] = Field(min_length=1)
    primary: bool

    @field_validator("messages")
    @classmethod
    def messages_valid(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for message in value:
            if message.get("role") not in {"system", "user", "assistant", "tool"}:
                raise ValueError("unsupported message role")
            if not isinstance(message.get("content"), (str, list)):
                raise ValueError("messages need explicit content")
        return value


class Manifest(StrictModel):
    schema_version: Literal[1]
    id: Identifier
    session: int = Field(gt=0)
    order: Literal["mlx-first", "splash-first"]
    backend: Literal["mlx", "splash"]
    base_url: str
    api_key_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] | None
    request_model: str = Field(min_length=1)
    response_model: str = Field(min_length=1)
    runtime: Runtime
    draft: Draft | None
    model: ModelAsset
    settings: Settings
    context_tokens: int = Field(gt=0)
    kv_precision: str = Field(min_length=1)
    cache_policy: Literal["disabled", "isolated", "replay"]
    concurrency: Literal[1, 2, 4]
    repeats: int = Field(gt=0, le=1000)
    warmups: Literal[1]
    deadline_s: float = Field(gt=0, le=7200)
    corpus_version: str = Field(min_length=1)
    corpus_sha256: SHA256
    cases: list[Case] = Field(min_length=1)
    rate_metric: Literal["backend_decode_tok_s", "client_output_wall_tok_s"]
    evidence: Evidence

    @field_validator("base_url")
    @classmethod
    def tunnel_only(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("use a credential-free loopback SSH tunnel URL")
        _ = url.port  # reject malformed ports before making an HTTP request
        return value.rstrip("/")

    @model_validator(mode="after")
    def frozen_contract(self) -> Self:
        if (
            self.rate_metric == "backend_decode_tok_s"
            and not self.evidence.native_timing_audit_sha256
        ):
            raise ValueError("native rates require a saved timing-definition audit")
        if self.corpus_sha256 != digest([c.model_dump() for c in self.cases]):
            raise ValueError("corpus_sha256 does not match the actual cases")
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate case id")
        if (self.backend == "splash") != (self.draft is not None):
            raise ValueError("Splash requires a pinned draft; MLX has none")
        if any((c.kind == "replay") != (self.cache_policy == "replay") for c in self.cases):
            raise ValueError("fresh and replay conditions need separate manifests")
        if self.cache_policy == "replay" and self.concurrency != 1:
            raise ValueError("replay histories must remain sequential")
        if any(c.max_tokens >= self.context_tokens for c in self.cases):
            raise ValueError("output limit must fit context")
        return self

    def comparison_key(self) -> str:
        shared = self.model_dump(
            exclude={
                "id",
                "session",
                "order",
                "backend",
                "base_url",
                "api_key_env",
                "request_model",
                "response_model",
                "runtime",
                "draft",
                "evidence",
            }
        )
        return digest({"contract": CONTRACT_VERSION, **shared})


class Measurement(StrictModel):
    ttft_s: float | None = None
    first_answer_s: float | None = None
    total_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    backend_decode_tok_s: float | None = None
    client_output_wall_tok_s: float | None = None
    finish_reason: str | None = None
    answer: str = ""
    reasoning: str = ""
    error: str | None = None


class Sample(StrictModel):
    case_id: str
    rep: int
    warmup: bool
    measurement: Measurement


class Run(StrictModel):
    contract: Literal["splash-phase0-2"] = CONTRACT_VERSION
    manifest: Manifest
    status: Literal["running", "complete", "interrupted", "failed"]
    samples: list[Sample] = Field(default_factory=list)
    error: str | None = None


def fresh_nonce(manifest: Manifest, case: Case, rep: int) -> str:
    return digest([manifest.corpus_sha256, manifest.session, case.id, rep])[:24]


def request_headers(manifest: Manifest, case: Case, rep: int) -> dict[str, str]:
    if manifest.backend == "mlx" and manifest.cache_policy == "isolated":
        return {"X-APC-Tenant": fresh_nonce(manifest, case, rep)}
    return {}


def request_body(manifest: Manifest, case: Case, rep: int) -> dict[str, Any]:
    messages = copy.deepcopy(case.messages)
    if case.kind == "fresh":
        # At the very beginning: a suffix cannot prevent prefix-cache reuse.
        nonce = fresh_nonce(manifest, case, rep)
        messages.insert(0, {"role": "system", "content": f"Benchmark instance {nonce}."})
    body: dict[str, Any] = {
        "model": manifest.request_model,
        "messages": messages,
        "max_tokens": case.max_tokens,
        "temperature": manifest.settings.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if manifest.backend == "splash" and manifest.cache_policy == "isolated":
        body["cache_salt"] = fresh_nonce(manifest, case, rep)
    if manifest.settings.seed is not None:
        body["seed"] = manifest.settings.seed
    if manifest.backend == "mlx":
        body["enable_thinking"] = manifest.settings.reasoning
    else:
        body["chat_template_kwargs"] = {"enable_thinking": manifest.settings.reasoning}
        if not manifest.settings.reasoning:
            body["reasoning_effort"] = "none"
    return body


class StreamFailure(ValueError):
    """Safe symbolic reason, never an upstream response body."""


async def sse_events(response: httpx.Response) -> AsyncIterator[str]:
    """HTTPX decodes UTF-8 and fragmented lines; dispatch complete SSE events."""
    data: list[str] = []
    event_name = ""
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if event_name == "error":
                raise StreamFailure("sse_error")
            event_name = ""
            if data:
                yield "\n".join(data)
                data, size = [], 0
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            part = line[5:].removeprefix(" ")
            size += len(part)
            if size > 4 * 1024 * 1024:
                raise StreamFailure("oversized_sse")
            data.append(part)
    # An unfinished event at EOF does not prove a complete generation.


def token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def apply_usage(m: Measurement, usage: dict[str, Any], max_tokens: int) -> None:
    if "prompt_tokens" not in usage or "completion_tokens" not in usage:
        raise StreamFailure("missing_token_counts")
    m.prompt_tokens = token_count(usage["prompt_tokens"])
    m.completion_tokens = token_count(usage["completion_tokens"])
    if not m.prompt_tokens or not m.completion_tokens or m.completion_tokens > max_tokens:
        raise StreamFailure("invalid_token_counts")
    for parent, field, limit in (
        ("prompt_tokens_details", "cached_tokens", m.prompt_tokens),
        ("completion_tokens_details", "reasoning_tokens", m.completion_tokens),
    ):
        details = usage.get(parent)
        if details is not None:
            if not isinstance(details, dict):
                raise StreamFailure("invalid_token_counts")
            if field in details:
                count = token_count(details[field])
                if count is None or count > limit:
                    raise StreamFailure("invalid_token_counts")
                setattr(m, field, count)


async def measure(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    *,
    response_model: str,
    deadline_s: float,
    clock: Callable[[], float] = time.monotonic,
    headers: dict[str, str] | None = None,
    prompt_timing_counts: Literal["total", "uncached"] = "total",
) -> Measurement:
    m = Measurement()
    t0 = clock()
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    saw_model = False
    done = False
    try:
        async with asyncio.timeout(deadline_s):
            async with client.stream(
                "POST", "/v1/chat/completions", json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    raise StreamFailure(f"http_{resp.status_code}")
                async for raw in sse_events(resp):
                    if raw == "[DONE]":
                        done = True
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        raise StreamFailure("malformed_sse") from None
                    if not isinstance(event, dict):
                        raise StreamFailure("malformed_sse")
                    if event.get("error"):
                        raise StreamFailure("sse_error")
                    if "model" in event:
                        if event["model"] != response_model:
                            raise StreamFailure("model_mismatch")
                        saw_model = True
                    for key in ("usage", "timings"):
                        if event.get(key) is not None and not isinstance(event[key], dict):
                            raise StreamFailure("malformed_sse")
                    if event.get("usage") is not None:
                        usage = event["usage"]
                    if event.get("timings") is not None:
                        timings = event["timings"]
                    choices = event.get("choices", [])
                    if not isinstance(choices, list):
                        raise StreamFailure("malformed_sse")
                    for choice in choices:
                        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                            raise StreamFailure("malformed_sse")
                        delta = choice.get("delta", {})
                        if not isinstance(delta, dict):
                            raise StreamFailure("malformed_sse")
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            if not isinstance(finish, str) or finish not in {
                                "length",
                                "stop",
                                "tool_calls",
                            }:
                                raise StreamFailure("invalid_finish_reason")
                            m.finish_reason = finish
                        for field in ("content", "reasoning_content", "reasoning"):
                            if delta.get(field) is not None and not isinstance(delta[field], str):
                                raise StreamFailure("malformed_sse")
                        content = delta.get("content") or ""
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if not isinstance(content, str) or not isinstance(reasoning, str):
                            raise StreamFailure("malformed_sse")
                        if (content or reasoning) and m.ttft_s is None:
                            m.ttft_s = clock() - t0
                            if content:
                                m.first_answer_s = m.ttft_s
                        elif content and m.first_answer_s is None:
                            m.first_answer_s = clock() - t0
                        m.answer += content
                        m.reasoning += reasoning
        m.total_s = clock() - t0
        if not done:
            raise StreamFailure("incomplete_stream")
        if m.finish_reason is None:
            raise StreamFailure("missing_finish_reason")
        if not saw_model:
            raise StreamFailure("missing_model_identity")
        apply_usage(m, usage, body["max_tokens"])
        if m.ttft_s is None or m.total_s <= 0:
            raise StreamFailure("missing_output")
        assert m.completion_tokens is not None
        m.client_output_wall_tok_s = m.completion_tokens / m.total_s
        timing_prompt_tokens = m.prompt_tokens
        if prompt_timing_counts == "uncached" and "prompt_n" in timings:
            if m.cached_tokens is None:
                raise StreamFailure("missing_cached_count_for_timings")
            assert m.prompt_tokens is not None
            timing_prompt_tokens = m.prompt_tokens - m.cached_tokens
        for field, expected in (
            ("predicted_n", m.completion_tokens),
            ("prompt_n", timing_prompt_tokens),
            ("cache_n", m.cached_tokens),
        ):
            if field in timings and (
                token_count(timings[field]) is None
                or (expected is not None and timings[field] != expected)
            ):
                raise StreamFailure("inconsistent_timing_counts")
        native = timings.get("predicted_per_second")
        if (
            isinstance(native, (int, float))
            and not isinstance(native, bool)
            and math.isfinite(native)
            and native > 0
        ):
            m.backend_decode_tok_s = float(native)
    except StreamFailure as exc:
        m.error = str(exc)
    except (TimeoutError, httpx.TimeoutException):
        m.error = "timeout"
    except httpx.HTTPError:
        m.error = "transport"
    if m.error:
        m.backend_decode_tok_s = m.client_output_wall_tok_s = None
    return m


def private_write(path: Path, text: str, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(os.open(path, flags, 0o600), "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def checkpoint(path: Path, run: Run) -> None:
    temporary = path.with_suffix(".tmp")
    private_write(temporary, run.model_dump_json(indent=2))
    temporary.replace(path)


async def run_manifest(
    manifest: Manifest,
    out: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> Run:
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out / f"{manifest.id}.json"
    run = Run(manifest=manifest, status="running")
    private_write(path, run.model_dump_json(indent=2), exclusive=True)
    if client is None:
        async with httpx.AsyncClient(
            base_url=manifest.base_url,
            trust_env=False,
            timeout=httpx.Timeout(manifest.deadline_s, connect=5),
        ) as http:
            return await _run(manifest, path, run, http)
    return await _run(manifest, path, run, client)


async def _run(manifest: Manifest, path: Path, run: Run, client: httpx.AsyncClient) -> Run:
    headers = {"Accept": "text/event-stream"}
    tasks: list[asyncio.Task[None]] = []
    try:
        if manifest.api_key_env:
            key = os.environ.get(manifest.api_key_env)
            if not key:
                raise StreamFailure("missing_api_key_environment")
            headers["Authorization"] = f"Bearer {key}"
        async with asyncio.timeout(min(manifest.deadline_s, 30)):
            models = await client.get(
                "/v1/models", headers={**headers, "Accept": "application/json"}
            )
            if models.status_code != 200:
                raise StreamFailure(f"models_http_{models.status_code}")
            try:
                ids = {row["id"] for row in models.json()["data"]}
            except (ValueError, KeyError, TypeError):
                raise StreamFailure("invalid_model_list") from None
            if manifest.request_model not in ids:
                raise StreamFailure("requested_model_not_served")
        semaphore = asyncio.Semaphore(manifest.concurrency)

        async def one(case: Case, rep: int) -> None:
            async with semaphore:
                try:
                    measurement = await measure(
                        client,
                        request_body(manifest, case, rep),
                        response_model=manifest.response_model,
                        deadline_s=manifest.deadline_s,
                        headers={**headers, **request_headers(manifest, case, rep)},
                        prompt_timing_counts="uncached" if manifest.backend == "mlx" else "total",
                    )
                except asyncio.CancelledError:
                    run.samples.append(
                        Sample(
                            case_id=case.id,
                            rep=rep,
                            warmup=rep == 0,
                            measurement=Measurement(error="cancelled"),
                        )
                    )
                    checkpoint(path, run)
                    raise
                run.samples.append(
                    Sample(case_id=case.id, rep=rep, warmup=rep == 0, measurement=measurement)
                )
                checkpoint(path, run)

        # Drain every round, including warm-up, before beginning the next.
        for rep in range(manifest.repeats + 1):
            if manifest.cache_policy == "replay":
                for case in manifest.cases:
                    await one(case, rep)
            else:
                tasks = [asyncio.create_task(one(case, rep)) for case in manifest.cases]
                await asyncio.gather(*tasks)
        run.status = "complete"
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        run.status = "interrupted"
        raise
    except StreamFailure as exc:
        run.status, run.error = "failed", str(exc)
    except (TimeoutError, httpx.TimeoutException):
        run.status, run.error = "failed", "preflight_timeout"
    except httpx.HTTPError:
        run.status, run.error = "failed", "preflight_transport"
    finally:
        checkpoint(path, run)
    return run


def load_runs(directory: Path) -> list[Run]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("no result files")
    runs: list[Run] = []
    for path in paths:
        try:
            runs.append(Run.model_validate_json(path.read_text()))
        except (ValueError, OSError):
            raise ValueError(
                "unreadable or malformed result; preserve it and inspect locally"
            ) from None
    return runs


def positive(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    at = (len(ordered) - 1) * q
    low = math.floor(at)
    high = math.ceil(at)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def bootstrap_ratio(blocks: list[list[float]]) -> list[float]:
    """Resample paired sessions, then prompts within sessions, not individual tokens."""
    rng = random.Random(0)
    ratios = []
    for _ in range(2000):
        selected = rng.choices(blocks, k=len(blocks))
        ratios.append(
            statistics.median(
                value for block in selected for value in rng.choices(block, k=len(block))
            )
        )
    return [percentile(ratios, 0.025), percentile(ratios, 0.975)]


def compare_runs(runs: list[Run]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "contract": CONTRACT_VERSION,
        "decision": "inconclusive",
        "speed_gate": "unassessed",
        "ttft_gate": "unassessed",
        "e2e_gate": "unassessed",
        "issues": [],
        "median_paired_ratio": None,
        "ratio_ci95": None,
        "categories": {},
        "samples": {"measured": 0, "warmup": 0, "errors": 0, "early_stops": 0},
        "pending_gates": [
            "Quality and compatibility",
            "Resources and service coexistence",
            "Context, ownership, start/stop cycles and restoration",
        ],
        "raw_runs": [],
    }
    issues: list[str] = report["issues"]
    for run in runs:
        try:
            raw_digest = digest(run.model_dump())
        except ValueError:
            raw_digest = None
            issues.append("invalid_result_artifact")
        report["raw_runs"].append({"id": run.manifest.id, "sha256": raw_digest})
    if not runs:
        issues.append("no_results")
        return report
    m = runs[0].manifest
    report["rate_metric"] = m.rate_metric
    report["comparison_sha256"] = m.comparison_key()
    if len({r.manifest.comparison_key() for r in runs}) != 1:
        issues.append("incompatible_manifests")
    if len({r.manifest.id for r in runs}) != len(runs):
        issues.append("duplicate_run")
    # A backend's runtime/draft may differ from the other backend, but not between blocks.
    for backend in ("mlx", "splash"):
        versions = {
            digest(
                [
                    r.manifest.runtime.model_dump(),
                    r.manifest.draft.model_dump() if r.manifest.draft else None,
                ]
            )
            for r in runs
            if r.manifest.backend == backend
        }
        if len(versions) > 1:
            issues.append("runtime_changed_between_sessions")
    sessions: dict[int, dict[str, Run]] = defaultdict(dict)
    index: dict[tuple[int, str, str], list[Measurement]] = defaultdict(list)
    for run in runs:
        rm = run.manifest
        if rm.backend in sessions[rm.session]:
            issues.append("duplicate_session_backend")
        sessions[rm.session][rm.backend] = run
        if run.status != "complete" or run.error:
            issues.append("incomplete_run")
        expected = {(case.id, rep) for case in rm.cases for rep in range(rm.repeats + 1)}
        actual = [(s.case_id, s.rep) for s in run.samples]
        if set(actual) != expected or len(actual) != len(expected):
            issues.append("incomplete_or_duplicate_samples")
        cases = {c.id: c for c in rm.cases}
        for s in sorted(run.samples, key=lambda item: item.rep):
            report["samples"]["warmup" if s.warmup else "measured"] += 1
            if s.warmup != (s.rep == 0):
                issues.append("invalid_warmup")
            v = s.measurement
            if v.error:
                report["samples"]["errors"] += 1
                issues.append("request_errors")
            case = cases.get(s.case_id)
            if not case or s.warmup:
                continue
            if (
                not positive(v.prompt_tokens)
                or type(v.prompt_tokens) is not int
                or not positive(v.completion_tokens)
                or type(v.completion_tokens) is not int
                or v.completion_tokens > case.max_tokens
                or not all(positive(x) for x in (v.ttft_s, v.total_s, getattr(v, m.rate_metric)))
            ):
                issues.append("missing_or_invalid_metrics")
                continue
            if (
                v.ttft_s is not None
                and v.total_s is not None
                and (
                    v.ttft_s > v.total_s
                    or (
                        v.first_answer_s is not None
                        and not v.ttft_s <= v.first_answer_s <= v.total_s
                    )
                )
            ):
                issues.append("invalid_timestamps")
            if v.prompt_tokens + case.max_tokens > rm.context_tokens:
                issues.append("prompt_exceeds_effective_context")
            bounds = {"short": (1, 1024), "2k": (1536, 3072), "8k": (6144, 10240)}
            if case.primary and case.category in bounds:
                low, high = bounds[case.category]
                if not low <= v.prompt_tokens <= high:
                    issues.append("prompt_outside_category")
            for count, limit in (
                (v.cached_tokens, v.prompt_tokens),
                (v.reasoning_tokens, v.completion_tokens),
            ):
                if count is not None and (type(count) is not int or not 0 <= count <= limit):
                    issues.append("invalid_token_details")
            if v.finish_reason != "length" or v.completion_tokens != case.max_tokens:
                report["samples"]["early_stops"] += 1
                issues.append("non_fixed_length_output")
            if rm.cache_policy in {"disabled", "isolated"} and v.cached_tokens != 0:
                issues.append("unverified_fresh_cache")
            if rm.cache_policy == "replay" and not positive(v.cached_tokens):
                issues.append("unverified_replay_cache")
            index[(rm.session, rm.backend, case.id)].append(v)
    if m.repeats < 5 or len(sessions) < 3:
        issues.append("insufficient_repetitions_or_sessions")
    orders: list[str] = []
    for _, pair in sorted(sessions.items()):
        if set(pair) != {"mlx", "splash"}:
            issues.append("unpaired_session")
            continue
        if pair["mlx"].manifest.order != pair["splash"].manifest.order:
            issues.append("inconsistent_session_order")
        orders.append(pair["mlx"].manifest.order)
    if len(set(orders)) < 2 or any(a == b for a, b in zip(orders, orders[1:], strict=False)):
        issues.append("sessions_not_counterbalanced")
    if issues:
        report["issues"] = sorted(set(issues))
        return report
    blocks: list[list[float]] = []
    categories: dict[str, list[float]] = defaultdict(list)
    ttft: dict[str, list[float]] = defaultdict(list)
    totals: dict[str, list[float]] = defaultdict(list)
    outputs: dict[str, list[int]] = defaultdict(list)
    e2e_ratios: list[float] = []
    for session in sorted(sessions):
        block = []
        for case in m.cases:
            a, b = (index[(session, backend, case.id)] for backend in ("mlx", "splash"))
            if len(a) != m.repeats or len(b) != m.repeats:
                report["issues"] = ["insufficient_valid_samples"]
                return report
            if any(av.prompt_tokens != bv.prompt_tokens for av, bv in zip(a, b, strict=True)):
                report["issues"] = ["paired_prompt_token_mismatch"]
                return report
            ratio = statistics.median(
                getattr(bv, m.rate_metric) / getattr(av, m.rate_metric)
                for av, bv in zip(a, b, strict=True)
            )
            e2e_ratios.append(
                statistics.median(v.total_s for v in b if v.total_s is not None)
                / statistics.median(v.total_s for v in a if v.total_s is not None)
            )
            if case.primary:
                block.append(ratio)
            categories[case.category].append(ratio)
            for backend, values in (("mlx", a), ("splash", b)):
                ttft[backend].extend(v.ttft_s for v in values if v.ttft_s is not None)
                totals[backend].extend(v.total_s for v in values if v.total_s is not None)
                outputs[backend].extend(
                    v.completion_tokens for v in values if v.completion_tokens is not None
                )
        if block:
            blocks.append(block)
    report["categories"] = {
        key: {"paired_ratio": statistics.median(values)} for key, values in categories.items()
    }
    report["latency"] = {
        backend: {
            "p95_ttft_s": percentile(ttft[backend], 0.95),
            "median_total_s": statistics.median(totals[backend]),
            "median_output_tokens": statistics.median(outputs[backend]),
        }
        for backend in ("mlx", "splash")
    }
    if not blocks:
        report["issues"] = ["no_primary_cases"]
        return report
    ratio = statistics.median(value for block in blocks for value in block)
    ci = bootstrap_ratio(blocks)
    report.update(median_paired_ratio=ratio, ratio_ci95=ci)
    if len(ttft["mlx"]) < 40 or len(ttft["splash"]) < 40:
        report["issues"].append("insufficient_p95_samples")
    else:
        report["ttft_gate"] = (
            "pass"
            if percentile(ttft["splash"], 0.95) <= 1.1 * percentile(ttft["mlx"], 0.95)
            else "fail"
        )
    report["e2e_gate"] = "pass" if all(r <= 1.0 for r in e2e_ratios) else "fail"
    primary_categories = {c.category for c in m.cases if c.primary}
    if (
        m.concurrency != 1
        or m.cache_policy == "replay"
        or primary_categories != {"short", "2k", "8k"}
    ):
        report["issues"].append("not_primary_single_request_matrix")
    elif m.rate_metric != "backend_decode_tok_s":
        report["issues"].append("client_wall_rate_is_not_native_decode")
    elif not report["issues"]:
        category_regression = any(
            statistics.median(categories[c]) < 1.0 for c in primary_categories
        )
        if ratio >= 1.25 and ci[0] > 1.0 and not category_regression:
            report["speed_gate"] = "pass"
        elif ci[1] < 1.25 or category_regression:
            report["speed_gate"] = "fail"
        else:
            report["issues"].append("confidence_interval_overlaps_boundary")
    if "fail" in (report["speed_gate"], report["ttft_gate"], report["e2e_gate"]):
        report["decision"] = "fail"
    return report


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Splash Phase 0 comparison",
        "",
        f"Decision: **{report['decision']}**",
        "",
        "This report assesses measurement gates only. Full adoption requires the separate evidence below.",
        "",
        f"Rate metric: `{report.get('rate_metric', 'unavailable')}`.",
        "Client output/wall rate includes prefill and HTTP overhead; it is not native decode throughput.",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    lines.extend(
        f"| {name} | {report[key]} |"
        for name, key in (
            ("Decode speed", "speed_gate"),
            ("p95 TTFT", "ttft_gate"),
            ("Fixed-length end-to-end", "e2e_gate"),
        )
    )
    lines += [
        "",
        f"Median paired ratio: {report['median_paired_ratio']}",
        f"95% paired session/prompt bootstrap interval: {report['ratio_ci95']}",
        "",
        f"Sample accounting: `{json.dumps(report['samples'], sort_keys=True)}`",
        "",
        "| Category | Median paired ratio |",
        "|---|---|",
    ]
    lines += [
        f"| {category} | {values['paired_ratio']:.4f} |"
        for category, values in report["categories"].items()
    ]
    if report.get("latency"):
        lines += [
            "",
            "Latency and actual output lengths:",
            "",
            "```json",
            json.dumps(report["latency"], indent=2, sort_keys=True),
            "```",
        ]
    lines += ["", "Unresolved measurement issues:", ""]
    lines += [f"- `{issue}`" for issue in report["issues"]] or ["- None"]
    lines += ["", "Required adoption evidence:", ""]
    lines += [f"- {gate}: pending" for gate in report["pending_gates"]]
    lines += ["", "Raw sample references (private artifact IDs and canonical SHA-256):", ""]
    lines += [f"- `{run['id']}`: `{run['sha256']}`" for run in report["raw_runs"]]
    return "\n".join(lines) + "\n"


def verify_restoration(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Read-only verification; never overwrite an external operator's intent."""
    issues = []
    required = {"intent", "sidecar_intent", "embed_intent", "recovery", "selected", "config_hashes"}
    if any(
        not isinstance(snapshot.get("restore"), dict)
        or not required.issubset(snapshot["restore"])
        or not isinstance(snapshot["restore"].get("intent"), dict)
        or not snapshot["restore"]["intent"].get("value")
        or not snapshot["restore"].get("config_hashes")
        for snapshot in (before, after)
    ):
        issues.append("incomplete_snapshot")
    if before.get("complete") is not True or after.get("complete") is not True:
        issues.append("incomplete_snapshot")
    if not before.get("restore") or before.get("restore") != after.get("restore"):
        issues.append("restore_state_changed")
    if after.get("serving_ready") is not True:
        issues.append("original_not_ready")
    if after.get("experiment_port_open") is not False:
        issues.append("experiment_port_open")
    if after.get("experiment_processes") != []:
        issues.append("experiment_processes_remain")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="measure an already running engine via a tunnel")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--results", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("--out", type=Path, required=True)
    restore = sub.add_parser("verify-restoration")
    restore.add_argument("--before", type=Path, required=True)
    restore.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "run":
            result = asyncio.run(
                run_manifest(Manifest.model_validate_json(args.manifest.read_text()), args.out)
            )
            return (
                0
                if result.status == "complete"
                and not any(s.measurement.error for s in result.samples)
                else 1
            )
        if args.command == "compare":
            report = compare_runs(load_runs(args.results))
            private_write(args.out, render_report(report), exclusive=True)
            print(f"Decision: {report['decision']}; speed gate: {report['speed_gate']}")
            return 0 if not report["issues"] and report["decision"] != "fail" else 1
        if args.command == "schema":
            private_write(args.out, json.dumps(Manifest.model_json_schema(), indent=2) + "\n")
            return 0
        if args.command == "verify-restoration":
            issues = verify_restoration(
                json.loads(args.before.read_text()), json.loads(args.after.read_text())
            )
            print(json.dumps({"restored": not issues, "issues": issues}))
            return 1 if issues else 0
    except (ValueError, OSError):
        # Pydantic error text includes input values; keep private manifests out of logs.
        print("Invalid input or artifact write failed; inspect private files locally.")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
