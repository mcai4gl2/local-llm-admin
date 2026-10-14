"""M8: embedding request checks and upstream response checks (pure)."""

import base64
import math
import struct

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.embed import (
    EmbedError,
    encode_base64,
    parse_embeddings,
    parse_rerank,
    parse_usage,
    texts_of,
    timeout_for,
    truncate,
    upstream_error,
    validate_embed,
    validate_rerank,
)

S = Settings(embed_max_inputs=3, embed_max_input_chars=20, rerank_max_documents=2, embed_dims=8)


def _err(fn, *a):
    with pytest.raises(EmbedError) as e:
        fn(*a)
    return e.value


def test_validate_embed_accepts_string_or_list():
    assert texts_of(validate_embed({"model": "qwen3-embed", "input": "hi"}, S)) == ["hi"]
    wide = S.model_copy(update={"embed_dims": 64})  # S's 8 is below the 32 minimum
    req = validate_embed(
        {"model": "qwen3-embed", "input": ["a", "b"], "dimensions": 32, "user": "x"}, wide
    )
    assert texts_of(req) == ["a", "b"] and req.dimensions == 32


@pytest.mark.parametrize(
    ("body", "msg"),
    [
        ({"model": "other", "input": "x"}, "model must be qwen3-embed"),
        ({"model": "qwen3-embed", "input": []}, "input is empty"),
        ({"model": "qwen3-embed", "input": ["a", "b", "c", "d"]}, "at most 3 inputs"),
        ({"model": "qwen3-embed", "input": ["a", ""]}, "input 1 is empty"),
        ({"model": "qwen3-embed", "input": "x" * 21}, "input 0 is longer than 20"),
        ({"model": "qwen3-embed", "input": "x", "dimensions": 16}, "dimensions must be 32-8"),
        ({"model": "qwen3-embed", "input": "x", "extra": 1}, "extra"),
        ({"model": "qwen3-embed"}, "input"),
        ("nope", "body"),
    ],
)
def test_validate_embed_rejects(body, msg):
    e = _err(validate_embed, body, S)
    assert e.code == 400 and msg in e.message


def test_validate_rerank():
    req = validate_rerank(
        {"model": "qwen3-rerank", "query": "q", "documents": ["a", "b"], "top_n": 1}, S
    )
    assert req.top_n == 1 and not req.return_documents
    assert (
        "at most 2 documents"
        in _err(
            validate_rerank, {"model": "qwen3-rerank", "query": "q", "documents": ["a"] * 3}, S
        ).message
    )
    assert (
        "model must be qwen3-rerank"
        in _err(validate_rerank, {"model": "x", "query": "q", "documents": ["a"]}, S).message
    )
    assert (
        "query is empty"
        in _err(
            validate_rerank, {"model": "qwen3-rerank", "query": "", "documents": ["a"]}, S
        ).message
    )
    assert (
        "top_n"
        in _err(
            validate_rerank,
            {"model": "qwen3-rerank", "query": "q", "documents": ["a"], "top_n": 0},
            S,
        ).message
    )
    long = "i" * 1001
    assert (
        "instruction"
        in _err(
            validate_rerank,
            {"model": "qwen3-rerank", "query": "q", "documents": ["a"], "instruction": long},
            S,
        ).message
    )


def test_truncate_renormalises():
    v = truncate([3.0, 4.0, 100.0], 2)
    assert v == [0.6, 0.8] and math.isclose(sum(x * x for x in v), 1.0)
    assert _err(truncate, [0.0, 0.0, 1.0], 2).code == 502


def test_encode_base64_round_trips():
    raw = base64.b64decode(encode_base64([1.5, -2.0]))
    assert list(struct.unpack("<2f", raw)) == [1.5, -2.0]


def _emb(vectors, shuffle=False):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"data": list(reversed(data)) if shuffle else data, "usage": {"prompt_tokens": 7}}


def test_parse_embeddings_orders_by_index():
    out = parse_embeddings(_emb([[1.0, 0.0], [0.0, 1.0]], shuffle=True), 2, 2)
    assert out == [[1.0, 0.0], [0.0, 1.0]] and parse_usage(_emb([[1.0]])) == 7
    assert parse_usage({}) is None


@pytest.mark.parametrize(
    ("data", "n", "width"),
    [
        (_emb([[1.0, 0.0]]), 2, 2),  # one vector fewer than inputs
        (_emb([[1.0, float("nan")], [0.0, 1.0]]), 2, 2),  # NaN
        (_emb([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), 2, 2),  # wrong width
        (
            {
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
            2,
            2,
        ),
        ({"data": [{"index": 0, "embedding": "abc"}]}, 1, 2),  # not a list of numbers
        ({"results": []}, 1, 2),  # not an embeddings response
        ([], 1, 2),
    ],
)
def test_parse_embeddings_rejects(data, n, width):
    assert _err(parse_embeddings, data, n, width).code == 502


def test_parse_rerank():
    data = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}
    assert sorted(parse_rerank(data, 2)) == [(0, 0.1), (1, 0.9)]


@pytest.mark.parametrize(
    "data",
    [
        {"results": [{"index": 0, "relevance_score": 0.5}]},  # missing a document
        {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}]},
        {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 2, "relevance_score": 0.4}]},
        {
            "results": [
                {"index": 0, "relevance_score": float("inf")},
                {"index": 1, "relevance_score": 0.4},
            ]
        },
        {
            "results": [
                {"index": True, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.4},
            ]
        },
        {"data": []},
    ],
)
def test_parse_rerank_rejects(data):
    assert _err(parse_rerank, data, 2).code == 502


def test_timeout_and_upstream_errors():
    s = Settings(embed_timeout_base_s=30.0, embed_timeout_per_input_s=0.5)
    assert timeout_for(10, s) == 35.0
    assert upstream_error(401, None).code == 502
    assert upstream_error(503, {"detail": "busy"}).code == 429
    assert upstream_error(422, {"detail": "bad input"}).message == "bad input"
    assert upstream_error(500, None).code == 502


# --- shapes recorded on the Mac (Phase 0, 2026-10-10) ------------------------------------

FIX = __import__("pathlib").Path(__file__).parent / "fixtures" / "embed"


def test_real_upstream_responses_parse():
    import json

    emb = json.loads((FIX / "embeddings_ok.json").read_text())
    vectors = parse_embeddings(emb, 2, 4)  # trimmed to 4 dims
    assert len(vectors) == 2 and parse_usage(emb) == emb["usage"]["prompt_tokens"]
    rr = json.loads((FIX / "rerank_ok.json").read_text())
    scores = dict(parse_rerank(rr, 2))  # upstream sorts by score; parse keeps indices
    assert scores[1] > 0.9 > 0.001 > scores[0]
    err = json.loads((FIX / "error_400.json").read_text())
    assert upstream_error(400, err).code == 400 and "embedding model" in upstream_error(400, err).message


def test_phase0_defaults():
    s = Settings()
    assert s.embed_max_inputs == 32 and s.embed_footprint_gb == 9.0
    assert s.embed_timeout_per_input_s == 0.2 and s.embed_min_interval_ms == 1500
    assert s.embed_ready_path == "/health"
