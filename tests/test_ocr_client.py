"""Bounded OCR admission is shared and survives ambiguous upstream failures."""

import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest

from local_llm_admin.config import Settings
from local_llm_admin.ocr import OcrClient, OcrDocument, OcrError, OcrLimits

from .ocr_fixtures import png, result


def client(handler, **limits):
    return OcrClient(
        Settings(ocr_port=8097), transport=httpx.MockTransport(handler),
        limits=replace(OcrLimits(), **limits),
    )


async def test_forwards_bytes_and_validates_result():
    reads = []
    async def handler(request):
        reads.append(json.loads(request.content))
        return httpx.Response(200, json=result())
    ocr = client(handler)
    response = await ocr.recognize(OcrDocument(png(), "image/png"))
    assert response.text == "Page 1"
    assert base64.b64decode(reads[0]["data"]) == png()
    assert reads[0]["media_type"] == "image/png" and "filename" not in reads[0]
    assert not ocr.busy


async def test_bounded_queue_serializes_all_clients():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=result())
    ocr = client(handler, queue_depth=1)
    first = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await entered.wait()
    second = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await asyncio.sleep(0)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 429 and len(calls) == 1
    release.set()
    await asyncio.gather(first, second)
    assert len(calls) == 2 and not ocr.busy


async def test_timeout_cannot_release_ambiguous_gpu_admission():
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(503)
        raise httpx.ReadTimeout("private document text", request=request)
    ocr = client(handler)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 504 and "private" not in exc.value.message
    assert ocr.busy
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 503


async def test_timeout_with_confirmed_cleanup_returns_timeout_without_freezing():
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"request_id": request.url.path.split("/")[-2], "stopped": True})
        raise httpx.ReadTimeout("timeout", request=request)
    ocr = client(handler)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 504 and not ocr.busy


async def test_disconnect_waits_for_confirmed_stop_before_admitting_more_work():
    entered, aborting, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            aborting.set()
            await stopped.wait()
            return httpx.Response(200, json={"request_id": request.url.path.split("/")[-2], "stopped": True})
        entered.set()
        await asyncio.Event().wait()
    ocr = client(handler)
    task = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await aborting.wait()
    assert ocr.busy
    stopped.set()
    await ocr.drain()
    assert not ocr.busy


async def test_cancelling_a_queued_request_never_cancels_the_active_one():
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        assert not request.url.path.endswith("/cancel")
        entered.set()
        await release.wait()
        return httpx.Response(200, json=result())
    ocr = client(handler)
    first = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await entered.wait()
    queued = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await first
    await ocr.drain()
    assert not ocr.busy


async def test_deadline_is_enforced_even_if_transport_ignores_http_timeouts():
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"request_id": request.url.path.split("/")[-2], "stopped": True})
        await asyncio.Event().wait()
    ocr = client(handler, document_timeout_s=0.02)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 504 and not ocr.busy


async def test_untrusted_upstream_errors_do_not_expose_content():
    async def handler(request):
        return httpx.Response(500, text="source.pdf: confidential extracted text")
    ocr = client(handler)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 502 and "confidential" not in str(exc.value)


async def test_validated_upstream_input_refusal_maps_without_body_leak():
    async def handler(request):
        return httpx.Response(422, json={"error": "private content"})
    ocr = client(handler)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 422 and "private" not in str(exc.value)


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="invalid JSON"), httpx.Response(200, content=b"x" * 1000),
])
async def test_malformed_and_oversized_upstream_output(response):
    ocr = client(lambda _: response, max_output_bytes=100)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 502


@pytest.mark.parametrize("ack", [None, [], {"stopped": True, "request_id": "wrong"}])
async def test_bad_cancellation_acknowledgement_keeps_admission_frozen(ack):
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json=ack) if ack is not None else httpx.Response(200, text="not JSON")
        raise httpx.ConnectError("private upstream path", request=request)
    ocr = client(handler)
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 503 and ocr.busy


