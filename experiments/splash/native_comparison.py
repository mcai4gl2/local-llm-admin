#!/usr/bin/env python3
"""Review a fresh, source-pinned native callback-emission series; never retrofit old rates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import bench
import native_timing
import native_validation
import overhead_validation
from pydantic import Field

Backend = Literal["mlx", "splash"]
Cohort = Literal["stock-8bit", "modified-4bit"]


class SourcePins(bench.StrictModel):
    source_sha256: bench.SHA256
    adapter_sha256: bench.SHA256


class Eligibility(SourcePins):
    cohort: Cohort
    backend: Backend
    evidence_sha256: bench.SHA256
    runtime_sha256: bench.SHA256
    model_pins: overhead_validation.ModelPins
    decision: Literal["eligible"]
    threshold: float = Field(gt=0)
    median_wall_ratio: float = Field(gt=0, le=1.05)
    category_upper95: dict[str, float]


class FrozenInput(bench.StrictModel):
    session: int = Field(ge=1, le=3)
    backend: Backend
    case_id: str
    rep: int = Field(ge=0, le=5)
    nonce: str
    body: dict[str, Any]
    headers: dict[str, str]
    body_sha256: bench.SHA256
    native_prompt_sha256: bench.SHA256
    decoded_prompt: str
    prompt_tokens: int = Field(gt=0)


class Design(bench.StrictModel):
    contract: Literal["splash-native-comparison-design-1"]
    cohort: Cohort
    boundary_audit_sha256: bench.SHA256
    profiles: dict[Backend, bench.Manifest]
    sources: dict[Backend, SourcePins]
    inputs: list[FrozenInput]


class NativeSample(bench.Sample):
    body: dict[str, Any]
    headers: dict[str, str]
    trace: native_timing.NativeTrace | None
    companion: dict[str, Any] | None


class NativeRun(bench.StrictModel):
    contract: Literal["splash-native-comparison-1"]
    cohort: Cohort
    manifest: bench.Manifest
    status: Literal["running", "complete", "interrupted", "failed"]
    error: str | None = None
    samples: list[NativeSample]


def request(
    cohort: Cohort, manifest: bench.Manifest, case: bench.Case, rep: int
) -> tuple[str, dict[str, Any], dict[str, str]]:
    nonce = bench.digest(
        [
            "splash-native-comparison-1",
            cohort,
            manifest.model.weights_sha256,
            manifest.corpus_sha256,
            manifest.session,
            case.id,
            rep,
        ]
    )[:24]
    body = bench.request_body(manifest, case, rep)
    body["messages"][0] = {"role": "system", "content": f"Benchmark instance {nonce}."}
    headers = {"X-APC-Tenant": nonce} if manifest.backend == "mlx" else {}
    if manifest.backend == "splash":
        body["cache_salt"] = nonce
    return nonce, body, headers


def _design_valid(design: Design) -> None:
    assert set(design.profiles) == set(design.sources) == {"mlx", "splash"}
    assert len({m.comparison_key() for m in design.profiles.values()}) == 1
    for backend, m in design.profiles.items():
        assert m.backend == backend and m.context_tokens == 32768
        assert m.repeats == 5 and m.warmups == 1 and m.concurrency == 1
        assert m.cache_policy == "isolated" and m.api_key_env is None
        assert m.settings.temperature == 0 and m.settings.seed == 0 and not m.settings.reasoning
        assert m.rate_metric == "backend_decode_tok_s"
        assert m.evidence.native_timing_audit_sha256 == design.boundary_audit_sha256
        assert len(m.cases) == 3 and {c.category for c in m.cases} == {"short", "2k", "8k"}
        assert all(
            c.primary
            and c.kind == "fresh"
            and c.max_tokens == (512 if c.category == "short" else 1024)
            for c in m.cases
        )
    expected = {
        (session, backend, c.id, rep)
        for session in range(1, 4)
        for backend, m in design.profiles.items()
        for c in m.cases
        for rep in range(6)
    }
    keys = [(r.session, r.backend, r.case_id, r.rep) for r in design.inputs]
    assert len(keys) == 108 and set(keys) == expected
    nonces: dict[str, tuple[int, str, int]] = {}
    native_hashes: set[tuple[str, str]] = set()
    for r in design.inputs:
        m = design.profiles[r.backend].model_copy(update={"session": r.session}, deep=True)
        c = next(c for c in m.cases if c.id == r.case_id)
        nonce, body, headers = request(design.cohort, m, c, r.rep)
        assert (r.nonce, r.body, r.headers) == (nonce, body, headers)
        assert r.body_sha256 == bench.digest(body)
        assert r.decoded_prompt.count(f"Benchmark instance {nonce}.") == 1
        assert r.prompt_tokens + c.max_tokens <= m.context_tokens
        group = (r.session, r.case_id, r.rep)
        assert nonce not in nonces or nonces[nonce] == group
        nonces[nonce] = group
        identity = (r.backend, r.native_prompt_sha256)
        assert identity not in native_hashes
        native_hashes.add(identity)
        if r.backend == "mlx":
            assert hashlib.sha256(r.decoded_prompt.encode()).hexdigest() == r.native_prompt_sha256


def _proofs_valid(design: Design, proofs: dict[str, Any]) -> dict[Backend, Eligibility]:
    assert set(proofs) == {"mlx", "splash"}
    parsed = {b: Eligibility.model_validate(proofs[b]) for b in design.profiles}
    for backend, proof in parsed.items():
        m, source = design.profiles[backend], design.sources[backend]
        assert proof.backend == backend and proof.cohort == design.cohort
        assert proof.threshold == 1.05
        assert (
            proof.source_sha256 == source.source_sha256
            and proof.adapter_sha256 == source.adapter_sha256
        )
        assert proof.runtime_sha256 == m.runtime.sha256
        assert proof.model_pins.model_dump() == {
            k: getattr(m.model, k)
            for k in ["weights_sha256", "tokenizer_sha256", "template_sha256"]
        }
        assert set(proof.category_upper95) == {"short", "2k", "8k"}
        assert all(bench.positive(v) and v <= 1.05 for v in proof.category_upper95.values())
    return parsed


def compare_native(
    runs: list[dict[str, Any]], design: dict[str, Any], eligibility: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": "splash-native-comparison-1",
        "decision": "inconclusive",
        "emission_speed_gate": "unassessed",
        "native_gpu_compute_gate": "unassessed",
        "ttft_gate": "unassessed",
        "e2e_gate": "unassessed",
        "issues": [],
        "scope": "First-to-last native token-delivery callback interval, excluding the entire first batch; includes runtime delivery/IPC, not isolated GPU compute.",
    }
    try:
        frozen = Design.model_validate(design)
        _design_valid(frozen)
        proofs = _proofs_valid(frozen, eligibility)
        parsed = [NativeRun.model_validate(run) for run in runs]
    except (ValueError, AssertionError, KeyError, StopIteration, TypeError):
        result["issues"] = ["invalid_or_ineligible_artifact"]
        return result
    result.update(cohort=frozen.cohort, design_sha256=bench.digest(frozen.model_dump()))
    keys = [(r.manifest.session, r.manifest.backend) for r in parsed]
    if len(keys) != 6 or set(keys) != {(s, b) for s in range(1, 4) for b in ["mlx", "splash"]}:
        result["issues"] = ["missing_or_duplicate_runs"]
        return result
    indexed = {(r.session, r.backend, r.case_id, r.rep): r for r in frozen.inputs}
    projected: list[bench.Run] = []
    accounted = 0
    try:
        for run in parsed:
            m = run.manifest
            source, original = frozen.sources[m.backend], frozen.profiles[m.backend]
            assert run.cohort == frozen.cohort and run.status == "complete" and run.error is None
            assert m.comparison_key() == original.comparison_key()
            assert m.runtime == original.runtime and m.draft == original.draft
            assert (
                m.request_model == original.request_model
                and m.response_model == original.response_model
            )
            assert m.evidence.native_timing_audit_sha256 == frozen.boundary_audit_sha256
            assert m.order == ("splash-first" if m.session == 2 else "mlx-first")
            expected = {(c.id, r) for c in m.cases for r in range(6)}
            actual = [(s.case_id, s.rep) for s in run.samples]
            assert len(actual) == 18 and set(actual) == expected
            samples: list[bench.Sample] = []
            for s in run.samples:
                r = indexed[(m.session, m.backend, s.case_id, s.rep)]
                c = next(c for c in m.cases if c.id == s.case_id)
                v = s.measurement
                assert s.warmup == (s.rep == 0) and (s.body, s.headers) == (r.body, r.headers)
                assert v.error is None and v.cached_tokens == 0 and v.finish_reason == "length"
                assert v.completion_tokens == c.max_tokens and v.prompt_tokens == r.prompt_tokens
                assert not v.reasoning and v.reasoning_tokens in (None, 0) and v.answer
                assert all(bench.positive(t) for t in [v.total_s, v.ttft_s, v.first_answer_s])
                assert (
                    v.total_s is not None and v.ttft_s is not None and v.first_answer_s is not None
                )
                assert v.ttft_s <= v.first_answer_s <= v.total_s
                assert s.trace is not None and s.companion is not None
                native = s.companion["native_request"]
                actual_hash = (
                    hashlib.sha256(native["rendered_prompt"].encode()).hexdigest()
                    if m.backend == "mlx"
                    else bench.digest(native["prompt_tokens"])
                )
                assert actual_hash == r.native_prompt_sha256
                checked = native_validation.review_trace(
                    s.trace,
                    s.companion,
                    v,
                    backend=m.backend,
                    source_sha256=source.source_sha256,
                    adapter_sha256=source.adapter_sha256,
                    nonce=r.nonce,
                    decoded_prompt=r.decoded_prompt,
                    max_tokens=c.max_tokens,
                    reasoning=False,
                )
                assert checked["elapsed_s"] <= v.total_s
                measured = v.model_copy(deep=True)
                measured.backend_decode_tok_s = checked["emission_tok_s"]
                samples.append(
                    bench.Sample(
                        case_id=s.case_id, rep=s.rep, warmup=s.warmup, measurement=measured
                    )
                )
                accounted += 1
            projected.append(bench.Run(manifest=m, status="complete", samples=samples))
    except (ValueError, AssertionError, KeyError, StopIteration, TypeError, AttributeError):
        result["issues"] = ["incompatible_or_incomplete_capture"]
        return result
    summary = bench.compare_runs(projected)
    # Only allowlisted aggregates leave this reviewer. Private model names/prompts/IDs stay private.
    for key in [
        "ttft_gate",
        "e2e_gate",
        "issues",
        "median_paired_ratio",
        "ratio_ci95",
        "categories",
        "latency",
        "samples",
    ]:
        result[key] = summary[key]
    result.update(
        emission_speed_gate=summary["speed_gate"],
        accounted_native_traces=accounted,
        private_runs_sha256=bench.digest(
            [
                r.model_dump()
                for r in sorted(parsed, key=lambda r: (r.manifest.session, r.manifest.backend))
            ]
        ),
        collector_evidence_sha256={b: p.evidence_sha256 for b, p in proofs.items()},
        boundary_audit_sha256=frozen.boundary_audit_sha256,
        uncertainty="2000 seed-0 bootstrap draws over paired sessions, then primary prompts; median of per-prompt paired repetition medians.",
    )
    if "fail" in (result["emission_speed_gate"], result["ttft_gate"], result["e2e_gate"]):
        result["decision"] = "fail"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["runs", "design", "eligibility", "out"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_native(
            json.loads(args.runs.read_text()),
            json.loads(args.design.read_text()),
            json.loads(args.eligibility.read_text()),
        )
        bench.private_write(args.out, json.dumps(result, indent=2) + "\n", exclusive=True)
    except (ValueError, OSError, TypeError):
        print(
            "Invalid native comparison evidence or unavailable output; inspect private artifacts."
        )
        return 2
    print("Fresh native emission series reviewed; broader adoption remains unqualified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
