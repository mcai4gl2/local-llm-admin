"""Streaming chat proxy to the MLX server's OpenAI API (bypasses LiteLLM)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .config import Settings

ACTIVE_MODEL_ID = "/Volumes/Models/models/active"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=200_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    system: str | None = Field(default=None, max_length=50_000)
    # None: use the model's saved admin-chat default (M3c), else the built-in one
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)
    thinking: bool | None = None


BUILTIN_CHAT: dict[str, Any] = {"temperature": 0.7, "max_tokens": 512}
_PROFILE_CHAT = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "max_tokens",
    "enable_thinking",
    "reasoning_effort",
)


def effective_chat(req: ChatRequest, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Built-in, then the model's saved chat defaults, then explicit request fields."""
    out = dict(BUILTIN_CHAT)
    out.update({k: v for k, v in (defaults or {}).items() if k in _PROFILE_CHAT and v is not None})
    explicit = {
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "enable_thinking": req.thinking,
    }
    out.update({k: v for k, v in explicit.items() if v is not None})
    return out


def build_payload(req: ChatRequest, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend(m.model_dump() for m in req.messages)
    eff = effective_chat(req, defaults)
    payload: dict[str, Any] = {
        "model": ACTIVE_MODEL_ID,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    for key in ("temperature", "max_tokens", "top_p", "top_k", "min_p", "reasoning_effort"):
        if key in eff:
            payload[key] = eff[key]
    if "enable_thinking" in eff:
        payload["chat_template_kwargs"] = {"enable_thinking": eff["enable_thinking"]}
    return payload


def _event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def stream_chat(
    settings: Settings,
    req: ChatRequest,
    transport: httpx.AsyncBaseTransport | None = None,
    defaults: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE events: reasoning/delta chunks, then one ``done`` (or one ``error``).

    Closing this generator (browser Stop) closes the upstream response, which makes the
    server log ``Generation cancelled`` and frees the single decode slot.
    """
    t0 = time.monotonic()
    t_first: float | None = None
    usage: dict[str, Any] | None = None
    finish: str | None = None
    n_deltas = 0
    timeout = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
    try:
        async with (
            httpx.AsyncClient(
                base_url=settings.mlx_api_base, timeout=timeout, transport=transport
            ) as http,
            http.stream("POST", "/v1/chat/completions", json=build_payload(req, defaults)) as resp,
        ):
            if resp.status_code != 200:
                yield _event(
                    {"type": "error", "message": f"model server returned HTTP {resp.status_code}"}
                )
                return
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
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason") or finish
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        t_first = t_first or time.monotonic()
                        yield _event({"type": "reasoning", "content": reasoning})
                    if delta.get("content"):
                        t_first = t_first or time.monotonic()
                        n_deltas += 1
                        yield _event({"type": "delta", "content": delta["content"]})
    except httpx.HTTPError:
        yield _event({"type": "error", "message": "model server is unreachable"})
        return
    t_end = time.monotonic()
    completion = (usage or {}).get("completion_tokens", n_deltas)
    gen_time = t_end - t_first if t_first else None
    yield _event(
        {
            "type": "done",
            "usage": {
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": completion,
            },
            "finish_reason": finish,
            "ttft_ms": round((t_first - t0) * 1000) if t_first else None,
            "tok_s": round(completion / gen_time, 1) if gen_time and completion else None,
            "settings": effective_chat(req, defaults),
        }
    )
