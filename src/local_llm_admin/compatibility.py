"""M2 compatibility cases against the loaded model (:8080, full active model ID).

Outcomes: ``pass``; ``fail`` (the capability is offered but its output is wrong);
``unsupported`` (the backend or model rejects the option, or there is no evidence it
exists); ``unknown`` (transport, server or model-load trouble says nothing about the
capability). ``error_kind`` tells those apart. A returned tool call is only validated,
never executed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import zlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx

from .chat import ACTIVE_MODEL_ID

MAX_DETAIL = 300
# the model id every case sends: MLX's full active path, or the id another engine serves
_MODEL_ID: ContextVar[str] = ContextVar("compat_model_id", default=ACTIVE_MODEL_ID)
# Strata reads thinking only from chat_template_kwargs / reasoning_effort, never a top-level
# enable_thinking (MLX's spelling); without it every case thinks at xhigh into max_tokens
_TEMPLATE_KWARGS: ContextVar[bool] = ContextVar("compat_template_kwargs", default=False)
CASES = (
    "plain",
    "stream",
    "tools",
    "json",
    "thinking",
    "reasoning_effort_low",
    "reasoning_effort_high",
    "vision",
)
_MODEL_LOAD_HINTS = (
    "repository not found",
    "not found for url",
    "does not appear to have",
    "no such file",
    "failed to load",
    "model not found",
)
_UNSUPPORTED_HINTS = ("unsupported", "not supported", "template")

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
JSON_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["name", "count"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CompatResult:
    key: str
    outcome: str  # pass | fail | unsupported | unknown
    detail: str = ""
    error_kind: str | None = None  # capability | transport | timeout | http | model_load


def _bounded(text: str) -> str:
    return text[:MAX_DETAIL]


def _backend_detail(text: str) -> str:
    try:
        doc = json.loads(text)
    except ValueError:
        return text.strip()
    if isinstance(doc, dict):
        detail = doc.get("detail") or doc.get("error") or doc
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail)
        return str(detail)
    return text.strip()


def classify_error(status: int, text: str, key: str = "") -> CompatResult:
    detail = _bounded(f"HTTP {status}: {_backend_detail(text)}")
    low = text.lower()
    if status == 404 or any(h in low for h in _MODEL_LOAD_HINTS):
        return CompatResult(key, "unknown", detail, "model_load")
    if status in (400, 422) or any(h in low for h in _UNSUPPORTED_HINTS):
        return CompatResult(key, "unsupported", detail, "capability")
    return CompatResult(key, "unknown", detail, "http")


def red_png(size: int = 8) -> bytes:
    """A tiny solid-red PNG, built in code (no user images)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _base(messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    payload = {
        "model": _MODEL_ID.get(),
        "messages": messages,
        "max_tokens": 256,
        "temperature": 0,
        "seed": 0,
        "enable_thinking": False,
        **extra,
    }
    if _TEMPLATE_KWARGS.get() and "reasoning_effort" not in payload:
        # an "off" next to reasoning_effort would override the effort under test
        payload["chat_template_kwargs"] = {"enable_thinking": payload["enable_thinking"]}
    return payload


def _user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


async def _post(
    http: httpx.AsyncClient, key: str, payload: dict[str, Any], deadline_s: float
) -> dict[str, Any] | CompatResult:
    try:
        async with asyncio.timeout(deadline_s):
            resp = await http.post("/v1/chat/completions", json=payload)
    except TimeoutError:
        return CompatResult(key, "unknown", f"no response within {deadline_s:.0f}s", "timeout")
    except httpx.TimeoutException:
        return CompatResult(key, "unknown", "model server timed out", "timeout")
    except httpx.HTTPError:
        return CompatResult(key, "unknown", "model server is unreachable", "transport")
    if resp.status_code != 200:
        return classify_error(resp.status_code, resp.text, key)
    try:
        body = resp.json()
        message = body["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return CompatResult(key, "fail", "response is not an OpenAI chat completion", "capability")
    return dict(message) if isinstance(message, dict) else {}


def _fail(key: str, detail: str) -> CompatResult:
    return CompatResult(key, "fail", _bounded(detail), "capability")


async def _plain(http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float) -> CompatResult:
    msg = await _post(http, key, _base(_user("Reply with the single word: ready")), d)
    if isinstance(msg, CompatResult):
        return msg
    if not (msg.get("content") or "").strip():
        return _fail(key, "empty answer")
    return CompatResult(key, "pass")


async def _stream(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float
) -> CompatResult:
    payload = _base(_user("Count from one to five in words."), stream=True)
    content = 0
    finished = done = False
    started = False
    try:
        async with asyncio.timeout(d):
            async with http.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    return classify_error(resp.status_code, resp.text, key)
                started = True
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        return _fail(key, "a stream chunk is not valid JSON")
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            content += 1
                        finished = finished or bool(choice.get("finish_reason"))
    except (TimeoutError, httpx.TimeoutException):
        if started:
            return CompatResult(key, "fail", f"stream did not finish within {d:.0f}s", "timeout")
        return CompatResult(key, "unknown", f"no response within {d:.0f}s", "timeout")
    except httpx.HTTPError:
        return CompatResult(key, "unknown", "model server is unreachable", "transport")
    if not content:
        return _fail(key, "stream carried no content deltas")
    if not finished or not done:
        return _fail(key, "stream ended without finish_reason or [DONE]")
    return CompatResult(key, "pass")


async def _tools(http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float) -> CompatResult:
    if not info.get("tool_parser"):
        return CompatResult(key, "unsupported", "the server has no tool parser for this model")
    payload = _base(
        _user("What is the weather in Paris right now? Use the get_weather tool."),
        tools=[WEATHER_TOOL],
        tool_choice="auto",
    )
    msg = await _post(http, key, payload, d)
    if isinstance(msg, CompatResult):
        return msg
    calls = msg.get("tool_calls") or []
    if not calls:
        return _fail(key, "the model answered without calling the tool")
    fn = (calls[0] or {}).get("function") or {}
    if fn.get("name") != "get_weather":
        return _fail(key, f"unexpected function {fn.get('name')!r}")
    try:
        args = json.loads(fn.get("arguments") or "")
    except (ValueError, TypeError):
        return _fail(key, "tool call arguments are not valid JSON")
    if not isinstance(args, dict) or not isinstance(args.get("city"), str):
        return _fail(key, "tool call arguments do not match the schema (city: string)")
    return CompatResult(key, "pass")


async def _json(http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float) -> CompatResult:
    payload = _base(
        _user("Return a JSON object naming one fruit and how many letters it has."),
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "fruit", "schema": JSON_SCHEMA},
        },
    )
    msg = await _post(http, key, payload, d)
    if isinstance(msg, CompatResult):
        return msg
    try:
        doc = json.loads(msg.get("content") or "")
    except (ValueError, TypeError):
        return _fail(key, "content is not valid JSON")
    ok = (
        isinstance(doc, dict)
        and set(doc) == {"name", "count"}
        and isinstance(doc["name"], str)
        and isinstance(doc["count"], int)
        and not isinstance(doc["count"], bool)
    )
    return CompatResult(key, "pass") if ok else _fail(key, "JSON does not match the schema")


