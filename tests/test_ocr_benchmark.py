"""Public-input qualification traffic does not retry failed OCR or invent token usage."""

import json

import httpx

from local_llm_admin.ocr_benchmark import measure


async def test_baseline_counts_real_responses_and_omits_unknown_throughput():
    calls = []
    async def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "public text"}}]})
    async with httpx.AsyncClient(base_url="http://litellm.example.com:4000",
                                 transport=httpx.MockTransport(handle)) as client:
        result = await measure(client, {"chat_model": "local-model", "embed_model": "qwen-embed"},
                               requests=30)
    assert len(calls) == 60 and result["chat_requests"] == 30 and result["embed_requests"] == 0
    assert result["chat_tokens_s"] is None and result["errors"] == 30  # invalid embedding responses
    assert result["ocr_requests"] == 0


async def test_ambiguous_ocr_failure_is_attempted_once_during_joint_load():
    import asyncio
    calls = []
    async def handle(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "local-ocr":
            raise httpx.ReadTimeout("upstream execution unknown")
        await asyncio.sleep(0)
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "public"}}],
                                        "usage": {"completion_tokens": 5}})
    async with httpx.AsyncClient(base_url="http://litellm.example.com:4000/v1/",
                                 transport=httpx.MockTransport(handle)) as client:
        result = await measure(client, {"chat_model": "local-model", "embed_model": "qwen-embed"},
                               ocr_document=b"public-fixture")
    assert calls.count("local-ocr") == 1 and result["errors"] == 1
    assert result["chat_requests"] == result["embed_requests"] == 30 and result["chat_tokens_s"] > 0
