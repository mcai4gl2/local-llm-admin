"""M10 deterministic graders. Pure: the answer text (reasoning already removed) and the
tool calls go in, a Grade comes out. Values compare loosely where models vary harmlessly:
strings by casefold and strip, numbers by value whether sent as number or numeric string."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import regex

from .eval_suites import (
    Choice,
    ContainsAll,
    Exact,
    GraderSpec,
    JsonFields,
    Judge,
    Number,
    Regex,
    ToolCall,
)

MAX_GRADED_CHARS = 20_000
REGEX_TIMEOUT_S = 1.0  # per match; the regex package matches without the GIL
STOPPED_THINKING = "no answer (stopped while thinking)"
_THINK = re.compile(r"<think>.*?</think>", re.S)
_OPEN_THINK = re.compile(r"<think>(?!.*</think>)", re.S)
_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
# a leading letter counts only when it stands alone: "(C)", "C", "C." but not "I think" or "A good"
_CHOICE_ALONE = re.compile(r"^\s*(?:\(([A-J])\)|([A-J])(?:[.):,]|[ \t]*$))", re.M)
# "answer"/"choice" (any case) then the letter: upper case ("I" not as the pronoun: "I believe",
# "I'm"), or lower case only when it stands alone, i.e. before the end of a line or punctuation
# ("Answer: c." but not "is a trick")
_CHOICE_SAID = re.compile(
    r"(?i:\b(?:answer|choice)\b)[ \t]*(?:(?i:is)\b|:)?[ \t]*\(?"
    r"(?:([A-HJ]|I(?!\s+[a-z]|'))|([a-j])(?=[ \t]*(?:[.)!,;]|$)))\b",
    re.M,
)
# the fallback when no explicit match: "is"/"it's" then a standalone upper-case letter
# ("it is B." but not "This is A common mistake" or "A-grade")
_CHOICE_IS = re.compile(r"(?i:\b(?:is|it's)\b)[ \t]*\(?([A-J])\)?(?=[ \t]*(?:[.!,;:?]|$))", re.M)
_FENCE = re.compile(r"`{3}(?:json)?\s*(.*?)`{3}", re.S)


@dataclass(frozen=True)
class Grade:
    score: float
    passed: bool
    detail: str


def final_answer(
    content: str | None, finish_reason: str | None, reasoning_chars: int, *, thinking: bool = False
) -> tuple[str | None, str | None]:
    """The answer to grade, or None and why. Reasoning is dropped: ``<think>`` blocks, and
    everything up to a closing tag that has no opening one (templates that put ``<think>``
    in the prompt). With thinking on, a reply cut off by the token limit with no closing
    tag is reasoning that never finished, unless the server split the reasoning out."""
    text = content or ""
    if _OPEN_THINK.search(text) or (not text.strip() and finish_reason == "length" and reasoning_chars):
        return None, STOPPED_THINKING
    if thinking and finish_reason == "length" and "</think>" not in text and not reasoning_chars:
        return None, STOPPED_THINKING
    text = _THINK.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = text.strip()
    if not text:
        return None, "empty answer"
    return text, None


def _norm(s: str) -> str:
    return " ".join(s.casefold().split()).strip(" .!?\"'")


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)):
        try:
            return abs(float(str(actual).replace(",", "")) - float(expected)) <= 1e-6
        except ValueError:
            return False
    if isinstance(expected, str):
        return isinstance(actual, str) and _norm(actual) == _norm(expected)
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and all(k in actual and _same(v, actual[k]) for k, v in expected.items())
            and len(actual) == len(expected)
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_same(a, b) for a, b in zip(expected, actual, strict=True))
        )
    return bool(expected == actual)


def json_object(text: str) -> Any:
    for candidate in [m.group(1) for m in _FENCE.finditer(text)] + [text]:
        start = candidate.find("{")
        while start != -1:
            try:
                return json.JSONDecoder().raw_decode(candidate[start:])[0]
            except ValueError:
                start = candidate.find("{", start + 1)
    return None


def _path(obj: Any, dotted: str) -> tuple[bool, Any]:
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return False, None
        obj = obj[part]
    return True, obj


def _field_ok(obj: Any, dotted: str, expected: Any) -> bool:
    found, value = _path(obj, dotted)
    return found and _same(expected, value)


def _ok(passed: bool, detail: str) -> Grade:
    return Grade(1.0 if passed else 0.0, passed, detail)


def grade(spec: GraderSpec, content: str | None, tool_calls: list[dict[str, Any]] | None) -> Grade | None:
    text = (content or "")[:MAX_GRADED_CHARS]
    if isinstance(spec, Judge):
        return None
    if isinstance(spec, ToolCall):
        return _tool(spec, tool_calls or [])
    if isinstance(spec, Exact):
        seen = _norm(text) if spec.normalize else text
        want = _norm(spec.answer) if spec.normalize else spec.answer
        return _ok(seen == want, f"expected {spec.answer!r}")
    if isinstance(spec, Regex):
        if len(content or "") > MAX_GRADED_CHARS:
            return _ok(False, "answer longer than the regex limit")
        flags = regex.V0 | (regex.I if spec.ignore_case else 0)
        try:
            found = regex.search(
                spec.pattern, text, flags, timeout=REGEX_TIMEOUT_S, concurrent=True
            )
        except TimeoutError:
            return _ok(False, f"regex timed out after {REGEX_TIMEOUT_S:g} s")
        return _ok(found is not None, f"pattern {spec.pattern!r}")
    if isinstance(spec, ContainsAll):
        hay = text if spec.case_sensitive else text.casefold()
        missing = [t for t in spec.terms if (t if spec.case_sensitive else t.casefold()) not in hay]
        return _ok(not missing, f"missing {missing}" if missing else "all terms present")
    if isinstance(spec, Choice):
        m = _CHOICE_ALONE.match(text)
        said = [(up or low).upper() for up, low in _CHOICE_SAID.findall(text)] or _CHOICE_IS.findall(text)
        got = said[-1] if said else ((m.group(1) or m.group(2)) if m else None)
        return _ok(got == spec.answer, f"chose {got or 'nothing'}, expected {spec.answer}")
    if isinstance(spec, Number):
        nums = _NUM.findall(text)
        if not nums:
            return _ok(False, "no number found")
        value = float(nums[-1].replace(",", ""))
        return _ok(abs(value - spec.answer) <= spec.tolerance, f"got {value:g}, expected {spec.answer:g}")
    if isinstance(spec, JsonFields):
        obj = json_object(text)
        if not isinstance(obj, dict):
            return Grade(0.0, False, "no JSON object found")
        wrong = [k for k, v in spec.fields.items() if not _field_ok(obj, k, v)]
        score = 1 - len(wrong) / len(spec.fields)
        return Grade(score, not wrong, f"wrong or missing: {wrong}" if wrong else "all fields match")
    raise AssertionError(f"unknown grader {spec!r}")


def _tool(spec: ToolCall, calls: list[dict[str, Any]]) -> Grade:
    if spec.name is None:
        if not calls:
            return _ok(True, "no tool call")
        return _ok(False, f"unexpected call {(calls[0].get('function') or {}).get('name')}")
    if not calls:
        return _ok(False, "no tool call")
    fn = calls[0].get("function") or {}
    if fn.get("name") != spec.name:
        return _ok(False, f"called {fn.get('name')!r}, expected {spec.name!r}")
    raw = fn.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return _ok(False, "arguments are not JSON")
    if not isinstance(args, dict):
        return _ok(False, "arguments are not an object")
    if spec.partial:
        wrong = [k for k, v in spec.arguments.items() if k not in args or not _same(v, args[k])]
    else:
        wrong = [] if _same(spec.arguments, args) else sorted(set(spec.arguments) ^ set(args)) or ["values"]
    return _ok(not wrong, f"argument mismatch: {wrong}" if wrong else "call matches")


def grade_response(
    spec: GraderSpec,
    content: str | None,
    finish_reason: str | None,
    reasoning_chars: int,
    tool_calls: list[dict[str, Any]] | None,
    *,
    thinking: bool,
) -> dict[str, Any]:
    """The result-row fields for one stored or fresh response: the runner grades with it,
    and a regrade re-applies it to stored responses (with the run's ``thinking``).
    ``needs_judge`` for a judge case."""
    answer, reason = final_answer(content, finish_reason, reasoning_chars, thinking=thinking)
    cut = reason == STOPPED_THINKING
    # a tool case can answer with calls and no text; a cut-off thought never counts
    if answer is None and (not isinstance(spec, ToolCall) or (cut and not tool_calls)):
        return {"score": 0.0, "passed": 0, "grade_state": "graded", "grade_detail": reason}
    g = grade(spec, answer, tool_calls)
    if g is None:
        return {"grade_state": "needs_judge"}
    return {"score": g.score, "passed": int(g.passed), "grade_state": "graded",
            "grade_detail": g.detail}  # fmt: skip