async def test_cancellation_transport_failure_keeps_admission_frozen():
    async def handler(request):
        raise httpx.ConnectError("private document", request=request)
    ocr = client(handler)
    with pytest.raises(OcrError):
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert ocr.busy


async def test_queued_timeout_and_active_failure_refuse_waiters_safely():
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        if request.url.path.endswith("/cancel"):
            return httpx.Response(503)
        entered.set()
        await release.wait()
        raise httpx.ReadTimeout("upstream busy", request=request)
    ocr = client(handler, queue_timeout_s=0.02)
    active = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await entered.wait()
    with pytest.raises(OcrError) as exc:
        await ocr.recognize(OcrDocument(png(), "image/png"))
    assert exc.value.code == 429
    queued = asyncio.create_task(ocr.recognize(OcrDocument(png(), "image/png")))
    await asyncio.sleep(0)
    release.set()
    failures = await asyncio.gather(active, queued, return_exceptions=True)
    assert [f.code for f in failures] == [504, 503]


@pytest.mark.parametrize("body,status", [
    ({"ready": True, "warmup": True, "instance": "generation-a"}, 200),
    ({"ready": True, "warmup": False, "instance": "generation-a"}, 200),
    ({"ready": False, "warmup": True, "instance": "generation-a"}, 200),
    ({"ready": True, "warmup": True, "instance": ""}, 200),
    ([], 200), ({}, 503),
])
def test_functional_readiness_requires_complete_warmup_metadata(body, status):
    ocr = client(lambda _: httpx.Response(status, json=body))
    assert ocr.warmup_sync() == (status == 200 and isinstance(body, dict) and body.get("ready") is True and body.get("warmup") is True and bool(body.get("instance")))


def test_readiness_failures_are_sanitized():
    ocr = client(lambda _: httpx.Response(200, text="private server output"))
    assert not ocr.warmup_sync()
    def unavailable(request):
        raise httpx.ConnectError("private server path", request=request)
    ocr.transport = httpx.MockTransport(unavailable)
    assert not ocr.warmup_sync()


def test_same_generation_cannot_clear_ambiguous_work():
    body = {"ready": True, "warmup": True, "instance": "generation-a"}
    ocr = client(lambda _: httpx.Response(200, json=body))
    assert ocr.warmup_sync()
    ocr._uncertain = True
    assert not ocr.warmup_sync()
    body["instance"] = "generation-b"
    assert ocr.warmup_sync() and not ocr.busy


def test_unknown_generation_cannot_clear_ambiguous_work_without_verified_restart():
    ocr = client(lambda _: httpx.Response(200, json={
        "ready": True, "warmup": True, "instance": "generation-a",
    }))
    ocr._uncertain = True
    assert not ocr.warmup_sync()
    assert ocr.warmup_sync(restarted=True)
@pytest.mark.parametrize("body,code,reason", [
    ({"state": "loading", "ready": False}, 503, "loading"),
    ({"state": "failed", "ready": False}, 503, "failed"),
    ({"ready": True}, 200, None),
    ([], 503, "failed"),
])
def test_health_reason_distinguishes_failed_dependencies_from_loading(body, code, reason):
    client = OcrClient(Settings(ocr_port=8097), transport=httpx.MockTransport(
        lambda _: httpx.Response(code, json=body)))
    assert client.unavailable_reason_sync() == reason
    client._uncertain = True
    assert client.unavailable_reason_sync() == "failed"


def test_health_transport_or_json_failure_is_unavailable():
    for handler in (lambda _: httpx.Response(503, content=b"not JSON"),
                    lambda request: (_ for _ in ()).throw(httpx.ConnectError("private endpoint"))):
        client = OcrClient(Settings(ocr_port=8097), transport=httpx.MockTransport(handler))
        assert client.unavailable_reason_sync() == "failed"
