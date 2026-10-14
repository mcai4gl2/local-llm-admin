#!/usr/bin/env python3
"""Reference vectors and rerank scores for the M8 correctness gate.

Runs the model cards' own code (sentence-transformers for Qwen3-Embedding, the yes/no
logit recipe for Qwen3-Reranker) on CPU in a scratch venv:

    python3 -m venv .smoke/embed-ref && .smoke/embed-ref/bin/pip install \
        'sentence-transformers>=3' 'transformers>=4.51' torch
    .smoke/embed-ref/bin/python ops/embed-reference.py tests/fixtures/embed/cases.json \
        tests/fixtures/embed/reference.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForCausalLM, AutoTokenizer

EMBED = "Qwen/Qwen3-Embedding-4B"
RERANK = "Qwen/Qwen3-Reranker-0.6B"
RERANK_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and"
    ' the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def rerank_scores(tok: Any, model: Any, query: str, docs: list[str]) -> list[float]:
    yes, no = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")
    texts = [
        f"{PREFIX}<Instruct>: {RERANK_INSTRUCTION}\n<Query>: {query}\n<Document>: {d}{SUFFIX}"
        for d in docs
    ]
    batch = tok(texts, padding=True, return_tensors="pt")
    with torch.no_grad():
        logits = model(**batch).logits[:, -1, :]
    pair = torch.stack([logits[:, no], logits[:, yes]], dim=1)
    return torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp().tolist()


def main(cases_path: str, out_path: str) -> None:
    cases = json.loads(open(cases_path).read())
    queries = [q for q, _ in cases["pairs"]]
    docs = [d for _, d in cases["pairs"]]
    emb = SentenceTransformer(EMBED, device="cpu", model_kwargs={"torch_dtype": torch.bfloat16})
    qv = emb.encode([cases["query_prefix"] + q for q in queries], normalize_embeddings=True)
    dv = emb.encode(docs, normalize_embeddings=True)
    tok = AutoTokenizer.from_pretrained(RERANK, padding_side="left")
    rr = AutoModelForCausalLM.from_pretrained(RERANK, torch_dtype=torch.float32).eval()
    out = {
        "query_prefix": cases["query_prefix"],
        "queries": queries,
        "documents": docs,
        "query_vectors": qv.tolist(),
        "document_vectors": dv.tolist(),
        "rerank": [
            {**c, "scores": rerank_scores(tok, rr, c["query"], c["documents"])}
            for c in cases["rerank"]
        ],
    }
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"wrote {out_path}: {len(queries)} pairs, width {len(out['query_vectors'][0])}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
