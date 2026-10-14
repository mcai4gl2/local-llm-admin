"""M2 measurement contract: benchmark cases, one measured request, summaries, comparison.

Token counts and rates come only from the server's ``usage``/``timings`` (M2
capability survey, pre-split design notes). A stream without them yields null counts and
rates: SSE chunks are never counted as tokens. Every numeric field of ``Measurement``
has an entry in ``METRICS`` saying its unit, where it comes from and what null means.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from types import TracebackType
from typing import Any

import httpx

from .chat import ACTIVE_MODEL_ID

CASE_VERSION = "1"
MAX_TOKENS = 128
WARMUP = 1
REPEATS = 3
MIN_VALID = 2
REGRESSION_THRESHOLD = 0.15
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

# what the benchmark sends besides messages; part of the comparability key
EFFECTIVE_SETTINGS: dict[str, Any] = {
    "temperature": 0,
    "seed": 0,
    "max_tokens": MAX_TOKENS,
    "enable_thinking": False,
    "stream": True,
    "include_usage": True,
}


@dataclass(frozen=True)
class Case:
    key: str
    target_prompt_tokens: int
    kind: str  # fresh | repeat_prefix | extended
    description: str


BENCHMARK_CASES = (
    Case("p128", 128, "fresh", "~128 prompt tokens, new prefix each repeat"),
    Case("p2k", 2048, "fresh", "~2k prompt tokens, new prefix each repeat"),
    Case("p8k", 8192, "fresh", "~8k prompt tokens, new prefix each repeat"),
    Case("repeat_2k", 2048, "repeat_prefix", "~2k tokens, the same prompt every repeat"),
    Case("extended_2k", 2048, "extended", "~2k-token conversation plus a new turn each repeat"),
)

_WORDS = (
    "river mountain lantern harbor meadow copper violet thunder garden pillar"
    " orchard compass silver canyon ember willow granite falcon marble prairie"
).split()
_QUESTIONS = (
    "Summarize the passage above in two sentences.",
    "List three words that appear in the passage above.",
    "Describe the tone of the passage above briefly.",
    "Name the first and last word of the passage above.",
    "Write one sentence that continues the passage above.",
)


def filler(target_tokens: int, seed: str = "") -> str:
    """Deterministic English-like text of roughly ``target_tokens`` tokens.

    The real count is whatever the server reports; this only sets the scale."""
    words = max(8, int(target_tokens * 0.75))
    out = []
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    for i in range(words):
        out.append(_WORDS[(i * 7 + h) % len(_WORDS)])
        if i % 12 == 11:
            out[-1] += "."
    return " ".join(out)


def build_messages(case: Case, rep: int) -> list[dict[str, str]]:
    """Messages for one repetition (``rep`` 0 is the warm-up)."""
    question = _QUESTIONS[rep % len(_QUESTIONS)]
    if case.kind == "repeat_prefix":
        text = f"{filler(case.target_prompt_tokens)}\n\n{_QUESTIONS[0]}"
        return [{"role": "user", "content": text}]
    if case.kind == "extended":
        history = filler(case.target_prompt_tokens - 64, seed="history")
        return [
            {"role": "user", "content": f"{history}\n\n{_QUESTIONS[0]}"},
            {"role": "assistant", "content": "The passage lists landscape words in a loop."},
            {"role": "user", "content": f"Turn {rep}: {question}"},
        ]
    # fresh: a per-repetition nonce first, so no prefix is shared with earlier requests
    nonce = f"Session {case.key}-{rep}-{time.time_ns()}."
    body = filler(case.target_prompt_tokens, seed=f"{case.key}-{rep}")
    return [{"role": "user", "content": f"{nonce}\n{body}\n\n{question}"}]


def benchmark_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "model": ACTIVE_MODEL_ID,  # always the full path: an unknown id makes the server load it
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": EFFECTIVE_SETTINGS["temperature"],
        "seed": EFFECTIVE_SETTINGS["seed"],
        "max_tokens": EFFECTIVE_SETTINGS["max_tokens"],
        "enable_thinking": EFFECTIVE_SETTINGS["enable_thinking"],
    }


def settings_hash(
    effective: dict[str, Any], runtime_fingerprint: str | None, profile: str | None = None
) -> str:
    """Comparability key. A non-default launch profile (M3c) is part of it, so a
    baseline measured with other server flags is never compared; the default profile
    keeps the key of results recorded before profiles existed."""
    doc: dict[str, Any] = {
        "effective": effective,
        "runtime": runtime_fingerprint,
        "case_version": CASE_VERSION,
    }
    if profile not in (None, "default"):
        doc["profile"] = profile
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


# --- metric definitions -------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    unit: str
    source: str
    null_means: str
    better: str | None = None  # higher | lower; None: not compared


METRICS: dict[str, MetricSpec] = {
    "ttft_s": MetricSpec(
        "s",
        "client: request sent to first non-empty content or reasoning delta",
        "no token arrived",
        "lower",
    ),
    "total_s": MetricSpec("s", "client: request sent to end of stream", "request failed"),
    "prompt_tokens": MetricSpec("tokens", "backend usage.prompt_tokens", "no usage reported"),
    "completion_tokens": MetricSpec(
        "tokens", "backend usage.completion_tokens", "no usage reported"
    ),
    "cached_tokens": MetricSpec(
        "tokens", "backend usage.prompt_tokens_details.cached_tokens", "not reported"
    ),
    "backend_prefill_tok_s": MetricSpec(
        "tokens/s", "backend timings.prompt_per_second", "no timings reported", "higher"
    ),
    "backend_decode_tok_s": MetricSpec(
        "tokens/s", "backend timings.predicted_per_second", "no timings reported", "higher"
    ),
    "client_decode_tok_s": MetricSpec(
        "tokens/s",
        "client estimate: backend completion_tokens / (end - first token)",
        "no backend token count",
    ),
    "backend_peak_memory_gb": MetricSpec(
        "GB", "backend timings.peak_memory (MLX allocator peak, not total memory)", "not reported"
    ),
    "content_chars": MetricSpec("characters", "client: visible content length", "never null"),
    "peak_rss_bytes": MetricSpec(
        "bytes", "admin: psutil RSS of the server process, sampled", "process not observed"
    ),
}


@dataclass
class Measurement:
    ttft_s: float | None = None
    total_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    backend_prefill_tok_s: float | None = None
    backend_decode_tok_s: float | None = None
    client_decode_tok_s: float | None = None
    backend_peak_memory_gb: float | None = None
    content_chars: int = 0
    peak_rss_bytes: int | None = None
    finish_reason: str | None = None
    token_source: str | None = None  # backend | None
    error: str | None = None
    error_kind: str | None = None  # http | transport | timeout

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


async def measure_stream(
    http: httpx.AsyncClient,
    payload: dict[str, Any],
    *,
    deadline_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> Measurement:
    """Send one streaming request with a total deadline. Cancellation propagates and
    closes the upstream stream (the server then frees its decode slot)."""
    m = Measurement()
    t0 = clock()
    t_first: float | None = None
    usage: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None
    try:
        async with asyncio.timeout(deadline_s):
            async with http.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    m.error, m.error_kind = f"http {resp.status_code}", "http"
                    return m
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    if isinstance(chunk.get("timings"), dict):
                        timings = chunk["timings"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        m.finish_reason = choice.get("finish_reason") or m.finish_reason
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        content = delta.get("content")
                        if (reasoning or content) and t_first is None:
                            t_first = clock()
                        if content:
                            m.content_chars += len(content)
    except TimeoutError:
        m.error, m.error_kind = f"no complete response within {deadline_s:.0f}s", "timeout"
        return m
    except httpx.TimeoutException:
        m.error, m.error_kind = "model server timed out", "timeout"
        return m
    except httpx.HTTPError:
        m.error, m.error_kind = "model server is unreachable", "transport"
        return m
    t_end = clock()
    m.total_s = t_end - t0
    m.ttft_s = t_first - t0 if t_first is not None else None
    if usage is not None:
        m.prompt_tokens = _int(usage.get("prompt_tokens"))
        m.completion_tokens = _int(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            m.cached_tokens = _int(details.get("cached_tokens"))
        if m.completion_tokens is not None:
            m.token_source = "backend"
    if timings is not None:
        m.backend_prefill_tok_s = _num(timings.get("prompt_per_second"))
        m.backend_decode_tok_s = _num(timings.get("predicted_per_second"))
        m.backend_peak_memory_gb = _num(timings.get("peak_memory"))
    if m.completion_tokens and t_first is not None and t_end > t_first:
        m.client_decode_tok_s = m.completion_tokens / (t_end - t_first)
    return m


def cache_state(cache_enabled: bool | None, cached_tokens: int | None) -> str:
    """``verified_warm`` needs reported cached tokens; ``verified_cold`` needs both a
    disabled prefix cache and a reported zero. Anything else is unknown."""
    if cached_tokens is not None and cached_tokens > 0:
        return "verified_warm"
    if cache_enabled is False and cached_tokens == 0:
        return "verified_cold"
    return "unknown"


def contaminated(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool | None:
    """Did another client use the server during one measured request?

    ``before``/``after`` are ``/metrics`` ``summary`` snapshots; exactly one request (ours)
    may start in between, and none may be in flight at the start. None: cannot tell."""
    if before is None or after is None:
        return None
    try:
        start, end = int(before["requests_started"]), int(after["requests_started"])
        in_flight = int(before["in_flight"])
    except (KeyError, TypeError, ValueError):
        return None
    if end < start:
        return None  # counters reset: the server restarted
    return in_flight > 0 or end - start > 1


# --- summaries ----------------------------------------------------------------------

_SUMMARY_METRICS = tuple(k for k in METRICS if k != "content_chars")


def summarize(reps: list[dict[str, Any]]) -> dict[str, Any]:
    """Medians over valid repeats: not the warm-up, no error, not contaminated.

    Unknown contamination (None) keeps a repeat but marks the summary uncontrolled."""
    excluded = {"warmup": 0, "contaminated": 0, "error": 0}
    valid: list[dict[str, Any]] = []
    controlled = True
    for r in reps:
        if r["warmup"]:
            excluded["warmup"] += 1
        elif r["error"]:
            excluded["error"] += 1
        elif r["contaminated"]:
            excluded["contaminated"] += 1
        else:
            if r["contaminated"] is None:
                controlled = False
            valid.append(r["data"])
    medians: dict[str, float | None] = {}
    for key in _SUMMARY_METRICS:
        values = [v[key] for v in valid if v.get(key) is not None]
        medians[key] = statistics.median(values) if values else None
    return {
        "valid": len(valid),
        "complete": len(valid) >= MIN_VALID,
        "controlled": controlled,
        "excluded": excluded,
        "medians": medians,
    }


def regressions(
    new: dict[str, Any], baseline: dict[str, Any], threshold: float = REGRESSION_THRESHOLD
) -> list[dict[str, Any]]:
    """Metrics that got worse by more than ``threshold`` (relative)."""
    out = []
    for key, spec in METRICS.items():
        if spec.better is None:
            continue
        a, b = baseline.get(key), new.get(key)
        if a is None or b is None or a <= 0:
            continue
        change = (b - a) / a
        worse = change < -threshold if spec.better == "higher" else change > threshold
        if worse:
            out.append({"metric": key, "baseline": a, "value": b, "change": round(change, 4)})
    return out


# --- process sampling ---------------------------------------------------------------


class PeakSampler:
    """Samples ``read()`` (RSS bytes, or None) every ``interval_s`` while active."""

    def __init__(self, read: Callable[[], int | None], interval_s: float = 0.5) -> None:
        self.read = read
        self.interval_s = interval_s
        self.peak: int | None = None
        self.samples = 0
        self._task: asyncio.Task[None] | None = None

    def _take(self) -> None:
        value = self.read()
        self.samples += 1
        if value is not None and (self.peak is None or value > self.peak):
            self.peak = value

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self._take)
            await asyncio.sleep(self.interval_s)

    async def __aenter__(self) -> PeakSampler:
        # one sample at each end, so even a very short request is observed
        await asyncio.to_thread(self._take)
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await asyncio.to_thread(self._take)
