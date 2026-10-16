import json

import httpx
import pytest
from pydantic import ValidationError

from local_llm_admin import chat
from local_llm_admin.config import Settings

from .fake_openai import FakeOpenAI, sse_chunks

S = Settings(mlx_api_base="http://mlx.test")


def _events(raw):
    return [json.loads(e.removeprefix("data: ")) for e in raw]


async def _collect(gen):
    return [e async for e in gen]


def test_strata_chat_default_leaves_room_to_think():
    """Strata thinks at xhigh by default: 512 tokens can end before any answer."""
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert chat.effective_chat(req, chat.STRATA_CHAT)["max_tokens"] == 8192
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    assert chat.effective_chat(req, chat.STRATA_CHAT)["max_tokens"] == 100


def test_request_validation_limits():
    ok = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert ok.max_tokens is None and ok.thinking is None  # resolved by effective_chat
    assert chat.effective_chat(ok) == {"temperature": 0.7, "max_tokens": 512}
    with pytest.raises(ValidationError):
        chat.ChatRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=32769)
    big = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=8192)
    assert chat.effective_chat(big)["max_tokens"] == 8192
    with pytest.raises(ValidationError):
        chat.ChatRequest(messages=[{"role": "system", "content": "sneaky"}])
    with pytest.raises(ValidationError):
        chat.ChatRequest(messages=[])
    with pytest.raises(ValidationError):
        chat.ChatRequest(messages=[{"role": "user", "content": "x"}], temperature=3)


def test_build_payload():
    req = chat.ChatRequest(
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "user", "content": "more"},
        ],
        system="be brief",
        temperature=0.2,
        max_tokens=100,
        thinking=False,
    )
    p = chat.build_payload(req)
    assert p["model"] == "/Volumes/Models/models/active"
    assert p["stream"] is True and p["stream_options"] == {"include_usage": True}
    assert p["messages"][0] == {"role": "system", "content": "be brief"}
    assert len(p["messages"]) == 4
    assert (p["temperature"], p["max_tokens"]) == (0.2, 100)
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    p2 = chat.build_payload(chat.ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert "chat_template_kwargs" not in p2 and p2["messages"][0]["role"] == "user"


async def test_stream_relays_deltas_reasoning_and_stats():
    fake = FakeOpenAI(
        sse_chunks(
            ["Hel", "lo"], usage={"prompt_tokens": 12, "completion_tokens": 2}, reasoning=["think"]
        )
    )
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    ev = _events(await _collect(chat.stream_chat(S, req, transport=fake.transport)))
    assert ev[0] == {"type": "reasoning", "content": "think"}
    assert [e["content"] for e in ev if e["type"] == "delta"] == ["Hel", "lo"]
    done = ev[-1]
    assert done["type"] == "done"
    assert done["usage"] == {"prompt_tokens": 12, "completion_tokens": 2}
    assert done["finish_reason"] == "stop"
    assert done["ttft_ms"] is not None and done["ttft_ms"] >= 0
    assert fake.requests[0]["stream"] is True


async def test_stream_without_usage_counts_deltas():
    fake = FakeOpenAI(sse_chunks(["a", "b", "c"]))
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    done = _events(await _collect(chat.stream_chat(S, req, transport=fake.transport)))[-1]
    assert done["usage"] == {"prompt_tokens": None, "completion_tokens": 3}


async def test_stream_upstream_error_status():
    fake = FakeOpenAI(status=503)
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    ev = _events(await _collect(chat.stream_chat(S, req, transport=fake.transport)))
    assert ev == [{"type": "error", "message": "model server returned HTTP 503"}]


async def test_stream_connect_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    ev = _events(await _collect(chat.stream_chat(S, req, transport=httpx.MockTransport(boom))))
    assert ev == [{"type": "error", "message": "model server is unreachable"}]


async def test_stream_skips_garbage_lines():
    chunks = [b": keepalive\n\n", b"data: {not json}\n\n", b"event: x\n"] + sse_chunks(["ok"])
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    ev = _events(await _collect(chat.stream_chat(S, req, transport=FakeOpenAI(chunks).transport)))
    assert [e["content"] for e in ev if e["type"] == "delta"] == ["ok"]


async def test_stop_closes_upstream_stream():
    fake = FakeOpenAI(sse_chunks([str(i) for i in range(50)]), delay=0.01)
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    gen = chat.stream_chat(S, req, transport=fake.transport)
    first = await gen.__anext__()
    assert json.loads(first.removeprefix("data: "))["type"] == "delta"
    await gen.aclose()  # what Starlette does when the browser aborts
    assert fake.stream.closed is True
    assert fake.stream.sent < 50


def test_profile_chat_defaults_sit_between_built_ins_and_explicit_fields():
    """M3c precedence: built-in, then the model's saved defaults, then the request."""
    defaults = {
        "temperature": 0.1,
        "top_p": 0.9,
        "reasoning_effort": "low",
        "enable_thinking": False,
        "unknown": 1,
    }
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=64)
    p = chat.build_payload(req, defaults)
    assert p["temperature"] == 0.1 and p["top_p"] == 0.9 and p["max_tokens"] == 64
    assert p["reasoning_effort"] == "low"
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    assert "unknown" not in p
    req = chat.ChatRequest(
        messages=[{"role": "user", "content": "hi"}], temperature=1.5, thinking=True
    )
    p = chat.build_payload(req, defaults)
    assert p["temperature"] == 1.5 and p["chat_template_kwargs"] == {"enable_thinking": True}
    assert chat.build_payload(req)["max_tokens"] == 512


async def test_stream_to_another_engine_uses_its_base_model_and_key():
    seen = []

    def handler(request):
        seen.append(request)
        body = b"".join(sse_chunks(["ok"]))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    target = chat.ChatTarget(base_url="http://strata.test", model="Qwen3.8-Flash-Next", api_key="k")
    req = chat.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    ev = _events(
        await _collect(chat.stream_chat(S, req, transport=httpx.MockTransport(handler), target=target))
    )
    assert ev[-1]["type"] == "done"
    assert str(seen[0].url) == "http://strata.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert json.loads(seen[0].content)["model"] == "Qwen3.8-Flash-Next"
