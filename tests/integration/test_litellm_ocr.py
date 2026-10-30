"""Isolated LiteLLM 1.99.1 SDK -> real gateway contract; no live route writes/inference.

Run with the pinned SDK or an isolated read-only copy of the installed SDK.
See docs/ocr.md for the source payload/provenance and reproduction commands.
"""

import asyncio
import base64
import json

import httpx
import pytest

from local_llm_admin.config import Settings
from local_llm_admin.decision import DecisionClient
from local_llm_admin.decision_keys import ClientKeys
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.gateway import create_gateway_app
from local_llm_admin.ocr import OcrClient
from local_llm_admin.service_store import ServiceStore
from tests.ocr_fixtures import chat, png, result

litellm = pytest.importorskip("litellm")
openai = pytest.importorskip("openai")
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest.mark.parametrize("media,data", [("image/png", png()), ("application/pdf", b"%PDF-1.7\n%%EOF")])
async def test_litellm_preserves_inline_image_and_pdf_through_gateway(tmp_path, media, data):
    from importlib.metadata import version
    assert version("litellm") == "1.99.1"
    settings = Settings(ocr_enabled=True, ocr_port=8097, ocr_vlm_port=8098,
                        decision_gateway_require_key=True)
    db = tmp_path / "admin.sqlite"
    keys = ClientKeys(db)
    key = keys.create("app-a-key")["key"]
    captured = []
    async def pipeline(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=result())
    ocr = OcrClient(settings, transport=httpx.MockTransport(pipeline))
    gateway = create_gateway_app(
        settings, DecisionClient(settings), keys, DecisionStore(db), ocr=ocr,
        ocr_reason=lambda: None, service_history=ServiceStore(db, settings),
    )
    litellm.register_model({
        "openai/local-ocr": {"mode": "chat", "supports_vision": True, "supports_pdf_input": True,
                             "supports_streaming": False, "input_cost_per_token": 0,
                             "output_cost_per_token": 0},
    })
    litellm.suppress_debug_info = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway)) as transport:
        client = openai.AsyncOpenAI(api_key=key, base_url="http://ocr-gateway/v1", http_client=transport,
                                    max_retries=0)
        response = await litellm.acompletion(
            model="openai/local-ocr", messages=chat(data, media)["messages"],
            client=client, timeout=300, num_retries=0, max_retries=0,
        )
        assert response.choices[0].message.content == "Page 1"
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
    async with asyncio.timeout(5):
        await asyncio.sleep(0.05)
        await GLOBAL_LOGGING_WORKER.flush()
        await GLOBAL_LOGGING_WORKER.stop()
        await litellm.close_litellm_async_clients()
    assert len(captured) == 1 and captured[0]["media_type"] == media
    assert base64.b64decode(captured[0]["data"]) == data
