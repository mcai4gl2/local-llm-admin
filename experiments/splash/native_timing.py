#!/usr/bin/env python3
"""Inspect a proposed engine-callback trace. Structural validity never proves an audit."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import bench
from pydantic import Field, model_validator


class TokenBatch(bench.StrictModel):
    at_ns: int = Field(ge=0, le=2**63 - 1)
    token_ids: list[Annotated[int, Field(ge=0)]] = Field(min_length=1)


class NativeTrace(bench.StrictModel):
    contract: Literal["splash-native-emission-1"]
    backend: Literal["mlx", "splash"]
    runtime_sha256: bench.SHA256
    adapter_sha256: bench.SHA256
    request_sha256: bench.SHA256
    source: Literal["engine_callback_entry"]
    clock: Literal["monotonic_ns"]
    completion_tokens: int = Field(gt=1)
    completion_ids_sha256: bench.SHA256
    finish_reason: Literal["length", "stop"]
    batches: list[TokenBatch] = Field(min_length=2)

    @model_validator(mode="after")
    def accounted(self) -> Self:
        ids = [token for batch in self.batches for token in batch.token_ids]
        if len(ids) != self.completion_tokens or bench.digest(ids) != self.completion_ids_sha256:
            raise ValueError("callback tokens do not match independently finalized completion IDs")
        times = [batch.at_ns for batch in self.batches]
        if times != sorted(times) or times[-1] <= times[0]:
            raise ValueError("callback interval must be monotonic and positive")
        return self


def inspect_trace(trace: NativeTrace) -> dict[str, Any]:
    trace = NativeTrace.model_validate(trace.model_dump())
    count = trace.completion_tokens - len(trace.batches[0].token_ids)
    elapsed = (trace.batches[-1].at_ns - trace.batches[0].at_ns) / 1_000_000_000
    return {
        "contract": trace.contract,
        "status": "structurally_valid_only",
        "native_gate": "unassessed",
        "trace_sha256": bench.digest(trace.model_dump()),
        "post_first_batch_tokens": count,
        "elapsed_s": elapsed,
        "emission_tok_s": count / elapsed,
        "remaining_evidence": "Pinned runtime adapters and equivalent collection points require review.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        trace = NativeTrace.model_validate_json(args.trace.read_text())
        result = inspect_trace(trace)
        bench.private_write(args.out, json.dumps(result, indent=2) + "\n", exclusive=True)
    except (ValueError, OSError):
        print("Invalid trace or unavailable output; inspect the private artifacts locally.")
        return 2
    print("Trace is structurally valid; native qualification remains unassessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
