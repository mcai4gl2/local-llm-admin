"""Splash 1.4.0 readings; no polling, request-history inference or database writes.

See docs/splash-telemetry.md. Response lifecycle intervals and native execution
counters have different populations and must not be reconciled implicitly.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .backend_types import EngineObservation, generation_record

Outcome = Literal["completed", "partial", "error", "cancelled"]
SPLASH_SOURCE = "splash"


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _seconds(value: object) -> float | None:
    number = _number(value)
    return number / 1000 if number is not None else None


def _detail(value: object, total: int | None) -> int | None:
    count = _count(value)
    return count if count is not None and (total is None or count <= total) else None


@dataclass(frozen=True)
class ResponseTelemetry:
    outcome: Outcome
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    ttft_s: float | None
    generation_s: float | None
    elapsed_s: float | None
    stream_tok_s: float | None


def normalize_response(document: object, *, outcome: Outcome) -> ResponseTelemetry:
    """Normalize one decoded response/final usage chunk, with caller-known outcome.

    Missing fields stay null. Counts do not establish stream completion, and these
    intervals never become native GPU timings or absolute request timestamps.
    """
    doc = _object(document)
    usage = _object(doc.get("usage"))
    latency = _object(_object(doc.get("metrics")).get("request_latency"))
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    return ResponseTelemetry(
        outcome=outcome,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=_detail(
            _object(usage.get("prompt_tokens_details")).get("cached_tokens"), prompt
        ),
        reasoning_tokens=_detail(
            _object(usage.get("completion_tokens_details")).get("reasoning_tokens"), completion
        ),
        ttft_s=_seconds(latency.get("ttft_ms")),
        generation_s=_seconds(latency.get("first_token_to_done_ms")),
        elapsed_s=_seconds(latency.get("wall_ms")),
        stream_tok_s=_number(latency.get("stream_tokens_per_second")),
    )


@dataclass(frozen=True)
class CounterTelemetry:
    submitted: int | None
    completed: int | None
    cancelled: int | None
    failed: int | None
    prefill_input_tokens: int | None
    decode_output_tokens: int | None
    cache_reused_tokens: int | None
    restarts: int | None
    status_stale: bool | None


def normalize_counters(document: object) -> CounterTelemetry:
    """Allowlist native counters without assigning a timestamp, epoch or profile.

    This is not an ingestible row: collection must first verify generation,
    freshness, reset scope and population. Never fill missing counters with zero.
    """
    doc = _object(document)
    requests, metrics = _object(doc.get("requests")), _object(doc.get("metrics"))
    transport = _object(doc.get("transport"))
    stale = transport.get("status_stale")
    return CounterTelemetry(
        submitted=_count(requests.get("submitted")),
        completed=_count(requests.get("completed")),
        cancelled=_count(requests.get("cancelled")),
        failed=_count(requests.get("failed")),
        prefill_input_tokens=_count(metrics.get("prefill_input_tokens")),
        decode_output_tokens=_count(metrics.get("decode_output_tokens")),
        cache_reused_tokens=_count(_object(doc.get("cache")).get("reused_tokens")),
        restarts=_count(transport.get("restarts")),
        status_stale=stale if type(stale) is bool else None,
    )


def source_coverage(enabled: bool) -> dict[str, Any]:
    """Capabilities are evidence, not an assertion that collection is running."""
    return {
        "source": SPLASH_SOURCE,
        "runtime": "1.4.0",
        "enabled": enabled,
        "coverage": "not_collected" if enabled else "disabled",
        "per_request_history": "unsupported",
        "response_usage": "request_scoped",
        "aggregate_counters": "available_unqualified",
        "last_poll": None,
        "last_ingest": None,
        "reason": "Splash usage is not collected. Complete request history is unsupported; "
        "native counters require generation and reset validation before ingestion.",
    }


def bind_reading(
    before: EngineObservation, after: EngineObservation, document: object, *, model: str
) -> dict[str, Any] | None:
    """Bind a fresh native snapshot to verified local generations on both sides."""
    if (
        before.backend != "splash"
        or after.backend != "splash"
        or before.ownership != "verified"
        or after.ownership != "verified"
        or before.identity != "verified"
        or after.identity != "verified"
        or not before.reference
        or before.reference != after.reference
        or generation_record(before) != generation_record(after)
    ):
        return None
    controllers = [p for p in before.processes if p.role == "controller"]
    natives = [p for p in before.processes if p.role == "native"]
    fp = (before.generation or {}).get("profile_fingerprint")
    if len(controllers) != 1 or len(natives) != 1 or not isinstance(fp, str) or not fp:
        return None
    doc = _object(document)
    instance, transport = _object(doc.get("instance")), _object(doc.get("transport"))
    instance_id = instance.get("id")
    restarts = _count(transport.get("restarts"))
    age = _number(transport.get("status_age_ms"))
    if (
        type(instance.get("pid")) is not int
        or instance["pid"] != controllers[0].pid
        or instance.get("model") != model
        or not isinstance(instance_id, str)
        or not 1 <= len(instance_id) <= 128
        or restarts is None
        or transport.get("status_stale") is not False
        or age is None
        or age != 0
    ):
        return None
    identity = {
        "profile": before.reference,
        "fingerprint": fp,
        "model": model,
        "instance": instance_id,
        "restarts": restarts,
        "processes": [
            {"pid": p.pid, "started": p.started, "role": p.role}
            for p in sorted(before.processes, key=lambda p: p.role)
        ],
    }
    epoch = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "source": SPLASH_SOURCE,
        "epoch": epoch,
        "profile": before.reference,
        "fingerprint": fp,
        "model": model,
        "identity": identity,
        "counters": asdict(normalize_counters(doc)),
    }
