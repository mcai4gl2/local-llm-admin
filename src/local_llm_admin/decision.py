"""The OpenJev decision sidecar (M6): client, request checks, memory budget, down alert.

The sidecar is driven only through ``decision/bin/openjev`` and its HTTP API on
loopback; the admin never imports OpenJev or MLX and never uses its venv. It may run
next to the MLX server, never next to ComfyUI (engines.py enforces the latter).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .config import Settings
from .sidecars import SidecarProcess, SidecarStatus, openjev_spec

MODELS = ("openjev-latest", "openjev-0.1", "jev-latest", "jev-preview")
MAX_STATE_BYTES = 32 * 1024
MAX_BODY_BYTES = 64 * 1024

QuestionId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
Described = str | dict[str, Any] | list[Any] | None
ChoiceName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ScoreLevel = str | dict[str, Any] | list[Any]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoulCriteria(_Strict):
    true: Described = None
    false: Described = None


class NoulQuestion(_Strict):
    type: Literal["noul"]
    instructions: Described = None
    criteria: NoulCriteria | None = None


class ChoiceQuestion(_Strict):
    type: Literal["choice"]
    instructions: Described = None
    criteria: Annotated[dict[ChoiceName, Described], Field(min_length=2, max_length=26)]


class ScoreQuestion(_Strict):
    type: Literal["score"]
    instructions: Described = None
    criteria: Annotated[list[ScoreLevel], Field(min_length=2, max_length=10)]


Question = Annotated[NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]


class ReadRequest(_Strict):
    """OpenJev's ``POST /v1/systemone`` body (``openjev/api.py``), narrowed for v1:
    no images, ``think`` or ``sequential``; at most 32 questions and 8 samples."""

    state: str | dict[str, Any] | list[Any]
    model: Literal["openjev-latest", "openjev-0.1", "jev-latest", "jev-preview"] = "openjev-latest"
    questions: Annotated[dict[QuestionId, Question], Field(min_length=1, max_length=32)]
    steps: Annotated[int, Field(ge=1, le=8)] | None = None
    samples: Annotated[int, Field(ge=1, le=8)] | None = None


class DecisionError(Exception):
    """A refused or failed read; ``code`` is the HTTP status the routes return."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_request(body: Any) -> dict[str, Any]:
    """The upstream body, or DecisionError(400) naming the first problem."""
    try:
        size = len(json.dumps(body))
    except (TypeError, ValueError):
        raise DecisionError(400, "body is not JSON") from None
    if size > MAX_BODY_BYTES:
        raise DecisionError(400, f"request is larger than {MAX_BODY_BYTES} bytes")
    try:
        req = ReadRequest.model_validate(body)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"]) or "body"
        raise DecisionError(400, f"{where}: {err['msg']}") from None
    if len(json.dumps(req.state)) > MAX_STATE_BYTES:
        raise DecisionError(400, f"state is larger than {MAX_STATE_BYTES} bytes")
    out: dict[str, Any] = req.model_dump(exclude_none=True)
    return out


def parse_server_timing(header: str | None) -> dict[str, float]:
    """``model;dur=0.0, server;dur=400.9, total;dur=400.9`` -> {name: ms}."""
    out: dict[str, float] = {}
    for part in (header or "").split(","):
        name, _, rest = part.strip().partition(";")
        m = re.search(r"dur=([0-9.]+)", rest)
        if name and m:
            out[name] = float(m.group(1))
    return out


def _valid_probs(probs: Any) -> bool:
    if not isinstance(probs, dict) or not probs:
        return False
    vals = list(probs.values())
    if not all(isinstance(v, int | float) and math.isfinite(v) and v >= 0 for v in vals):
        return False
    return bool(abs(sum(vals) - 1.0) <= 1e-3)


