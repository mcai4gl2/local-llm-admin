"""M2 §4: compatibility cases. A generated tool call is never executed."""

import json

import httpx
import pytest

from local_llm_admin import compatibility as compat
from local_llm_admin.chat import ACTIVE_MODEL_ID

from .fake_mlx_server import FakeMlxServer


@pytest.fixture
def server():
    return FakeMlxServer()


async def _run(server, key, **info):
    info = {"tool_parser": "qwen3_coder", "vision": True, **info}
    async with httpx.AsyncClient(base_url="http://mlx.test", transport=server.transport) as http:
        return await compat.run_case(http, key, info, deadline_s=5)


def _message(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "stop"}]}


def test_case_list_is_stable():
    assert [c for c in compat.CASES] == [
        "plain",
        "stream",
        "tools",
        "json",
        "thinking",
        "reasoning_effort_low",
        "reasoning_effort_high",
        "vision",
    ]


async def test_every_request_uses_the_full_model_id(server):
    for key in compat.CASES:
        await _run(server, key)
    assert server.requests and {r["model"] for r in server.requests} == {ACTIVE_MODEL_ID}


async def test_plain_chat_pass_and_empty_fail(server):
    assert (await _run(server, "plain")).outcome == "pass"
    server.reply = lambda p: _message("")
    r = await _run(server, "plain")
    assert r.outcome == "fail" and r.error_kind == "capability"


async def test_streaming_pass_and_malformed_fail(server):
    assert (await _run(server, "stream")).outcome == "pass"
    server.stream_parts = []
    r = await _run(server, "stream")
    assert r.outcome == "fail" and "no content" in r.detail


async def test_stream_that_hangs_is_a_failure_not_transport(server):
    server.stream_parts = ["x"] * 100
    server.stream_delay = 0.2
    async with httpx.AsyncClient(base_url="http://mlx.test", transport=server.transport) as http:
        r = await compat.run_case(http, "stream", {}, deadline_s=0.5)
    assert r.outcome == "fail" and r.error_kind == "timeout"


async def test_tool_call_is_validated_never_executed(server):
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": json.dumps({"city": "Paris"})},
    }
    server.reply = lambda p: _message(None, [call])
    r = await _run(server, "tools")
    assert r.outcome == "pass"
    sent = server.requests[-1]
    assert sent["tools"][0]["function"]["name"] == "get_weather"
    assert not hasattr(compat, "execute_tool")


@pytest.mark.parametrize(
    ("tool_calls", "detail"),
    [
        (None, "without calling"),
        ([{"function": {"name": "other", "arguments": "{}"}}], "unexpected function"),
        ([{"function": {"name": "get_weather", "arguments": "{city"}}], "not valid JSON"),
        ([{"function": {"name": "get_weather", "arguments": '{"town": 1}'}}], "schema"),
    ],
)
async def test_malformed_tool_calls_fail(server, tool_calls, detail):
    server.reply = lambda p: _message("I think it is sunny.", tool_calls)
    r = await _run(server, "tools")
    assert r.outcome == "fail" and detail in r.detail


async def test_no_tool_parser_is_unsupported_without_a_request(server):
    r = await _run(server, "tools", tool_parser=None)
    assert r.outcome == "unsupported" and server.requests == []


async def test_json_mode_validates_schema(server):
    server.reply = lambda p: _message(json.dumps({"name": "x", "count": 3}))
    assert (await _run(server, "json")).outcome == "pass"
    assert server.requests[-1]["response_format"]["type"] == "json_schema"
    server.reply = lambda p: _message("Sure! Here is JSON: {")
    r = await _run(server, "json")
    assert r.outcome == "fail" and "not valid JSON" in r.detail
    server.reply = lambda p: _message(json.dumps({"name": "x"}))
    r = await _run(server, "json")
    assert r.outcome == "fail" and "schema" in r.detail


async def test_json_mode_rejected_by_backend_is_unsupported(server):
    server.reply = lambda p: httpx.Response(
        400, json={"detail": "Unsupported response_format type: 'json_schema'"}
    )
    r = await _run(server, "json")
    assert r.outcome == "unsupported" and "response_format" in r.detail


async def test_reasoning_effort_rejected_by_the_template_is_unsupported_with_detail(server):
    # regression fixture (D-M5): the chat template rejects reasoning_effort "high"
    def reply(p):
        if p.get("reasoning_effort") == "high":
            return httpx.Response(
                500,
                json={"detail": "TemplateError: reasoning_effort must be one of low, medium"},
            )
        return _message("ok")

    server.reply = reply
    low = await _run(server, "reasoning_effort_low")
    high = await _run(server, "reasoning_effort_high")
    assert low.outcome == "pass"
    assert high.outcome == "unsupported" and "reasoning_effort must be one of" in high.detail
    assert server.requests[-1]["reasoning_effort"] == "high"


async def test_thinking_toggle(server):
    assert (await _run(server, "thinking")).outcome == "pass"
    assert server.requests[-1]["enable_thinking"] is True


