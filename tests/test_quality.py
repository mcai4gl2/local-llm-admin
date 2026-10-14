"""M2 §1: the measurement contract (cases, stream measurement, summaries, comparison)."""

import asyncio
import json
from dataclasses import fields

import httpx
import pytest

from local_llm_admin import quality as q
from local_llm_admin.chat import ACTIVE_MODEL_ID

from .fake_openai import FakeOpenAI, sse_chunks

USAGE = {
    "prompt_tokens": 131,
    "completion_tokens": 20,
    "total_tokens": 151,
    "prompt_tokens_details": {"cached_tokens": 0},
}
TIMINGS = {
    "prompt_n": 131,
    "cache_n": 0,
    "predicted_n": 20,
    "prompt_ms": 400.0,
    "prompt_per_second": 327.5,
    "predicted_ms": 500.0,
    "predicted_per_second": 40.0,
    "peak_memory": 17.5,
}


def _usage_stream(parts, usage=USAGE, timings=TIMINGS, reasoning=()):
    chunks = sse_chunks(parts, reasoning=reasoning)
    tail = {"choices": [], "usage": usage}
    if timings is not None:
        tail["timings"] = timings
    return [*chunks[:-1], f"data: {json.dumps(tail)}\n\n".encode(), chunks[-1]]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 0.25
        return self.t


async def _measure(chunks, *, status=200, deadline_s=30.0, delay=0.0):
    fake = FakeOpenAI(chunks, status=status, delay=delay)
    async with httpx.AsyncClient(base_url="http://mlx.test", transport=fake.transport) as http:
        m = await q.measure_stream(http, {"stream": True}, deadline_s=deadline_s, clock=Clock())
    return m, fake


# --- cases and payloads ------------------------------------------------------------


def test_benchmark_cases_cover_sizes_and_prefix_kinds():
    keys = {c.key: c for c in q.BENCHMARK_CASES}
    sizes = sorted(c.target_prompt_tokens for c in q.BENCHMARK_CASES if c.kind == "fresh")
    assert sizes == [128, 2048, 8192]
    assert {c.kind for c in keys.values()} == {"fresh", "repeat_prefix", "extended"}
    assert q.WARMUP == 1 and q.REPEATS == 3


def test_fresh_prompts_differ_per_repetition_but_repeated_prefix_is_identical():
    fresh = next(c for c in q.BENCHMARK_CASES if c.kind == "fresh")
    rep = next(c for c in q.BENCHMARK_CASES if c.kind == "repeat_prefix")
    assert q.build_messages(fresh, 0) != q.build_messages(fresh, 1)
    assert q.build_messages(rep, 0) == q.build_messages(rep, 3)


def test_extended_conversation_keeps_the_history_and_adds_a_turn():
    ext = next(c for c in q.BENCHMARK_CASES if c.kind == "extended")
    a, b = q.build_messages(ext, 0), q.build_messages(ext, 1)
    assert len(a) >= 3 and a[:-1] == b[:-1] and a[-1] != b[-1]
    assert [m["role"] for m in a[-3:]] == ["user", "assistant", "user"]


def test_prompt_size_scales_with_the_target():
    sizes = {}
    for c in q.BENCHMARK_CASES:
        if c.kind == "fresh":
            sizes[c.target_prompt_tokens] = len(json.dumps(q.build_messages(c, 0)))
    assert sizes[128] < sizes[2048] < sizes[8192]
    # roughly 4 characters per token of English filler
    assert 2048 * 2 < sizes[2048] < 2048 * 8


def test_benchmark_payload_is_deterministic_and_uses_the_full_model_id():
    msgs = [{"role": "user", "content": "hi"}]
    p = q.benchmark_payload(msgs)
    assert p["model"] == ACTIVE_MODEL_ID != "active"
    assert p["stream"] is True and p["stream_options"] == {"include_usage": True}
    assert p["temperature"] == 0 and p["seed"] == 0 and p["enable_thinking"] is False
    assert p["max_tokens"] == q.MAX_TOKENS
    assert p["messages"] == msgs


def test_settings_hash_changes_with_settings_and_runtime_fingerprint():
    base = q.settings_hash(q.EFFECTIVE_SETTINGS, "fp1")
    assert base == q.settings_hash(dict(q.EFFECTIVE_SETTINGS), "fp1")
    assert base != q.settings_hash({**q.EFFECTIVE_SETTINGS, "max_tokens": 64}, "fp1")
    assert base != q.settings_hash(q.EFFECTIVE_SETTINGS, "fp2")
    assert base != q.settings_hash(q.EFFECTIVE_SETTINGS, None)


