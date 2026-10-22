"""M10 judge: the rubric prompt, verdict parsing, the configured judge and one judged
request. The judge's API key goes only into the Authorization header of the client the
runner builds for it; it is never logged or stored."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .chat import ACTIVE_MODEL_ID
from .config import Settings
from .eval_graders import json_object
from .eval_suites import EvalCase, Judge
from .mlx import validate_name

log = logging.getLogger("local_llm_admin")

JUDGE_SYSTEM = (
    "You grade one answer against a rubric. Reply with a JSON object only: "
    '{"score": <integer>, "reason": "<one sentence>"}. Do not add any other text.'
)
MAX_TOKENS = 300
ATTEMPTS = 2  # one retry on an unusable verdict
_THINK = re.compile(r"<think>.*?</think>", re.S)
_CLOSE = re.compile(r"<\s*/\s*(task|rubric|reference|answer)\s*>", re.I)


@dataclass(frozen=True)
class JudgeTarget:
    kind: Literal["external", "mlx"]
    label: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    mlx_name: str | None


def judge_target(settings: Settings) -> JudgeTarget | None:
    """The configured judge; ``None`` for ``none``. ``ValueError`` for a malformed value."""
    value = settings.eval_judge.strip()
    if value == "none":
        return None
    if value == "external":
        base = settings.eval_judge_base_url.strip().rstrip("/")
        model = settings.eval_judge_model.strip()
        if not base or not model:
            raise ValueError("external judge needs EVAL_JUDGE_BASE_URL and EVAL_JUDGE_MODEL")
        if not base.startswith(("http://", "https://")):
            raise ValueError("EVAL_JUDGE_BASE_URL must be an http or https URL")
        key = settings.eval_judge_api_key.get_secret_value()
        return JudgeTarget("external", f"external:{model}", base, model, key, None)
    if value.startswith("mlx:"):
        name = validate_name(value[4:])
        return JudgeTarget("mlx", f"mlx:{name}", settings.mlx_api_base, ACTIVE_MODEL_ID, "", name)
    raise ValueError(f"EVAL_JUDGE must be none, external or mlx:<name>, not {value[:40]!r}")


def _quote(text: str) -> str:
    """Escape closing tags (any case or spacing) so quoted text cannot end its block."""
    return _CLOSE.sub(lambda m: f"&lt;/{m.group(1)}&gt;", text)


def build_judge_messages(case: EvalCase, answer: str, spec: Judge) -> list[dict[str, str]]:
    if case.messages is not None:
        task = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in case.messages
        )
    else:
        task = case.prompt or ""
    if case.system:
        task = f"system: {case.system}\n\n{task}"
    scale = f"Score 1 to {spec.scale}" + (": 1 = fails, 2 = passes" if spec.scale == 2 else "")
    parts = [
        f"<task>\n{_quote(task)}\n</task>",
        f"<rubric>\n{scale}.\n{_quote(spec.rubric)}\n</rubric>",
    ]
    if spec.reference is not None:
        parts.append(f"<reference>\n{_quote(spec.reference)}\n</reference>")
    parts.append(f"<answer>\n{_quote(answer)}\n</answer>")
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_verdict(text: str, scale: int) -> tuple[float, str] | None:
    """``((score - 1) / (scale - 1), reason)`` from ``{"score": int, "reason": str}``,
    fenced or bare; ``None`` for anything else."""
    obj = json_object(_THINK.sub("", text or ""))
    if not isinstance(obj, dict):
        return None
    score, reason = obj.get("score"), obj.get("reason", "")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= scale:
        return None
    if not isinstance(reason, str):
        return None
    return (score - 1) / (scale - 1), reason


def _payload(target: JudgeTarget, messages: list[dict[str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": target.model,
        "messages": messages,
        "stream": False,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
    }
    if target.kind == "mlx":  # an external endpoint may refuse unknown fields
        payload["enable_thinking"] = False
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


async def _attempt(
    http: httpx.AsyncClient, target: JudgeTarget, payload: dict[str, Any], deadline_s: float
) -> str | None:
    path = "chat/completions" if target.base_url.endswith("/v1") else "/v1/chat/completions"
    try:
        async with asyncio.timeout(deadline_s):
            resp = await http.post(
                path, json=payload, timeout=httpx.Timeout(deadline_s, connect=5.0)
            )
    except (TimeoutError, httpx.HTTPError) as exc:
        log.warning("judge %s: request failed (%s)", target.label, type(exc).__name__)
        return None
    if resp.status_code != 200:
        log.warning("judge %s: HTTP %s", target.label, resp.status_code)
        return None
    try:
        msg = resp.json()["choices"][0]["message"]
        content = msg.get("content")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None
    return content if isinstance(content, str) else None


async def judge_one(
    http: httpx.AsyncClient,
    target: JudgeTarget,
    messages: list[dict[str, str]],
    *,
    deadline_s: float,
    until: float,
    scale: int,
) -> tuple[float, str] | None:
    """One verdict on ``scale``, retried once when the judge answers prose, nothing or
    an error. Each attempt gets ``deadline_s`` but never runs past ``until`` (a
    ``time.monotonic()`` instant, the run deadline); no retry once that has passed."""
    payload = _payload(target, messages)
    for _ in range(ATTEMPTS):
        left = min(deadline_s, until - time.monotonic())
        if left <= 0:
            return None
        text = await _attempt(http, target, payload, left)
        verdict = parse_verdict(text or "", scale)
        if verdict is not None:
            return verdict
    return None
