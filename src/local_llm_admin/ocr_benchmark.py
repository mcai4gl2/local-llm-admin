"""Explicit public-corpus and joint-load collection through a configured LiteLLM origin."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .ocr import validate_result


def envelope(data: bytes, media: str) -> dict[str, Any]:
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Corpus input exceeds the contract")
    encoded = "data:" + media + ";base64," + base64.b64encode(data).decode()
    part = ({"type": "file", "file": {"file_data": encoded}} if media == "application/pdf" else
            {"type": "image_url", "image_url": {"url": encoded}})
    return {"model": "local-ocr", "messages": [{"role": "user", "content": [part]}],
            "stream": False, "response_format": {"type": "json_object"}}


async def collect_corpus(client: httpx.AsyncClient, corpus: Path, references: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    manifest = json.loads((corpus / "manifest.json").read_text())["files"]
    for name, case in references["cases"].items():
        filename = name + (".pdf" if len(case["pages"]) > 1 else ".png")
        data = (corpus / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest[filename]:
            raise ValueError("Frozen corpus changed")
        try:
            response = await client.post("chat/completions", json=envelope(
                data, "application/pdf" if filename.endswith(".pdf") else "image/png"))
            response.raise_for_status()
            body = json.loads(response.json()["choices"][0]["message"]["content"])
            results[name] = validate_result(body, Settings()).model_dump()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            results[name] = {"complete": False, "truncated": True, "pages": []}
    return results


async def measure(
    client: httpx.AsyncClient, config: dict[str, Any], *, requests: int = 30,
    ocr_document: bytes | None = None,
) -> dict[str, Any]:
    if requests < 30:
        raise ValueError("At least 30 chat and embedding requests are required")
    samples: dict[str, list[float]] = {"chat": [], "embed": []}
    tokens: list[int | None] = []
    errors = 0
    ocr_requests = 0
    finished = asyncio.Event()

    async def traffic(kind: str) -> None:
        nonlocal errors
        for _ in range(requests):
            started = time.perf_counter()
            try:
                if kind == "chat":
                    body = {"model": config["chat_model"], "messages": [{"role": "user", "content":
                            "Describe a quiet public garden in three short sentences."}],
                            "max_tokens": 128, "temperature": 0, "stream": False}
                    response = await client.post("chat/completions", json=body)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload["choices"][0]["message"]["content"], str):
                        raise ValueError("Invalid chat response")
                    count = payload.get("usage", {}).get("completion_tokens")
                    tokens.append(count if type(count) is int and count > 0 else None)
                else:
                    response = await client.post("embeddings", json={
                        "model": config["embed_model"], "input": ["Public garden reference."],
                    })
                    response.raise_for_status()
                    vector = response.json()["data"][0]["embedding"]
                    if not isinstance(vector, list) or not vector:
                        raise ValueError("Invalid embedding response")
                samples[kind].append((time.perf_counter() - started) * 1000)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                errors += 1

    async def ocr_traffic() -> None:
        nonlocal errors, ocr_requests
        if ocr_document is None:
            return
        while not finished.is_set():
            ocr_requests += 1
            try:
                response = await client.post("chat/completions", json=envelope(ocr_document, "image/png"))
                response.raise_for_status()
                body = json.loads(response.json()["choices"][0]["message"]["content"])
                validate_result(body, Settings())
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                errors += 1
                return  # no repeated OCR after an ambiguous failure
            await asyncio.sleep(0)

    competitor = asyncio.create_task(ocr_traffic())
    try:
        await asyncio.gather(traffic("chat"), traffic("embed"))
    finally:
        finished.set()
        await competitor  # retain active work until completion/deadline, never cancel into overlap

    def p95(values: list[float]) -> float | None:
        return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None

    known = len(tokens) == requests and all(value is not None for value in tokens)
    return {"schema_version": 1, "chat_p95_ms": p95(samples["chat"]),
            "embed_p95_ms": p95(samples["embed"]), "errors": errors,
            "chat_tokens_s": sum(value or 0 for value in tokens) / (sum(samples["chat"]) / 1000)
            if known and samples["chat"] else None,
            "chat_requests": len(samples["chat"]), "embed_requests": len(samples["embed"]),
            "ocr_requests": ocr_requests}


async def run(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text())
    base = urlsplit(config["base"])
    if (base.scheme not in ("http", "https") or not base.hostname or base.username
            or base.password or base.query or base.fragment or base.path.rstrip("/")):
        raise ValueError("Use a private LiteLLM HTTP(S) origin")
    if not args.run:
        print(json.dumps({"phase": args.phase, "requests_each": args.requests,
                          "target_hash": hashlib.sha256(config["base"].encode()).hexdigest(), "executes": False}))
        return
    key = os.environ[config.get("api_key_env", "LITELLM_CLIENT_KEY")]
    args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Reserve the report before any inference; never overwrite a frozen outcome.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        try:
            async with httpx.AsyncClient(base_url=config["base"].rstrip("/") + "/v1/",
                                         headers={"Authorization": "Bearer " + key}, timeout=300) as client:
                if args.phase == "corpus":
                    references = json.loads(args.references.read_text())
                    result = await collect_corpus(client, args.corpus, references)
                else:
                    document = (args.corpus / "english.png").read_bytes() if args.phase == "combined" else None
                    result = await measure(client, config, requests=args.requests, ocr_document=document)
            json.dump(result, output, indent=2, sort_keys=True)
        except BaseException:
            json.dump({"complete": False, "reason": "Collection interrupted; preserve this outcome."}, output)
            raise
        finally:
            output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--phase", choices=("corpus", "baseline", "combined"), required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--references", type=Path, default=Path("experiments/ocr/references.json"))
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    asyncio.run(run(parser.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
