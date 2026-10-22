"""M10: eval suite model, validation and the built-in suites."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterator
from importlib import resources
from typing import Annotated, Any, Literal

import regex

try:  # Python 3.11 moved the regex parser into the re package (no public stubs either way)
    _sre_parse: Any = importlib.import_module("re._parser")
except ImportError:  # pragma: no cover - older interpreters
    _sre_parse = importlib.import_module("sre_parse")

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .quality import filler


class SuiteError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Exact(_Spec):
    type: Literal["exact"]
    answer: str
    normalize: bool = True


class Regex(_Spec):
    type: Literal["regex"]
    pattern: str
    ignore_case: bool = False


class ContainsAll(_Spec):
    type: Literal["contains_all"]
    terms: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1)]
    case_sensitive: bool = False


class Choice(_Spec):
    type: Literal["choice"]
    answer: str = Field(pattern=r"^[A-J]$")


class Number(_Spec):
    type: Literal["number"]
    answer: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=1e-6, ge=0, allow_inf_nan=False)


class JsonFields(_Spec):
    type: Literal["json_fields"]
    fields: dict[str, Any] = Field(min_length=1)


class ToolCall(_Spec):
    type: Literal["tool_call"]
    name: str | None
    arguments: dict[str, Any] = {}
    partial: bool = False


class Judge(_Spec):
    type: Literal["judge"]
    rubric: str
    scale: Literal[2, 5] = 5
    reference: str | None = None


GraderSpec = Annotated[
    Exact | Regex | ContainsAll | Choice | Number | JsonFields | ToolCall | Judge,
    Field(discriminator="type"),
]


class EvalCase(_Spec):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    category: str
    prompt: str | None = None
    messages: list[dict[str, str]] | None = None
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    max_tokens: int = 512
    grader: GraderSpec

    @model_validator(mode="after")
    def _one_input(self) -> EvalCase:
        if (self.prompt is None) == (self.messages is None):
            raise ValueError("give exactly one of prompt or messages")
        return self


class NeedleSpec(_Spec):
    lengths: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    depths: list[Annotated[float, Field(ge=0, le=1)]] = Field(min_length=1)
    seed: str


class Suite(_Spec):
    id: str
    version: int
    title: str
    description: str = ""
    cases: list[EvalCase] = []
    needle: NeedleSpec | None = None

    def content_hash(self) -> str:
        data = self.model_dump(exclude={"title", "description"})
        canon = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()[:16]


# Regex safety (custom suites are graded in the admin process). On the parsed pattern,
# for every repeat (*, +, ?, {m,}, {m,n}, lazy or possessive) whose body holds another
# repeat or an alternation at any depth:
# - an alternation inside a repeat is refused ((a|aa)+ backtracks exponentially);
# - a repeat inside an unbounded repeat, or one bounded above 100, is refused ((a+)+,
#   ((a+))+, (a+){1,999});
# - a bounded repeat (max <= 100) may hold only single characters and repeats of single
#   characters, and the body must split one way only: each inner repeat must share no
#   character with what can follow it, wrapping round to the body's start (core-v1's
#   (\s+[^\s.]+){4} passes; (a+){4}, (a+b?){4} and ((a+){2}){3} do not).
# One-character alternations the parser folds into a set ((?:a|b)+) are not alternations.
# Polynomial backtracking outside repeated groups (a*a*a*$) is not caught; the Regex
# grader matches with the `regex` package, which has a per-match timeout and releases the
# GIL (stdlib re holds it, so a worker thread would not keep the event loop responsive).
_MAX_BOUNDED_REPEAT = 100
_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})
_PROBE = frozenset(range(0x3000))  # characters checked for overlap (beyond: assumed shared)
_CATEGORY = {"CATEGORY_DIGIT": r"\d", "CATEGORY_NOT_DIGIT": r"\D", "CATEGORY_SPACE": r"\s",
             "CATEGORY_NOT_SPACE": r"\S", "CATEGORY_WORD": r"\w", "CATEGORY_NOT_WORD": r"\W"}  # fmt: skip


def _subpatterns(av: Any) -> Iterator[Any]:
    if isinstance(av, _sre_parse.SubPattern):
        yield av
    elif isinstance(av, (tuple, list)):
        for item in av:
            yield from _subpatterns(item)


def _walk(pattern: Any) -> Iterator[tuple[str, Any]]:
    for op, av in pattern:
        yield str(op), av
        for child in _subpatterns(av):
            yield from _walk(child)


def _category(name: str) -> frozenset[int]:
    rx = re.compile(_CATEGORY.get(name, "."), re.S)  # an unknown category: everything
    return frozenset(c for c in _PROBE if rx.match(chr(c)))


def _chars(op: str, av: Any) -> frozenset[int] | None:
    """The characters one single-character item matches, either case (None: not one)."""
    if op == "LITERAL":
        found = frozenset({av})
    elif op == "NOT_LITERAL":
        found = _PROBE - {av}
    elif op == "ANY":
        found = _PROBE
    elif op == "IN":
        found, negate = frozenset(), False
        for o, a in av:
            o = str(o)
            if o == "NEGATE":
                negate = True
            elif o == "LITERAL":
                found |= {a}
            elif o == "RANGE":
                found |= frozenset(range(a[0], a[1] + 1))
            elif o == "CATEGORY":
                found |= _category(str(a))
            else:  # nothing else is parsed into a set; if it were, assume it overlaps
                found = _PROBE
        found = _PROBE - found if negate else found
    else:
        return None
    return found | {ord(ch) for c in found for ch in (chr(c).lower(), chr(c).upper()) if len(ch) == 1}


def _items(pattern: Any) -> list[tuple[int, int, frozenset[int]]] | None:
    """A repeated body as (min, max, characters) per item, or None when it holds anything
    but single characters and repeats of one (groups are flattened, anchors skipped)."""
    out: list[tuple[int, int, frozenset[int]]] = []
    for op, av in pattern:
        op = str(op)
        if op == "AT":
            continue
        if op == "SUBPATTERN":
            inner = _items(av[3])
            if inner is None:
                return None
            out += inner
            continue
        if op in _REPEATS:
            lo, hi, body = av
            atoms = list(body)
            chars = _chars(str(atoms[0][0]), atoms[0][1]) if len(atoms) == 1 else None
            if chars is None:
                return None
            out.append((lo, hi, chars))
            continue
        chars = _chars(op, av)
        if chars is None:
            return None
        out.append((1, 1, chars))
    return out


def _ambiguous(items: list[tuple[int, int, frozenset[int]]]) -> bool:
    n = len(items)
    for i, (lo, hi, chars) in enumerate(items):
        if lo == hi:
            continue
        for j in range(i + 1, i + n + 1):  # what can follow, wrapping into the next round
            nlo, _, nchars = items[j % n]
            if chars & nchars:
                return True
            if nlo > 0:
                break
    return False


def _repeat_problem(pattern: Any) -> str | None:
    for op, av in _walk(pattern):
        if op not in _REPEATS:
            continue
        _, hi, body = av
        inner = list(_walk(body))
        if any(o == "BRANCH" for o, _ in inner):
            return "repeats an alternation"
        if not any(o in _REPEATS for o, _ in inner):
            continue
        if hi == _sre_parse.MAXREPEAT or hi > _MAX_BOUNDED_REPEAT:
            return "nested quantifier"
        items = _items(body)
        if items is None or _ambiguous(items):
            return "nested quantifier"
    return None


def _first(exc: ValidationError) -> str:
    e = exc.errors()[0]
    where = ".".join(str(p) for p in e["loc"]) or "body"
    return f"{where}: {e['msg']}"


def _check_regex(pattern: str) -> None:
    if len(pattern) > 300:
        raise SuiteError("regex pattern is longer than 300 characters")
    try:
        regex.compile(pattern, regex.V0)
        parsed = _sre_parse.parse(pattern)
    except (re.error, regex.error) as exc:
        raise SuiteError(f"invalid regex: {exc}") from None
    problem = _repeat_problem(parsed)
    if problem is not None:
        raise SuiteError(f"regex {problem}: {pattern[:40]}")


def parse_suite(data: Any) -> Suite:
    if not isinstance(data, dict):
        raise SuiteError("suite must be a JSON object")
    try:
        suite = Suite.model_validate(data)
    except ValidationError as exc:
        raise SuiteError(_first(exc)) from None
    if not suite.cases and suite.needle is None:
        raise SuiteError("suite has no cases")
    seen: set[str] = set()
    for case in suite.cases:
        if case.id in seen:
            raise SuiteError(f"duplicate case id {case.id}")
        seen.add(case.id)
        if isinstance(case.grader, Regex):
            _check_regex(case.grader.pattern)
    return suite


def parse_jsonl(text: str, *, id: str, title: str, max_bytes: int, max_cases: int) -> Suite:
    if len(text.encode()) > max_bytes:
        raise SuiteError(f"file is larger than {max_bytes} bytes")
    cases: list[dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SuiteError(f"line {n}: invalid JSON ({exc.msg})") from None
        if not isinstance(obj, dict):
            raise SuiteError(f"line {n}: not a JSON object")
        try:
            EvalCase.model_validate(obj)
        except ValidationError as exc:
            raise SuiteError(f"line {n}: {_first(exc)}") from None
        cases.append(obj)
        if len(cases) > max_cases:
            raise SuiteError(f"suite has more than {max_cases} cases")
    return parse_suite({"id": id, "version": 1, "title": title, "cases": cases})


def builtin_suites() -> list[Suite]:
    root = resources.files("local_llm_admin") / "evals"
    files = sorted((p for p in root.iterdir() if p.name.endswith(".json")), key=lambda p: p.name)
    return [parse_suite(json.loads(p.read_text(encoding="utf-8"))) for p in files]


NEEDLE_SENTENCE = "The access code for the blue door is {code}."
NEEDLE_QUESTION = "What is the access code for the blue door? Reply with the code only."
NEEDLE_MARGIN = 1024  # room for the question and the reply
NEEDLE_UNKNOWN_CONTEXT_MAX = 8192
_PARAGRAPH_WORDS = 60


def _needle_prompt(length: int, seed: str, code: str, depth: float) -> str:
    """``filler`` is sized in tokens, like ``length``. The needle goes in as its own
    paragraph at the boundary nearest ``depth``."""
    words = filler(length, seed=seed).split()
    paras = [" ".join(words[i : i + _PARAGRAPH_WORDS]) for i in range(0, len(words), _PARAGRAPH_WORDS)]
    paras.insert(round(depth * len(paras)), NEEDLE_SENTENCE.format(code=code))
    return "\n\n".join(paras) + f"\n\n{NEEDLE_QUESTION}"


# The suite's content_hash covers the needle spec, not the generated text: changing
# quality.filler (or this layout) must bump the version of needle-v1.
def expand(suite: Suite, context_tokens: int | None) -> list[EvalCase]:
    """The suite's cases plus, for a needle suite, one case per (length, depth) that fits
    ``length + 1024 <= context_tokens``. An unknown context (None) means lengths up to
    8192 only. Ids, codes and text are deterministic, so runs line up for compare/regrade."""
    cases = list(suite.cases)
    spec = suite.needle
    if spec is None:
        return cases
    for length in spec.lengths:
        if context_tokens is None:
            if length > NEEDLE_UNKNOWN_CONTEXT_MAX:
                continue
        elif length + NEEDLE_MARGIN > context_tokens:
            continue
        for depth in spec.depths:
            digest = hashlib.sha256(f"{spec.seed}-{length}-{depth}".encode()).hexdigest()
            code = "K" + digest[:6].upper()
            cases.append(
                EvalCase(
                    id=f"needle-{length}-{round(depth * 100)}",
                    category=f"needle-{length}",
                    prompt=_needle_prompt(length, f"{spec.seed}-{length}", code, depth),
                    max_tokens=32,
                    grader=ContainsAll(type="contains_all", terms=[code], case_sensitive=True),
                )
            )
    return cases


def messages_for(case: EvalCase) -> list[dict[str, str]]:
    out = [{"role": "system", "content": case.system}] if case.system else []
    if case.messages is not None:
        return out + [dict(m) for m in case.messages]
    return [*out, {"role": "user", "content": case.prompt or ""}]