async def test_mistaken_model_id_is_a_model_load_failure_not_incompatibility(server):
    # regression fixture: a client sending model "active" makes the server try to load it
    server.reply = lambda p: httpx.Response(
        404, json={"detail": "Repository Not Found for url: https://huggingface.co/active"}
    )
    r = await _run(server, "plain")
    assert r.outcome == "unknown" and r.error_kind == "model_load"


async def test_transport_and_server_errors_are_unknown(server):
    server.unreachable = True
    r = await _run(server, "plain")
    assert r.outcome == "unknown" and r.error_kind == "transport"
    server.unreachable = False
    server.reply = lambda p: httpx.Response(503, text="busy")
    r = await _run(server, "plain")
    assert r.outcome == "unknown" and r.error_kind == "http"


async def test_vision_probe_only_with_evidence(server):
    r = await _run(server, "vision", vision=False)
    assert r.outcome == "unsupported" and "not probed" in r.detail and server.requests == []
    server.reply = lambda p: _message("Red.")
    r = await _run(server, "vision")
    assert r.outcome == "pass"
    part = server.requests[-1]["messages"][0]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    server.reply = lambda p: _message("Blue.")
    assert (await _run(server, "vision")).outcome == "fail"


def test_synthetic_image_is_a_valid_png():
    data = compat.red_png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) < 200


def test_error_detail_is_bounded():
    r = compat.classify_error(400, "x" * 5000)
    assert len(r.detail) <= 300


async def test_unknown_case_key_raises(server):
    with pytest.raises(KeyError):
        await _run(server, "nope")


async def test_stream_http_error_is_classified(server):
    server.chat_status = 503
    r = await _run(server, "stream")
    assert r.outcome == "unknown" and r.error_kind == "http"


async def test_stream_with_a_malformed_chunk_fails(server):
    server.stream_parts = ["ok"]
    original = server._stream
    server._stream = lambda: [b"data: {broken\n\n", *original()]
    r = await _run(server, "stream")
    assert r.outcome == "fail" and "not valid JSON" in r.detail


async def test_stream_without_finish_reason_fails(server):
    server._stream = lambda: [
        b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    r = await _run(server, "stream")
    assert r.outcome == "fail" and "finish_reason" in r.detail


async def test_stream_transport_and_silent_timeout_are_unknown(server):
    server.unreachable = True
    assert (await _run(server, "stream")).error_kind == "transport"

    def hang(request):
        raise httpx.ReadTimeout("x")

    async with httpx.AsyncClient(
        base_url="http://mlx.test", transport=httpx.MockTransport(hang)
    ) as http:
        r = await compat.run_case(http, "stream", {}, deadline_s=5)
    assert r.outcome == "unknown" and r.error_kind == "timeout"


async def test_errors_before_tool_or_vision_answers_are_unknown(server):
    server.unreachable = True
    assert (await _run(server, "tools")).error_kind == "transport"
    assert (await _run(server, "vision")).error_kind == "transport"


async def test_non_streaming_timeouts_are_unknown():
    async def slow(request):
        import asyncio

        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="http://mlx.test", transport=httpx.MockTransport(slow)
    ) as http:
        r = await compat.run_case(http, "plain", {}, deadline_s=0.05)
    assert r.outcome == "unknown" and r.error_kind == "timeout"

    def read_timeout(request):
        raise httpx.ReadTimeout("x")

    async with httpx.AsyncClient(
        base_url="http://mlx.test", transport=httpx.MockTransport(read_timeout)
    ) as http:
        r = await compat.run_case(http, "plain", {}, deadline_s=5)
    assert r.error_kind == "timeout"


async def test_a_body_that_is_not_a_chat_completion_fails(server):
    server.reply = lambda p: {"object": "error"}
    r = await _run(server, "plain")
    assert r.outcome == "fail" and "not an OpenAI chat completion" in r.detail


async def test_empty_option_answer_fails_but_reasoning_only_passes(server):
    server.reply = lambda p: _message("")
    assert (await _run(server, "thinking")).outcome == "fail"
    server.reply = lambda p: {
        "choices": [{"message": {"content": "", "reasoning_content": "2+2=4"}}]
    }
    assert (await _run(server, "thinking")).outcome == "pass"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain words", "HTTP 400: plain words"),
        ('["a list"]', 'HTTP 400: ["a list"]'),
        ('{"error": {"message": "bad option"}}', "HTTP 400: bad option"),
        ('{"error": {"code": 1}}', 'HTTP 400: {"code": 1}'),
    ],
)
def test_backend_detail_extraction(text, expected):
    assert compat.classify_error(400, text).detail == expected


async def test_reasoning_field_alone_counts_as_an_answer(server):
    server.reply = lambda p: {"choices": [{"message": {"content": "", "reasoning": "2+2=4"}}]}
    assert (await _run(server, "thinking")).outcome == "pass"