async def _option(http: httpx.AsyncClient, key: str, d: float, **extra: Any) -> CompatResult:
    msg = await _post(http, key, _base(_user("What is 2 + 2?"), **extra), d)
    if isinstance(msg, CompatResult):
        return msg
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if not ((msg.get("content") or "").strip() or reasoning):
        return _fail(key, "empty answer")
    return CompatResult(key, "pass")


async def _thinking(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float
) -> CompatResult:
    return await _option(http, key, d, enable_thinking=True)


async def _effort_low(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float
) -> CompatResult:
    return await _option(http, key, d, reasoning_effort="low")


async def _effort_high(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float
) -> CompatResult:
    return await _option(http, key, d, reasoning_effort="high")


async def _vision(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], d: float
) -> CompatResult:
    if not info.get("vision"):
        return CompatResult(key, "unsupported", "not probed: the weights have no vision_config")
    url = "data:image/png;base64," + base64.b64encode(red_png()).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What colour is this image? Answer with one word."},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
    ]
    msg = await _post(http, key, _base(messages), d)
    if isinstance(msg, CompatResult):
        return msg
    if "red" not in (msg.get("content") or "").lower():
        return _fail(key, "the answer does not name the image's colour (red)")
    return CompatResult(key, "pass")


_RUNNERS = {
    "plain": _plain,
    "stream": _stream,
    "tools": _tools,
    "json": _json,
    "thinking": _thinking,
    "reasoning_effort_low": _effort_low,
    "reasoning_effort_high": _effort_high,
    "vision": _vision,
}


async def run_case(
    http: httpx.AsyncClient, key: str, info: dict[str, Any], *, deadline_s: float
) -> CompatResult:
    """``info``: ``tool_parser`` (from /health), ``vision`` (weights have a
    ``vision_config``), optionally ``model_id`` (default: MLX's active path) and
    ``engine`` (``strata`` sends thinking in ``chat_template_kwargs``).
    Cancellation propagates."""
    token = _MODEL_ID.set(info.get("model_id") or ACTIVE_MODEL_ID)
    kw_token = _TEMPLATE_KWARGS.set(info.get("engine") == "strata")
    try:
        return await _RUNNERS[key](http, key, info, deadline_s)
    finally:
        _TEMPLATE_KWARGS.reset(kw_token)
        _MODEL_ID.reset(token)
