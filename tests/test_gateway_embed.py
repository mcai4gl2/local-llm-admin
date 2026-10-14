"""M8: the LAN gateway's embeddings and rerank routes."""

import base64
import json
import struct

import httpx
import pytest

from local_llm_admin.decision import DecisionClient
from local_llm_admin.decision_keys import ClientKeys
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.embed import EmbedClient
from local_llm_admin.embed_store import EmbedStore
from local_llm_admin.engines import ComfyClient
from local_llm_admin.gateway import create_gateway_app
from local_llm_admin.jobs import JobStore
from local_llm_admin.sidecars import (
    SidecarRegistry,
    embed_spec,
    service_unavailable_reason,
    write_service_intent,
)

EMB = {"model": "qwen3-embed", "input": ["hello", "world!"]}
RR = {"model": "qwen3-rerank", "query": "q", "documents": ["a", "abcdef"], "top_n": 1}


@pytest.fixture
async def gw(fake_embed, fake_decision, fake_comfy):
    s = fake_embed.settings
    db = s.admin_data_dir / "admin.sqlite"
    keys, store = ClientKeys(db), JobStore(db)
    reg = SidecarRegistry(s, {"qwen-embed": fake_embed.transport})
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    embed = EmbedClient(s, transport=fake_embed.transport)
    app = create_gateway_app(
        s,
        DecisionClient(s, transport=fake_decision.transport),
        keys,
        DecisionStore(db),
        registry=reg,
        embed=embed,
        embed_history=EmbedStore(db),
        embed_reason=lambda: service_unavailable_reason(
            reg, store, "qwen-embed", lambda: comfy.running().running
        ),
    )
    app.state.test = {"embed": embed, "store": store, "history": EmbedStore(db)}
    alice = keys.create("alice")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 5555)), base_url="http://gw"
    ) as c:
        c.headers["Authorization"] = f"Bearer {alice['key']}"
        c.app = app
        yield c


async def test_embeddings_ok_and_usage_row(gw, fake_embed):
    fake_embed.start()
    r = await gw.post("/v1/embeddings", json=EMB)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list" and body["model"] == "qwen3-embed"
    assert [d["index"] for d in body["data"]] == [0, 1] and len(body["data"][0]["embedding"]) == 8
    assert body["usage"]["prompt_tokens"] > 0
    row = gw.app.state.test["history"].recent(1)[0]
    assert (
        row["route"] == "embed" and row["inputs"] == 2 and row["chars"] == 11 and row["dims"] == 8
    )
    assert "hello" not in json.dumps(row)


async def test_embeddings_base64_and_dimensions(gw, fake_embed):
    fake_embed.settings.embed_dims = 64
    fake_embed.flags["width"] = 64
    fake_embed.start()
    r = await gw.post("/v1/embeddings", json={**EMB, "dimensions": 32, "encoding_format": "base64"})
    raw = base64.b64decode(r.json()["data"][0]["embedding"])
    vec = struct.unpack("<32f", raw)
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-5


async def test_rerank_ok(gw, fake_embed):
    fake_embed.start()
    r = await gw.post("/v1/rerank", json=RR)
    assert r.status_code == 200 and r.json()["results"] == [{"index": 1, "relevance_score": 0.375}]
    assert gw.app.state.test["history"].recent(1)[0]["route"] == "rerank"


async def test_embeddings_503_reasons(gw, fake_embed, fake_comfy):
    spec = embed_spec(fake_embed.settings)
    r = await gw.post("/v1/embeddings", json=EMB)
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "stopped"
    write_service_intent(gw.app.state.test["store"], spec, "running")
    assert (await gw.post("/v1/embeddings", json=EMB)).json()["detail"]["reason"] == "down"
    import subprocess

    subprocess.run([str(fake_embed.settings.comfyui_path), "start"], capture_output=True)
    assert (await gw.post("/v1/rerank", json=RR)).json()["detail"]["reason"] == "comfyui_active"
    assert "qwen-embed start" not in (fake_embed.state / "calls.log").read_text()  # never starts it


async def test_in_flight_limit_429(gw, fake_embed):
    fake_embed.start()
    embed = gw.app.state.test["embed"]
    while embed.try_acquire():
        pass
    r = await gw.post("/v1/embeddings", json=EMB)
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    r = await gw.post("/v1/rerank", json=RR)
    assert r.status_code == 429  # one pool for both routes


async def test_bad_requests(gw, fake_embed):
    fake_embed.start()
    assert (await gw.post("/v1/embeddings", content=b"nope")).status_code == 400
    assert (await gw.post("/v1/embeddings", json={"model": "x", "input": "a"})).status_code == 400
    big = {"model": "qwen3-embed", "input": ["x" * 30000] * 140}
    assert (await gw.post("/v1/embeddings", json=big)).status_code == 413
    fake_embed.flags["width"] = 3
    assert (await gw.post("/v1/embeddings", json=EMB)).status_code == 502


async def test_models_lists_embed_ids_without_a_sidecar(gw, fake_embed):
    assert (await gw.get("/v1/models")).status_code == 503
    fake_embed.start()
    ids = [m["id"] for m in (await gw.get("/v1/models")).json()["data"]]
    assert ids == ["qwen3-embed", "qwen3-rerank"]


async def test_keys_required_when_configured(gw, fake_embed):
    fake_embed.settings.decision_gateway_require_key = True
    fake_embed.start()
    r = await gw.post("/v1/embeddings", json=EMB, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
