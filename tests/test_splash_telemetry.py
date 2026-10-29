"""Pinned response/counter normalization, without inference rows or a polling loop."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.splash_telemetry import (
    Outcome,
    normalize_counters,
    normalize_response,
    source_coverage,
)

FIX = Path(__file__).parent / "fixtures"


def final_response(name: str = "splash-chat.sse") -> dict[str, Any]:
    chunks = [
        json.loads(line[6:])
        for line in (FIX / "splash-phase0" / name).read_text().splitlines()
        if line.startswith("data: {")
    ]
    return next(c for c in chunks if c.get("usage"))


@pytest.mark.parametrize("name", ["splash-chat.sse", "modified-4bit-splash-chat.sse"])
def test_recorded_response_preserves_usage_and_runtime_interval_boundaries(name: str) -> None:
    doc = final_response(name)
    r = normalize_response(doc, outcome="completed")
    assert r.prompt_tokens == doc["usage"]["prompt_tokens"]
    assert r.completion_tokens == 96
    assert r.cached_tokens == 0 and r.reasoning_tokens == 0
    latency = doc["metrics"]["request_latency"]
    assert r.ttft_s == latency["ttft_ms"] / 1000
    assert r.generation_s == latency["first_token_to_done_ms"] / 1000
    assert r.elapsed_s == latency["wall_ms"] / 1000
    assert r.stream_tok_s == latency["stream_tokens_per_second"]
    assert r.outcome == "completed"
    assert "decode_tok_s" not in asdict(r)  # no native/GPU claim
    assert not any(k in asdict(r) for k in ("started", "finished", "model", "created", "content"))


@pytest.mark.parametrize(
    "bad", [None, True, False, -1, 1.5, "2", float("nan"), float("inf"), [], {}]
)
def test_invalid_counts_stay_unknown(bad: Any) -> None:
    r = normalize_response(
        {
            "usage": {
                "prompt_tokens": bad,
                "completion_tokens": bad,
                "prompt_tokens_details": {"cached_tokens": bad},
                "completion_tokens_details": {"reasoning_tokens": bad},
            }
        },
        outcome="completed",
    )
    assert r.prompt_tokens is r.completion_tokens is r.cached_tokens is r.reasoning_tokens is None


@pytest.mark.parametrize(
    "bad", [None, True, False, -1, "2", float("nan"), float("inf"), 10**1000, [], {}]
)
def test_invalid_timings_stay_unknown(bad: Any) -> None:
    r = normalize_response(
        {
            "metrics": {
                "request_latency": {
                    k: bad
                    for k in (
                        "ttft_ms",
                        "first_token_to_done_ms",
                        "wall_ms",
                        "stream_tokens_per_second",
                    )
                }
            }
        },
        outcome="completed",
    )
    assert r.ttft_s is r.generation_s is r.elapsed_s is r.stream_tok_s is None


def test_zero_is_measured_and_nested_counts_cannot_exceed_totals() -> None:
    r = normalize_response(
        {
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 1},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
            "metrics": {"request_latency": {"ttft_ms": 0, "wall_ms": 0}},
        },
        outcome="completed",
    )
    assert r.prompt_tokens == r.completion_tokens == r.ttft_s == r.elapsed_s == 0
    assert r.cached_tokens is r.reasoning_tokens is None


@pytest.mark.parametrize(
    "doc",
    [
        None,
        [],
        True,
        "bad",
        {},
        {"usage": []},
        {"metrics": []},
        {"usage": {"prompt_tokens_details": [], "completion_tokens_details": False}},
        {"metrics": {"request_latency": "bad"}},
        {"timings": {"predicted_per_second": 50}},
    ],
)
def test_missing_or_malformed_documents_do_not_invent_usage(doc: Any) -> None:
    r = normalize_response(doc, outcome="partial")
    assert all(v is None for k, v in asdict(r).items() if k != "outcome")


@pytest.mark.parametrize("outcome", ["completed", "partial", "error", "cancelled"])
def test_response_counts_do_not_promote_an_incomplete_outcome(outcome: Outcome) -> None:
    r = normalize_response(final_response(), outcome=outcome)
    assert r.outcome == outcome and r.completion_tokens == 96


def test_recorded_counter_projection_is_distinct_from_billed_response_tokens() -> None:
    captures = json.loads((FIX / "splash/telemetry-captures.json").read_text())["captures"]
    for capture in captures:
        doc = capture["status_projection"]
        r = normalize_counters(doc)
        assert r.completed == doc["requests"]["completed"]
        assert r.prefill_input_tokens == doc["metrics"]["prefill_input_tokens"]
        assert r.decode_output_tokens == doc["metrics"]["decode_output_tokens"]
        assert r.restarts == doc["transport"]["restarts"]
        assert r.status_stale == doc["transport"]["status_stale"]
        assert "prompt_tokens" not in asdict(r)
        assert "started" not in asdict(r)


@pytest.mark.parametrize(
    "doc",
    [
        None,
        [],
        {},
        {"requests": [], "metrics": [], "transport": []},
        {
            "requests": {"submitted": True, "completed": -1, "failed": 1.5, "cancelled": "0"},
            "metrics": {"decode_output_tokens": float("inf")},
            "transport": {"restarts": True, "status_stale": 0},
        },
    ],
)
def test_malformed_or_absent_counters_are_unknown(doc: Any) -> None:
    assert all(v is None for v in asdict(normalize_counters(doc)).values())


def test_missing_nested_details_are_unknown_even_with_valid_totals() -> None:
    r = normalize_response(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}, outcome="completed"
    )
    assert r.prompt_tokens == 10 and r.completion_tokens == 5
    assert r.cached_tokens is r.reasoning_tokens is None


@pytest.mark.parametrize("enabled", [False, True])
def test_source_coverage_never_claims_polling_or_ingestion(enabled: bool) -> None:
    s = source_coverage(enabled)
    assert s["source"] == "splash"
    assert s["coverage"] == ("not_collected" if enabled else "disabled")
    assert s["last_poll"] is s["last_ingest"] is None
    assert s["per_request_history"] == "unsupported"
    assert s["response_usage"] == "request_scoped"


def test_distinct_synthetic_counters_keep_native_names_and_freshness() -> None:
    r = normalize_counters(
        {
            "requests": {"submitted": 13, "completed": 7, "cancelled": 3, "failed": 2},
            "metrics": {"prefill_input_tokens": 101, "decode_output_tokens": 37},
            "cache": {"reused_tokens": 19},
            "transport": {"restarts": 5, "status_stale": True},
        }
    )
    assert asdict(r) == {
        "submitted": 13,
        "completed": 7,
        "cancelled": 3,
        "failed": 2,
        "prefill_input_tokens": 101,
        "decode_output_tokens": 37,
        "cache_reused_tokens": 19,
        "restarts": 5,
        "status_stale": True,
    }


def test_explicit_nested_usage_can_be_measured_with_or_without_parent_totals() -> None:
    for totals in ({}, {"prompt_tokens": 100, "completion_tokens": 20}):
        r = normalize_response(
            {
                "usage": {
                    **totals,
                    "prompt_tokens_details": {"cached_tokens": 10},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                }
            },
            outcome="partial",
        )
        assert r.cached_tokens == 10 and r.reasoning_tokens == 5
        assert r.outcome == "partial"
