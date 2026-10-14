"""The embedding service adapter (M8): request checks, the loopback client, and checks
on everything the service returns. The service is driven only through embed/bin/qwen-embed
and its loopback HTTP API; the admin never imports mlx-vlm.

The upstream contract is MLX-VLM's /v1/embeddings and /v1/rerank as recorded in Phase 0
(plan record). Plausible but wrong output (a missing vector, a NaN, a wrong width, a
duplicate rerank index) is a 502, never passed on. The service applies no prompt format:
clients add Qwen3's query instruction themselves (D-E9). ``dimensions`` is always applied
here by truncating and re-normalising (MRL), never sent upstream (D-E10)."""

from __future__ import annotations

import asyncio
import base64
import math
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .config import Settings

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_INSTRUCTION = 1000
MIN_DIMS = 32
# Qwen3-Embedding's retrieval instruction; the admin test page adds it for "query" texts
QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)
# Qwen3-Reranker's model-card instruction, sent when a client gives none: MLX-VLM's own
# default ranks clearly worse (Phase 0: 0.11 vs 0.73 on an obvious match)
RERANK_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"


class EmbedError(Exception):
    """A refused or failed request; ``code`` is the HTTP status the routes return."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbedRequest(BaseModel):
    """OpenAI ``POST /v1/embeddings``, narrowed."""

    model_config = ConfigDict(extra="forbid")
    model: str
    input: str | list[str]
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: int | None = None
    user: str | None = None  # OpenAI clients send it; ignored


class RerankRequest(BaseModel):
    """Cohere/Jina-style ``POST /v1/rerank``."""

    model_config = ConfigDict(extra="forbid")
    model: str
    query: str
    documents: list[str]
    top_n: int | None = None
    return_documents: bool = False
    instruction: str | None = None


def _first(exc: ValidationError) -> str:
    e = exc.errors()[0]
    where = ".".join(str(p) for p in e["loc"]) or "body"
    return f"{where}: {e['msg']}"


def _parse(model: type[BaseModel], body: Any) -> Any:
    if not isinstance(body, dict):
        raise EmbedError(400, "body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise EmbedError(400, _first(exc)) from None


def texts_of(req: EmbedRequest) -> list[str]:
    return [req.input] if isinstance(req.input, str) else list(req.input)


def validate_embed(body: Any, settings: Settings) -> EmbedRequest:
    req: EmbedRequest = _parse(EmbedRequest, body)
    if req.model != settings.embed_model_id:
        raise EmbedError(400, f"model must be {settings.embed_model_id}")
    texts = texts_of(req)
    if not texts:
        raise EmbedError(400, "input is empty")
    if len(texts) > settings.embed_max_inputs:
        raise EmbedError(400, f"at most {settings.embed_max_inputs} inputs per request")
    for i, t in enumerate(texts):
        if not t:
            raise EmbedError(400, f"input {i} is empty")
        if len(t) > settings.embed_max_input_chars:
            raise EmbedError(
                400, f"input {i} is longer than {settings.embed_max_input_chars} characters"
            )
    if req.dimensions is not None and not MIN_DIMS <= req.dimensions <= settings.embed_dims:
        raise EmbedError(400, f"dimensions must be {MIN_DIMS}-{settings.embed_dims}")
    return req


def validate_rerank(body: Any, settings: Settings) -> RerankRequest:
    req: RerankRequest = _parse(RerankRequest, body)
    if req.model != settings.rerank_model_id:
        raise EmbedError(400, f"model must be {settings.rerank_model_id}")
    cap = settings.embed_max_input_chars
    if not req.query:
        raise EmbedError(400, "query is empty")
    if len(req.query) > cap:
        raise EmbedError(400, f"query is longer than {cap} characters")
    if not req.documents:
        raise EmbedError(400, "documents is empty")
    if len(req.documents) > settings.rerank_max_documents:
        raise EmbedError(400, f"at most {settings.rerank_max_documents} documents per request")
    for i, d in enumerate(req.documents):
        if len(d) > cap:
            raise EmbedError(400, f"document {i} is longer than {cap} characters")
    if req.top_n is not None and req.top_n < 1:
        raise EmbedError(400, "top_n must be at least 1")
    if req.instruction is not None and len(req.instruction) > MAX_INSTRUCTION:
        raise EmbedError(400, f"instruction is longer than {MAX_INSTRUCTION} characters")
    return req


def truncate(vec: list[float], dims: int) -> list[float]:
    head = vec[:dims]
    norm = math.sqrt(sum(x * x for x in head))
    if norm == 0.0:
        raise EmbedError(502, "the embedding service returned a zero vector")
    return [x / norm for x in head]


def encode_base64(vec: list[float]) -> str:
    """OpenAI's base64 format: little-endian float32."""
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode()


def _number(x: Any) -> TypeGuard[float]:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def parse_embeddings(data: Any, n: int, width: int) -> list[list[float]]:
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise EmbedError(502, "the embedding service returned no data")
    if len(items) != n:
        raise EmbedError(502, f"the embedding service returned {len(items)} vectors for {n} inputs")
    out: list[list[float] | None] = [None] * n
    for pos, item in enumerate(items):
        idx = item.get("index", pos) if isinstance(item, dict) else None
        emb = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n or out[idx]:
            raise EmbedError(502, "the embedding service returned a bad or repeated index")
        if not isinstance(emb, list) or not all(_number(x) for x in emb):
            raise EmbedError(502, "the embedding service returned a malformed vector")
        if len(emb) != width:
            raise EmbedError(
                502, f"the embedding service returned width {len(emb)}, expected {width}"
            )
        out[idx] = [float(x) for x in emb]
    return [v for v in out if v is not None]


def parse_usage(data: Any) -> int | None:
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None