def normalize_answers(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """Upstream answers -> ``{id, type, probs, top, confidence, expected, invalid}`` rows.

    A distribution that is not finite, non-negative and summing to 1 (± 1e-3) is marked
    invalid and its numbers are dropped rather than shown.
    """
    rows: list[dict[str, Any]] = []
    answers = resp.get("answers")
    for qid, a in (answers if isinstance(answers, dict) else {}).items():
        a = a if isinstance(a, dict) else {}
        kind = a.get("type")
        row: dict[str, Any] = {
            "id": qid,
            "type": kind,
            "probs": None,
            "top": None,
            "confidence": None,
            "expected": None,
            "invalid": False,
        }
        if kind == "noul":
            p = a.get("noul")
            probs = {"yes": p, "no": 1 - p} if isinstance(p, int | float) else None
        else:
            probs = a.get("probabilities")
        if not _valid_probs(probs):
            row["invalid"] = True
            rows.append(row)
            continue
        assert isinstance(probs, dict)
        row["probs"] = probs
        row["top"] = max(probs, key=lambda k: probs[k])
        if kind == "choice":
            row["top"] = a.get("choice", row["top"])
        if kind == "score":
            legend = a.get("legend") if isinstance(a.get("legend"), dict) else {}
            row["expected"] = a.get("score")
            row["legend"] = legend
        conf = a.get("confidence")
        row["confidence"] = conf if isinstance(conf, int | float) else None
        rows.append(row)
    return rows


def upstream_error(status: int, body: Any) -> DecisionError:
    """Map an OpenJev error response; both ``detail`` shapes occur (string or object)."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("error_type") or "error")
    elif isinstance(detail, str):
        message = detail
    else:
        message = f"upstream returned HTTP {status}"
    if status in (401, 403):
        # the admin's own key was refused: a configuration problem, not the caller's
        return DecisionError(502, "the decision sidecar refused the admin's key")
    if status in (429, 529):
        return DecisionError(429, f"the decision sidecar is busy: {message}")
    if 400 <= status < 500:
        return DecisionError(400, message)
    return DecisionError(502, f"the decision sidecar failed (HTTP {status})")


# --- client -------------------------------------------------------------------------


class DecisionClient:
    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self.process = SidecarProcess(
            lambda: openjev_spec(settings), transport, settings.probe_timeout_s * 3
        )

    @property
    def transport(self) -> httpx.MockTransport | None:
        return self._transport

    @property
    def root(self) -> Path:
        return self.settings.decision_root

    def argv(self, action: str) -> list[str]:
        return self.process.argv(action)

    def pid_from_file(self) -> int | None:
        return self.process.pid_from_file()

    def running(self) -> SidecarStatus:
        return self.process.status()

    def down_sync(self) -> bool:
        return self.process.down_sync()

    def ready_sync(self) -> bool:
        return self.process.ready_sync()

    def rss_bytes(self, pid: int | None) -> int | None:
        return self.process.rss_bytes(pid)

    def cli_status(self) -> dict[str, Any]:
        return self.process.cli_status()

    async def status(self) -> dict[str, Any]:
        proc = self.running()
        ready = proc.running and await asyncio.to_thread(self.ready_sync)
        return {
            "enabled": self.settings.decision_enabled,
            "running": proc.running,
            "pid": proc.pid,
            "ready": ready,
            "rss_bytes": self.rss_bytes(proc.pid),
            "listen": self.settings.decision_api_base,
            "checked_at": time.time(),
        }

    async def _upstream(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        key = self.settings.decision_api_key.get_secret_value()
        if not key:
            raise DecisionError(503, "DECISION_API_KEY is not configured")
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.decision_api_base,
                timeout=self.settings.decision_request_timeout_s,
                transport=self._transport,
            ) as http:
                return await http.request(
                    method, path, json=payload, headers={"Authorization": f"Bearer {key}"}
                )
        except httpx.TimeoutException:
            raise DecisionError(504, "the decision sidecar did not answer in time") from None
        except httpx.HTTPError:
            raise DecisionError(503, "the decision sidecar is not reachable") from None

    async def forward(self, payload: dict[str, Any]) -> httpx.Response:
        """One already-validated read, passed through unchanged (the LAN gateway)."""
        return await self._upstream("POST", "/v1/systemone", payload)

    async def models(self) -> httpx.Response:
        return await self._upstream("GET", "/v1/models")

    async def read(self, body: Any) -> dict[str, Any]:
        """Validate, forward with the admin's key, and normalise one read."""
        payload = validate_request(body)
        t0 = time.perf_counter()
        resp = await self.forward(payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code != 200:
            raise upstream_error(resp.status_code, data)
        if not isinstance(data, dict):
            raise DecisionError(502, "the decision sidecar returned a malformed response")
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return {
            "model": data.get("model"),
            "answers": normalize_answers(data),
            "raw": data.get("answers"),
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
            "latency_ms": round(elapsed_ms, 1),
            "server_timing": parse_server_timing(resp.headers.get("server-timing")),
        }