# --- measuring one request ------------------------------------------------------------


async def test_measure_uses_backend_usage_and_timings():
    m, fake = await _measure(_usage_stream(["Hel", "lo"]))
    assert m.error is None and m.token_source == "backend"
    assert (m.prompt_tokens, m.completion_tokens, m.cached_tokens) == (131, 20, 0)
    assert m.backend_prefill_tok_s == 327.5 and m.backend_decode_tok_s == 40.0
    assert m.backend_peak_memory_gb == 17.5
    assert m.finish_reason == "stop" and m.content_chars == 5
    assert m.ttft_s is not None and m.total_s is not None and m.total_s > m.ttft_s
    # client-side rate is labelled separately and only uses backend token counts
    assert m.client_decode_tok_s == pytest.approx(20 / (m.total_s - m.ttft_s))


async def test_many_chunks_without_usage_fabricate_no_token_rate():
    m, _ = await _measure(sse_chunks(["x"] * 200))
    assert m.error is None and m.content_chars == 200
    assert m.token_source is None
    assert m.prompt_tokens is None and m.completion_tokens is None
    assert m.client_decode_tok_s is None and m.backend_decode_tok_s is None
    assert m.cached_tokens is None


async def test_usage_without_timings_leaves_backend_rates_null():
    m, _ = await _measure(_usage_stream(["a"], timings=None))
    assert m.completion_tokens == 20 and m.backend_decode_tok_s is None
    assert m.backend_prefill_tok_s is None and m.backend_peak_memory_gb is None


async def test_ttft_ignores_empty_role_deltas_and_counts_reasoning():
    role = b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
    only_role, _ = await _measure([role, *sse_chunks([])])
    assert only_role.ttft_s is None
    reasoning, _ = await _measure([role, *_usage_stream(["x"], reasoning=["think"])])
    assert reasoning.ttft_s is not None and reasoning.content_chars == 1


async def test_http_error_is_a_failed_measurement_with_status():
    m, _ = await _measure([], status=503)
    assert m.error == "http 503" and m.error_kind == "http"
    assert m.completion_tokens is None


async def test_transport_error_is_classified():
    def boom(request):
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(
        base_url="http://mlx.test", transport=httpx.MockTransport(boom)
    ) as http:
        m = await q.measure_stream(http, {}, deadline_s=5)
    assert m.error_kind == "transport" and "unreachable" in m.error


async def test_deadline_closes_the_stream():
    m, fake = await _measure(sse_chunks(["x"] * 50), delay=0.05, deadline_s=0.2)
    assert m.error_kind == "timeout"
    assert fake.stream.closed and fake.stream.sent < 50


async def test_cancellation_propagates_and_closes_the_stream():
    fake = FakeOpenAI(sse_chunks(["x"] * 50), delay=0.05)
    async with httpx.AsyncClient(base_url="http://mlx.test", transport=fake.transport) as http:
        task = asyncio.create_task(q.measure_stream(http, {}, deadline_s=30))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fake.stream.closed


async def test_malformed_lines_are_skipped():
    chunks = [b"data: {not json\n\n", b": keepalive\n\n", *_usage_stream(["ok"])]
    m, _ = await _measure(chunks)
    assert m.error is None and m.content_chars == 2


def test_every_measurement_field_has_unit_source_and_null_semantics():
    for f in fields(q.Measurement):
        if f.name in ("error", "error_kind", "token_source", "finish_reason"):
            continue
        spec = q.METRICS[f.name]
        assert spec.unit and spec.source and spec.null_means, f.name


# --- cache state, summaries and comparison --------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "cached", "state"),
    [
        (False, 0, "verified_cold"),
        (False, 12, "verified_warm"),
        (True, 12, "verified_warm"),
        (True, 0, "unknown"),  # a cache could still hold other prefixes
        (None, 0, "unknown"),
        (False, None, "unknown"),
    ],
)
def test_cache_state(enabled, cached, state):
    assert q.cache_state(enabled, cached) == state


def _rep(i, *, warmup=False, contaminated=False, error=None, decode=40.0, ttft=0.5):
    data = {
        "ttft_s": ttft,
        "total_s": 3.0,
        "prompt_tokens": 131,
        "completion_tokens": 20,
        "backend_decode_tok_s": decode,
        "backend_prefill_tok_s": 300.0,
        "client_decode_tok_s": None,
    }
    return {
        "rep": i,
        "warmup": warmup,
        "contaminated": contaminated,
        "error": error,
        "data": data,
    }