def parse_rerank(data: Any, n: int) -> list[tuple[int, float]]:
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != n:
        raise EmbedError(502, "the rerank service returned the wrong number of scores")
    seen: set[int] = set()
    out: list[tuple[int, float]] = []
    for item in items:
        idx = item.get("index") if isinstance(item, dict) else None
        score = item.get("relevance_score") if isinstance(item, dict) else None
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n or idx in seen:
            raise EmbedError(502, "the rerank service returned a bad or repeated index")
        if not _number(score):
            raise EmbedError(502, "the rerank service returned a malformed score")
        seen.add(idx)
        out.append((idx, float(score)))
    return out


def timeout_for(n: int, settings: Settings) -> float:
    return settings.embed_timeout_base_s + n * settings.embed_timeout_per_input_s


def upstream_error(status: int, body: Any) -> EmbedError:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("error_type") or "error")
    elif isinstance(detail, str):
        message = detail
    else:
        message = f"upstream returned HTTP {status}"
    if status in (401, 403):
        return EmbedError(502, "the embedding service refused the admin's key")
    if status in (429, 503):
        return EmbedError(429, f"the embedding service is busy: {message}")
    if 400 <= status < 500:
        return EmbedError(400, message)
    return EmbedError(502, f"the embedding service failed (HTTP {status})")


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    dims: int
    tokens: int | None
    latency_ms: float


@dataclass(frozen=True)
class RerankResult:
    results: list[dict[str, Any]]
    latency_ms: float


class EmbedClient:
    """Talks to the service on loopback. At most ``embed_max_in_flight`` requests at once,
    shared by the admin page and the gateway, so bulk work never takes over the GPU."""

    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._lock = threading.Lock()
        self._inflight = 0
        # one upstream call at a time, then a gap, so the chat model gets the GPU back
        self._pace: asyncio.Lock | None = None
        self._free_at = 0.0

    def try_acquire(self) -> bool:
        with self._lock:
            if self._inflight >= self.settings.embed_max_in_flight:
                return False
            self._inflight += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def _headers(self) -> dict[str, str]:
        return {}  # loopback only; EMBED_API_KEY is reserved (Phase 0 record)

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> Any:
        if self._pace is None:
            self._pace = asyncio.Lock()
        async with self._pace:
            wait = self._free_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._post_now(path, body, timeout)
            finally:
                self._free_at = time.monotonic() + self.settings.embed_min_interval_ms / 1000

    async def _post_now(self, path: str, body: dict[str, Any], timeout: float) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.embed_api_base, timeout=timeout, transport=self._transport
            ) as http:
                resp = await http.post(path, json=body, headers=self._headers())
        except httpx.TimeoutException:
            raise EmbedError(
                504, f"the embedding service did not answer within {timeout:.0f} s"
            ) from None
        except httpx.HTTPError:
            raise EmbedError(503, "the embedding service is not reachable") from None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code != 200:
            raise upstream_error(resp.status_code, data)
        if data is None:
            raise EmbedError(502, "the embedding service returned a non-JSON response")
        return data

    async def embed(self, req: EmbedRequest) -> EmbedResult:
        """An already-validated request; the caller holds a slot."""
        texts = texts_of(req)
        t0 = time.perf_counter()
        data = await self._post(
            "/v1/embeddings",
            {
                "model": self.settings.embed_upstream_model,
                "input": texts,
                "encoding_format": "float",
            },
            timeout_for(len(texts), self.settings),
        )
        vectors = parse_embeddings(data, len(texts), self.settings.embed_dims)
        if req.dimensions is not None and req.dimensions < self.settings.embed_dims:
            vectors = [truncate(v, req.dimensions) for v in vectors]
        return EmbedResult(
            vectors,
            len(vectors[0]),
            parse_usage(data),
            round((time.perf_counter() - t0) * 1000, 1),
        )

    async def rerank(self, req: RerankRequest) -> RerankResult:
        t0 = time.perf_counter()
        body: dict[str, Any] = {
            "model": self.settings.rerank_upstream_model,
            "query": req.query,
            "documents": req.documents,
            "top_n": len(req.documents),  # every score comes back; top_n is applied here
            "return_documents": False,
        }
        body["instruction"] = req.instruction or RERANK_INSTRUCTION
        data = await self._post("/v1/rerank", body, timeout_for(len(req.documents), self.settings))
        pairs = sorted(parse_rerank(data, len(req.documents)), key=lambda p: -p[1])
        if req.top_n is not None:
            pairs = pairs[: req.top_n]
        results = [
            {
                "index": i,
                "relevance_score": score,
                **({"document": {"text": req.documents[i]}} if req.return_documents else {}),
            }
            for i, score in pairs
        ]
        return RerankResult(results, round((time.perf_counter() - t0) * 1000, 1))

    def warmup_sync(self) -> bool:
        """One embedding and one rerank, so a start job succeeds only once both models
        answer correctly (the first call loads each model)."""
        s = self.settings
        try:
            with httpx.Client(
                base_url=s.embed_api_base,
                timeout=s.embed_ready_timeout_s,
                transport=self._transport,
            ) as http:
                e = http.post(
                    "/v1/embeddings", json={"model": s.embed_upstream_model, "input": ["warm up"]}
                )
                r = http.post(
                    "/v1/rerank",
                    json={
                        "model": s.rerank_upstream_model,
                        "query": "warm up",
                        "documents": ["warm up"],
                        "top_n": 1,
                    },
                )
            if e.status_code != 200 or r.status_code != 200:
                return False
            parse_embeddings(e.json(), 1, s.embed_dims)
            parse_rerank(r.json(), 1)
        except (httpx.HTTPError, ValueError, EmbedError):
            return False
        return True
