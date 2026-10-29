"""One authenticated OpenAI URL follows the owned engine without changing client IDs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request

from local_llm_admin.backend_types import InferenceDescriptor
from local_llm_admin.backends import BackendUnavailable
from local_llm_admin.chat import ACTIVE_MODEL_ID, ChatTarget
from local_llm_admin.config import Settings
from local_llm_admin.inference_gateway import MAX_BODY_BYTES, create_inference_app

KEY = "public-test-inference-key-1234567890"
AUTH = {"Authorization": "Bearer " + KEY}


def setup_gateway(handler: Any, **settings: Any) -> tuple[Any, Any]:
    registry = SimpleNamespace(
        resolve_inference=AsyncMock(
            return_value=InferenceDescriptor(
                ChatTarget("http://splash.test", "example/stock", "private-upstream-key")
            )
        )
    )
    app = create_inference_app(
        Settings(inference_api_key=KEY, **settings),
        registry,
        transport=httpx.MockTransport(handler),
    )
    return app, registry


@pytest.mark.parametrize(
    "backend,model",
    [("mlx", ACTIVE_MODEL_ID), ("strata", "qwen-flash"), ("splash", "example/stock")],
)
@pytest.mark.parametrize("route", ["/chat/completions", "/completions"])
async def test_one_url_and_model_follow_the_selected_engine(
    backend: str, model: str, route: str
) -> None:
    calls = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert json.loads(request.content)["model"] == model
        assert request.headers["authorization"] == "Bearer upstream-key"
        assert "cookie" not in request.headers and "x-csrf-token" not in request.headers
        return httpx.Response(
            200, json={"model": model, "choices": [], "usage": {"completion_tokens": 1}}
        )

    app, registry = setup_gateway(serve)
    registry.resolve_inference.return_value = InferenceDescriptor(
        ChatTarget(f"http://{backend}.test", model, "upstream-key")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        response = await client.post(
            route,
            headers={**AUTH, "cookie": "admin=private", "x-csrf-token": "private"},
            json={
                "model": "local-model",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [{"type": "function"}],
                "stream": False,
            },
        )
    assert response.status_code == 200
    assert response.json()["model"] == "local-model"
    assert response.json()["usage"] == {"completion_tokens": 1}
    assert calls[0].url.host == f"{backend}.test"
    assert json.loads(calls[0].content)["tools"] == [{"type": "function"}]


async def test_engine_changes_do_not_require_a_new_litellm_configuration() -> None:
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, json.loads(request.content)["model"]))
        return httpx.Response(200, json={"model": seen[-1][1]})

    app, registry = setup_gateway(serve)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        for host, model in [
            ("mlx", ACTIVE_MODEL_ID),
            ("strata", "flash"),
            ("splash", "example/stock"),
        ]:
            registry.resolve_inference.return_value = InferenceDescriptor(
                ChatTarget(f"http://{host}.test", model)
            )
            r = await client.post(
                "/chat/completions", headers=AUTH, json={"model": ACTIVE_MODEL_ID, "messages": []}
            )
            assert r.status_code == 200 and r.json()["model"] == ACTIVE_MODEL_ID
    assert seen == [
        ("mlx.test", ACTIVE_MODEL_ID),
        ("strata.test", "flash"),
        ("splash.test", "example/stock"),
    ]


@pytest.mark.parametrize(
    "authorization", [None, "Bearer wrong", "Basic abc", "Bearer " + KEY + "x"]
)
async def test_authentication_precedes_any_engine_probe(authorization: str | None) -> None:
    app, registry = setup_gateway(lambda r: pytest.fail("upstream must not be reached"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        for route in ("/models", "/chat/completions"):
            headers = {"Authorization": authorization} if authorization else {}
            r = await client.request(
                "GET" if route == "/models" else "POST",
                route,
                headers=headers,
                json={"model": "local-model"},
            )
            assert r.status_code == 401 and "error" in r.json()
    registry.resolve_inference.assert_not_awaited()


async def test_disabled_gateway_is_closed_even_with_a_session_cookie() -> None:
    app = create_inference_app(Settings(), SimpleNamespace())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        assert (
            await client.get("/models", headers={"Cookie": "admin=anything"})
        ).status_code == 503


@pytest.mark.parametrize(
    "reason",
    ["engine is unknown", "engine is conflict", "model server is not serving", "engine disabled"],
)
async def test_unavailable_engine_does_not_fall_back(reason: str) -> None:
    app, registry = setup_gateway(lambda r: pytest.fail("no fallback is allowed"))
    registry.resolve_inference.side_effect = BackendUnavailable(reason)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        assert r.status_code == 503
        assert reason not in r.text


async def test_wrong_model_never_reaches_mlx_or_loads_foreign_weights() -> None:
    app, registry = setup_gateway(lambda r: pytest.fail("foreign request reached MLX"))
    registry.resolve_inference.return_value = InferenceDescriptor(
        ChatTarget("http://mlx.test", ACTIVE_MODEL_ID)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "example/splash-only"}
        )
        assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


async def test_models_lists_the_stable_id_and_only_the_active_native_model() -> None:
    app, registry = setup_gateway(lambda r: pytest.fail("models must use verified descriptor"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.get("/models", headers=AUTH)
        assert {row["id"] for row in r.json()["data"]} == {
            "local-model",
            ACTIVE_MODEL_ID,
            "example/stock",
        }


async def test_stream_keeps_usage_reasoning_tools_and_finish_with_public_model() -> None:
    chunks = [
        b": heartbeat\n\n",
        b"data: "
        + json.dumps(
            {
                "model": "example/stock",
                "choices": [
                    {"delta": {"reasoning_content": "think", "tool_calls": [{"id": "call-1"}]}}
                ],
            }
        ).encode()
        + b"\n\n",
        b"data: "
        + json.dumps(
            {
                "model": "example/stock",
                "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 2},
            }
        ).encode()
        + b"\n\n",
        b"data: [DONE]\n\n",
    ]

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for chunk in chunks:
                for byte in chunk:
                    yield bytes([byte])

        async def aclose(self) -> None:
            self.closed = True

    stream = Stream()
    app, _ = setup_gateway(
        lambda r: httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "local-model", "stream": True}
        )
    data = [
        json.loads(line[6:])
        for line in r.text.splitlines()
        if line.startswith("data: ") and "[DONE]" not in line
    ]
    assert data[0]["choices"][0]["delta"]["reasoning_content"] == "think"
    assert data[0]["choices"][0]["delta"]["tool_calls"] == [{"id": "call-1"}]
    assert data[-1]["usage"] == {"completion_tokens": 2}
    assert data[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(item["model"] == "local-model" for item in data)
    assert ": heartbeat" in r.text and "data: [DONE]" in r.text and stream.closed


@pytest.mark.parametrize(
    "body", [b"bad json", b"[]", b"{}", b'{"model":42}', b'{"model":"local-model","stream":1}']
)
async def test_malformed_body_is_refused(body: bytes) -> None:
    app, _ = setup_gateway(lambda r: pytest.fail("malformed body forwarded"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        assert (
            await client.post("/chat/completions", headers=AUTH, content=body)
        ).status_code == 400


async def test_oversized_request_is_refused_before_model_probe() -> None:
    app, registry = setup_gateway(lambda r: pytest.fail("oversized body forwarded"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        assert (
            await client.post(
                "/chat/completions", headers=AUTH, content=b"x" * (MAX_BODY_BYTES + 1)
            )
        ).status_code == 413
    registry.resolve_inference.assert_not_awaited()


@pytest.mark.parametrize("status", [400, 401, 404, 429, 503])
async def test_upstream_errors_are_sanitized_and_keep_status(status: int) -> None:
    app, _ = setup_gateway(
        lambda r: httpx.Response(status, text="private-upstream-key /private/weights")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        assert r.status_code == status
        assert "private" not in r.text


async def test_missing_vision_encoder_has_an_actionable_public_error() -> None:
    native = (
        "this server was started without the vision encoder "
        "(run setup again and choose 'vision'), so it cannot read images"
    )
    app, _ = setup_gateway(
        lambda r: httpx.Response(
            400,
            json={
                "error": {"type": "invalid_request_error", "message": native},
                "private": "private-upstream-key /private/weights",
            },
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
    assert r.status_code == 400
    error = r.json()["error"]
    assert "vision encoder" in error["message"]
    assert "text-only" in error["message"]
    assert error["code"] == "upstream_error"
    assert "private" not in r.text and "setup" not in r.text


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"error": "private"},
        {"error": {"message": None}},
        {"error": {"message": "private-upstream-key /private/weights"}},
        {"error": {"message": "this server was started without the vision encoder private"}},
    ],
)
async def test_unrecognized_error_documents_stay_generic(document: Any) -> None:
    app, _ = setup_gateway(lambda r: httpx.Response(400, json=document))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "Selected engine refused the request"
    assert "private" not in r.text


@pytest.mark.parametrize("broken", [False, True])
async def test_error_body_read_is_bounded_and_closed(broken: bool) -> None:
    class ErrorBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            if broken:
                raise httpx.ReadError("private transport failure")
            yield b"x" * 8193
            pytest.fail("read beyond the error limit")

        async def aclose(self) -> None:
            self.closed = True

    stream = ErrorBody()
    app, _ = setup_gateway(lambda r: httpx.Response(400, stream=stream))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
    assert r.status_code == 400 and stream.closed
    assert r.json()["error"]["message"] == "Selected engine refused the request"


async def test_connect_failure_is_not_retried() -> None:
    calls = []

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("private endpoint unavailable")

    app, _ = setup_gateway(fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        assert r.status_code == 502 and "private" not in r.text
    assert len(calls) == 1


async def test_switching_engine_refuses_new_admission() -> None:
    app = create_inference_app(
        Settings(inference_api_key=KEY), SimpleNamespace(), busy=lambda: True
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        assert (await client.get("/models", headers=AUTH)).status_code == 503
        assert (
            await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        ).status_code == 503


async def test_dedicated_listener_does_not_grant_control_api_access(fake_mlx: Any) -> None:
    from local_llm_admin.app import create_app

    settings = fake_mlx.settings
    settings.inference_api_key = KEY
    admin = create_app(settings, start_background=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(admin), base_url="http://admin.test"
    ) as client:
        assert (await client.get("/v1/models", headers=AUTH)).status_code == 303
        assert (
            await client.post("/api/engine", headers=AUTH, json={"engine": "off"})
        ).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(admin.state.inference), base_url="http://shared.test"
    ) as client:
        assert (await client.get("/v1/models", headers=AUTH)).status_code == 503
        assert (await client.get("/v1/api/status", headers=AUTH)).status_code == 404
        assert (
            await client.post("/api/engine", headers=AUTH, json={"engine": "off"})
        ).status_code == 404


@pytest.mark.parametrize("content", [b"not-json", b"[]", b"x" * (MAX_BODY_BYTES + 1)])
async def test_invalid_upstream_json_cannot_escape_the_boundary(content: bytes) -> None:
    app, _ = setup_gateway(lambda r: httpx.Response(200, content=content))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        assert r.status_code == 502


@pytest.mark.parametrize(
    "frame",
    [
        b"data: nope\n\n",
        b"data: []\n\n",
        b"data: " + b"x" * (2 * 1024 * 1024 + 1),
        b"data: " + b"x" * (2 * 1024 * 1024 + 1) + b"\n\n",
    ],
)
async def test_broken_or_unbounded_streams_emit_sanitized_errors(frame: bytes) -> None:
    app, _ = setup_gateway(
        lambda r: httpx.Response(200, content=frame, headers={"content-type": "text/event-stream"})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "local-model", "stream": True}
        )
        assert "Selected engine stream failed" in r.text and "[DONE]" in r.text


async def test_stream_transport_failure_is_closed_without_retry() -> None:
    class Broken(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b": tick\n\n"
            raise httpx.ReadError("private failure")

        async def aclose(self) -> None:
            self.closed = True

    stream = Broken()
    app, _ = setup_gateway(
        lambda r: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "local-model", "stream": True}
        )
        assert "stream failed" in r.text and "private" not in r.text and stream.closed


@pytest.mark.parametrize(
    "stream,content,expected",
    [(False, b"{}", {}), (True, b'data: {"choices": []}', {"choices": []})],
)
async def test_model_less_native_objects_and_unterminated_final_event(
    stream: bool, content: bytes, expected: dict
) -> None:
    app, _ = setup_gateway(
        lambda r: httpx.Response(
            200, content=content, headers={"content-type": "text/event-stream"}
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "local-model", "stream": stream}
        )
        assert (json.loads(r.text.removeprefix("data: ")) if stream else r.json()) == expected


async def test_wrong_stream_content_type_is_not_forwarded() -> None:
    app, _ = setup_gateway(lambda r: httpx.Response(200, json={"private": "error"}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.post(
            "/chat/completions", headers=AUTH, json={"model": "local-model", "stream": True}
        )
        assert r.status_code == 502 and "private" not in r.text


@pytest.mark.parametrize("route", ["/models", "/chat/completions"])
async def test_inspection_failure_and_operations_started_during_admission_refuse(
    route: str,
) -> None:
    from local_llm_admin.inference_gateway import create_inference_app

    registry = SimpleNamespace(resolve_inference=AsyncMock(side_effect=OSError("private path")))
    app = create_inference_app(Settings(inference_api_key=KEY), registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        r = await client.request(
            "GET" if route == "/models" else "POST",
            route,
            headers=AUTH,
            json={"model": "local-model"},
        )
        assert r.status_code == 503 and "private" not in r.text
    states = iter([False, True])
    registry.resolve_inference.side_effect = None
    registry.resolve_inference.return_value = InferenceDescriptor(
        ChatTarget("http://mlx.test", ACTIVE_MODEL_ID)
    )
    app = create_inference_app(
        Settings(inference_api_key=KEY),
        registry,
        busy=lambda: next(states),
        transport=httpx.MockTransport(lambda r: pytest.fail("engine operation was bypassed")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://shared.test"
    ) as client:
        assert (
            await client.post("/chat/completions", headers=AUTH, json={"model": "local-model"})
        ).status_code == 503


@pytest.fixture
async def live_server() -> Any:
    import contextlib
    import socket

    import uvicorn

    @contextlib.asynccontextmanager
    async def serve(app: Any) -> AsyncIterator[str]:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            sock.close()

    yield serve


async def test_real_socket_streams_arrive_before_completion_and_disconnect_frees_slots(
    live_server: Any,
) -> None:
    from contextlib import AsyncExitStack

    from fastapi.responses import JSONResponse, StreamingResponse

    from local_llm_admin.inference_gateway import MAX_INFLIGHT

    native = FastAPI()
    closed = asyncio.Queue()

    @native.post("/v1/chat/completions")
    async def complete(request: Request) -> Any:
        body = await request.json()
        assert body["model"] == "example/stock"
        if not body.get("stream"):
            return JSONResponse({"model": "example/stock", "choices": []})

        async def stream() -> AsyncIterator[bytes]:
            try:
                yield b'data: {"model":"example/stock","choices":[]}\n\n'
                await asyncio.Event().wait()
            finally:
                closed.put_nowait(True)

        return StreamingResponse(stream(), media_type="text/event-stream")

    async with live_server(native) as upstream:
        registry = SimpleNamespace(
            resolve_inference=AsyncMock(
                return_value=InferenceDescriptor(ChatTarget(upstream, "example/stock"))
            )
        )
        proxy = create_inference_app(Settings(inference_api_key=KEY), registry)
        edge = FastAPI()
        edge.mount("/v1", proxy)
        async with live_server(edge) as url, httpx.AsyncClient(base_url=url, timeout=3) as client:
            iterators = []
            async with AsyncExitStack() as streams:
                for _ in range(MAX_INFLIGHT):
                    response = await streams.enter_async_context(
                        client.stream(
                            "POST",
                            "/v1/chat/completions",
                            headers=AUTH,
                            json={"model": "local-model", "stream": True},
                        )
                    )
                    assert response.status_code == 200
                    async with asyncio.timeout(2):
                        lines = response.aiter_lines()
                        iterators.append(lines)
                        first = await anext(lines)
                    assert json.loads(first.removeprefix("data: "))["model"] == "local-model"
                assert (
                    await client.post(
                        "/v1/chat/completions", headers=AUTH, json={"model": "local-model"}
                    )
                ).status_code == 429
            for _ in range(MAX_INFLIGHT):
                await asyncio.wait_for(closed.get(), 2)
            assert (
                await client.post(
                    "/v1/chat/completions", headers=AUTH, json={"model": "local-model"}
                )
            ).status_code == 200
