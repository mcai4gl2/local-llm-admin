"""Correctness gate for the embedding service (M8 Phase 0, release live check).

Compares a running endpoint's vectors and rerank scores with references computed once by
``ops/embed-reference.py`` (sentence-transformers, the model card's own code). Plausible
but wrong vectors (wrong pooling, missing prompt) would otherwise go unnoticed."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import httpx

MIN_COSINE = 0.99
# The instruction ops/embed-reference.py scored with (the model card's default). Upstream
# defaults differ (MLX-VLM 0.7.3 uses its own), so the gate always sends this one.
RERANK_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"


def cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def compare_vectors(ref: list[list[float]], got: list[list[float]], min_cos: float) -> list[str]:
    if len(got) != len(ref):
        return [f"expected {len(ref)} vectors, got {len(got)}"]
    if got and len(got[0]) != len(ref[0]):
        return [f"width {len(got[0])} differs from the reference's {len(ref[0])}"]
    out = []
    for i, (r, g) in enumerate(zip(ref, got, strict=True)):
        c = cosine(r, g)
        if c < min_cos:
            out.append(f"vector {i}: cosine {c:.3f} < {min_cos}")
    return out


def _order(scores: list[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: -scores[i])


def compare_rankings(ref: list[float], got: list[float]) -> list[str]:
    want, have = _order(ref), _order(got)
    return [] if want == have else [f"order {have} differs from the reference's {want}"]


def _nearest(queries: list[list[float]], docs: list[list[float]]) -> list[int]:
    return [max(range(len(docs)), key=lambda j: cosine(q, docs[j])) for q in queries]


def run(base: str, ref: dict[str, Any], key: str, embed_model: str, rerank_model: str) -> list[str]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    fails: list[str] = []
    with httpx.Client(base_url=base, timeout=300.0, headers=headers) as http:

        def embed(texts: list[str]) -> list[list[float]]:
            r = http.post("/v1/embeddings", json={"model": embed_model, "input": texts})
            r.raise_for_status()
            return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]

        q = embed([ref["query_prefix"] + t for t in ref["queries"]])
        d = embed(ref["documents"])
        fails += [f"query {m}" for m in compare_vectors(ref["query_vectors"], q, MIN_COSINE)]
        fails += [f"document {m}" for m in compare_vectors(ref["document_vectors"], d, MIN_COSINE)]
        want = _nearest(ref["query_vectors"], ref["document_vectors"])
        if _nearest(q, d) != want:
            fails.append(f"nearest documents {_nearest(q, d)} differ from the reference's {want}")
        for i, case in enumerate(ref["rerank"]):
            r = http.post(
                "/v1/rerank",
                json={
                    "model": rerank_model,
                    "query": case["query"],
                    "documents": case["documents"],
                    "instruction": RERANK_INSTRUCTION,
                },
            )
            r.raise_for_status()
            scores = [0.0] * len(case["documents"])
            for item in r.json()["results"]:
                scores[item["index"]] = item["relevance_score"]
            fails += [f"rerank {i}: {m}" for m in compare_rankings(case["scores"], scores)]
    return fails


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m local_llm_admin.embed_check")
    p.add_argument("--base", required=True, help="e.g. http://127.0.0.1:8084 or http://<mac>:8082")
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--key", default="")
    p.add_argument("--embed-model", default="qwen3-embed")
    p.add_argument("--rerank-model", default="qwen3-rerank")
    a = p.parse_args(argv)
    fails = run(a.base, json.loads(a.reference.read_text()), a.key, a.embed_model, a.rerank_model)
    for f in fails:
        print(f"FAIL {f}")
    print("PASS" if not fails else f"{len(fails)} failure(s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