def test_summary_uses_medians_of_valid_repeats_only():
    reps = [
        _rep(0, warmup=True, decode=1.0),
        _rep(1, decode=40.0),
        _rep(2, decode=44.0),
        _rep(3, decode=38.0),
        _rep(4, contaminated=True, decode=5.0),
        _rep(5, error="http 500", decode=5.0),
    ]
    s = q.summarize(reps)
    assert s["valid"] == 3 and s["complete"] is True
    assert s["medians"]["backend_decode_tok_s"] == 40.0
    assert s["medians"]["client_decode_tok_s"] is None  # all null stays null, never zero
    assert s["excluded"] == {"warmup": 1, "contaminated": 1, "error": 1}


def test_summary_with_too_few_valid_repeats_is_incomplete():
    s = q.summarize([_rep(0, warmup=True), _rep(1), _rep(2, contaminated=True)])
    assert s["complete"] is False and s["valid"] == 1


def test_contamination_unknown_counts_as_valid_but_marks_uncontrolled():
    reps = [_rep(1), _rep(2), {**_rep(3), "contaminated": None}]
    s = q.summarize(reps)
    assert s["valid"] == 3 and s["controlled"] is False


def test_regression_flags_only_beyond_threshold_and_in_the_bad_direction():
    base = {"backend_decode_tok_s": 40.0, "ttft_s": 1.0, "backend_prefill_tok_s": 300.0}
    new = {"backend_decode_tok_s": 33.0, "ttft_s": 1.1, "backend_prefill_tok_s": 400.0}
    flags = q.regressions(new, base)
    assert [f["metric"] for f in flags] == ["backend_decode_tok_s"]
    assert flags[0]["change"] == pytest.approx(-0.175)
    slower = q.regressions({"ttft_s": 1.2}, {"ttft_s": 1.0})
    assert [f["metric"] for f in slower] == ["ttft_s"]


def test_regression_ignores_null_or_zero_baselines():
    assert q.regressions({"backend_decode_tok_s": 10.0}, {"backend_decode_tok_s": None}) == []
    assert q.regressions({"backend_decode_tok_s": None}, {"backend_decode_tok_s": 10.0}) == []
    assert q.regressions({"ttft_s": 1.0}, {"ttft_s": 0.0}) == []


# --- contamination and peak RSS --------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ({"in_flight": 0, "requests_started": 10}, {"requests_started": 11}, False),
        ({"in_flight": 0, "requests_started": 10}, {"requests_started": 12}, True),
        ({"in_flight": 1, "requests_started": 10}, {"requests_started": 11}, True),
        (None, {"requests_started": 11}, None),
        ({"in_flight": 0, "requests_started": 10}, None, None),
        # a server restart resets the counters: cannot tell
        ({"in_flight": 0, "requests_started": 10}, {"requests_started": 1}, None),
    ],
)
def test_contamination(before, after, expected):
    assert q.contaminated(before, after) is expected


async def test_peak_rss_sampler_records_the_maximum():
    values = iter([100, 300, 200])

    def rss():
        return next(values, 150)

    sampler = q.PeakSampler(rss, interval_s=0.01)
    async with sampler:
        await asyncio.sleep(0.08)
    assert sampler.peak == 300 and sampler.samples >= 3
    assert sampler.interval_s == 0.01


async def test_peak_rss_sampler_samples_a_short_request_at_both_ends():
    values = iter([100, 250])
    sampler = q.PeakSampler(lambda: next(values, 50), interval_s=10)
    async with sampler:
        pass
    assert sampler.peak == 250 and sampler.samples >= 2


async def test_peak_rss_sampler_tolerates_a_vanished_process():
    def rss():
        return None

    sampler = q.PeakSampler(rss, interval_s=0.01)
    async with sampler:
        await asyncio.sleep(0.03)
    assert sampler.peak is None


async def test_httpx_read_timeout_is_a_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow")

    async with httpx.AsyncClient(
        base_url="http://mlx.test", transport=httpx.MockTransport(slow)
    ) as http:
        m = await q.measure_stream(http, {}, deadline_s=5)
    assert m.error_kind == "timeout" and m.as_dict()["error_kind"] == "timeout"


def test_malformed_metrics_snapshot_means_unknown():
    assert (
        q.contaminated({"requests_started": "x", "in_flight": 0}, {"requests_started": 1}) is None
    )
    assert q.contaminated({}, {"requests_started": 1}) is None


async def test_partial_usage_keeps_missing_fields_null():
    m, _ = await _measure(_usage_stream(["a"], usage={"prompt_tokens": 5}, timings=None))
    assert m.prompt_tokens == 5
    assert m.completion_tokens is None and m.cached_tokens is None
    assert m.token_source is None and m.client_decode_tok_s is None
