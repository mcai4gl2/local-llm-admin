"""M8: the embedding client against the fake service."""

import math

import pytest

from local_llm_admin.embed import EmbedClient, EmbedError, validate_embed, validate_rerank


@pytest.fixture
def client(fake_embed):
    fake_embed.start()
    return EmbedClient(fake_embed.settings, transport=fake_embed.transport)


async def test_embed_full_width_and_usage(client, fake_embed):
    s = fake_embed.settings
    res = await client.embed(validate_embed({"model": "qwen3-embed", "input": ["ab", "abcd"]}, s))
    assert res.dims == 8 and len(res.vectors) == 2 and res.tokens == 3  # fake: len // 4 + 1 each
    path, body = fake_embed.requests[-1]
    assert path == "/v1/embeddings" and body["model"] == s.embed_upstream_model
    assert "dimensions" not in body  # truncation is always local


async def test_embed_truncates_locally(client, fake_embed):
    fake_embed.settings.embed_dims = 64
    fake_embed.flags["width"] = 64
    req = validate_embed(
        {"model": "qwen3-embed", "input": "x", "dimensions": 32}, fake_embed.settings
    )
    res = await client.embed(req)
    assert res.dims == 32 and math.isclose(sum(v * v for v in res.vectors[0]), 1.0)


async def test_embed_wrong_width_is_502(client, fake_embed):
    fake_embed.flags["width"] = 5
    with pytest.raises(EmbedError) as e:
        await client.embed(
            validate_embed({"model": "qwen3-embed", "input": "x"}, fake_embed.settings)
        )
    assert e.value.code == 502


async def test_rerank_sorts_and_cuts(client, fake_embed):
    s = fake_embed.settings
    req = validate_rerank(
        {
            "model": "qwen3-rerank",
            "query": "q",
            "documents": ["a", "abcdef", "abc"],
            "top_n": 2,
            "return_documents": True,
        },
        s,
    )
    res = await client.rerank(req)
    assert [r["index"] for r in res.results] == [1, 2]
    assert res.results[0]["document"] == {"text": "abcdef"}
    assert fake_embed.requests[-1][1]["model"] == s.rerank_upstream_model


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ({"timeout": True}, 504),
        ({"status": 500}, 502),
        ({"status": 400, "body": {"detail": "no"}}, 400),
        ({"raw": b"<html>"}, 502),
    ],
)
async def test_errors_map(client, fake_embed, flags, code):
    fake_embed.flags.update(flags)
    with pytest.raises(EmbedError) as e:
        await client.embed(
            validate_embed({"model": "qwen3-embed", "input": "x"}, fake_embed.settings)
        )
    assert e.value.code == code


async def test_not_listening_is_503(fake_embed):
    c = EmbedClient(fake_embed.settings, transport=fake_embed.transport)
    with pytest.raises(EmbedError) as e:
        await c.embed(validate_embed({"model": "qwen3-embed", "input": "x"}, fake_embed.settings))
    assert e.value.code == 503


def test_in_flight_slot(fake_embed):
    fake_embed.settings.embed_max_in_flight = 2
    c = EmbedClient(fake_embed.settings)
    assert c.try_acquire() and c.try_acquire() and not c.try_acquire()
    c.release()
    assert c.try_acquire()


def test_warmup(client, fake_embed):
    assert client.warmup_sync() is True
    assert [p for p, _ in fake_embed.requests[-2:]] == ["/v1/embeddings", "/v1/rerank"]
    fake_embed.flags["status"] = 500
    assert client.warmup_sync() is False


async def test_rerank_default_instruction(client, fake_embed):
    from local_llm_admin.embed import RERANK_INSTRUCTION

    s = fake_embed.settings
    await client.rerank(validate_rerank({"model": "qwen3-rerank", "query": "q", "documents": ["a"]}, s))
    assert fake_embed.requests[-1][1]["instruction"] == RERANK_INSTRUCTION
    body = {"model": "qwen3-rerank", "query": "q", "documents": ["a"], "instruction": "mine"}
    await client.rerank(validate_rerank(body, s))
    assert fake_embed.requests[-1][1]["instruction"] == "mine"


async def test_min_interval_spaces_upstream_calls(client, fake_embed):
    """Back-to-back embedding starves the chat model's decode (Phase 0: -88%); the client
    leaves a gap after each upstream call, even for concurrent callers."""
    import asyncio
    import time

    s = fake_embed.settings
    req = validate_embed({"model": "qwen3-embed", "input": "x"}, s)
    s.embed_min_interval_ms = 0
    t0 = time.perf_counter()
    await asyncio.gather(client.embed(req), client.embed(req))
    assert time.perf_counter() - t0 < 0.15
    s.embed_min_interval_ms = 200
    t0 = time.perf_counter()
    await asyncio.gather(client.embed(req), client.embed(req), client.embed(req))
    assert time.perf_counter() - t0 >= 0.38  # two gaps between three calls
