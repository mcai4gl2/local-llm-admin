"""M10: suite validation and the built-in suites."""

import json

import pytest

from local_llm_admin.eval_suites import (
    SuiteError,
    builtin_suites,
    expand,
    messages_for,
    parse_jsonl,
    parse_suite,
)


def _suite(*cases, **extra):
    return {"id": "t", "version": 1, "title": "T", "cases": list(cases), **extra}


def _case(cid="a", **grader):
    return {"id": cid, "category": "x", "prompt": "p", "grader": grader or {"type": "exact", "answer": "y"}}


def test_valid_suite_and_hash_is_stable():
    s1 = parse_suite(_suite(_case()))
    s2 = parse_suite(json.loads(json.dumps(_suite(_case()))))
    assert s1.content_hash() == s2.content_hash() and len(s1.content_hash()) == 16


def test_hash_changes_with_content():
    a = parse_suite(_suite(_case())).content_hash()
    b = parse_suite(_suite(_case(type="exact", answer="z"))).content_hash()
    assert a != b


@pytest.mark.parametrize(
    "bad, msg",
    [
        (_suite(_case(), _case()), "duplicate case id a"),
        (_suite(), "no cases"),
        (_suite(_case(type="regex", pattern="(a+)+$")), "nested quantifier"),
        (_suite(_case(type="regex", pattern="(x*y){2,}")), "nested quantifier"),
        (_suite(_case(type="regex", pattern="a" * 301)), "longer than 300"),
        (_suite(_case(type="regex", pattern="(")), "invalid regex"),
        (_suite(_case(type="choice", answer="Z")), "answer"),
        (_suite({"id": "a", "category": "x", "grader": {"type": "exact", "answer": "y"}}), "prompt or messages"),
        (_suite({"id": "Bad Id", "category": "x", "prompt": "p", "grader": {"type": "exact", "answer": "y"}}), "id"),
        (_suite(_case(type="nope")), "type"),
    ],
)
def test_refusals(bad, msg):
    with pytest.raises(SuiteError, match=msg):
        parse_suite(bad)


def test_jsonl_import_reports_the_line():
    good = json.dumps(_case("a"))
    with pytest.raises(SuiteError, match="line 2"):
        parse_jsonl(good + "\n{not json\n", id="c", title="C", max_bytes=10_000, max_cases=10)


def test_jsonl_limits():
    line = json.dumps(_case("a"))
    with pytest.raises(SuiteError, match="larger than"):
        parse_jsonl(line, id="c", title="C", max_bytes=10, max_cases=10)
    lines = "\n".join(json.dumps(_case(f"c{i}")) for i in range(3))
    with pytest.raises(SuiteError, match="more than 2 cases"):
        parse_jsonl(lines, id="c", title="C", max_bytes=100_000, max_cases=2)


def test_messages_for_prompt_and_system():
    s = parse_suite(_suite({**_case(), "system": "be brief"}))
    assert messages_for(s.cases[0]) == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "p"}]


def test_builtins_parse_and_core_has_25_cases():
    suites = {s.id: s for s in builtin_suites()}
    assert set(suites) >= {"core-v1"}
    core = suites["core-v1"]
    assert len(core.cases) == 25
    assert {c.category for c in core.cases} == {"instruction", "extraction", "math", "reasoning", "tools"}


def test_jsonl_happy_path_and_blank_lines():
    text = json.dumps(_case("a")) + "\n\n" + json.dumps(_case("b")) + "\n"
    s = parse_jsonl(text, id="c", title="C", max_bytes=10_000, max_cases=10)
    assert [c.id for c in s.cases] == ["a", "b"] and s.version == 1


def test_jsonl_rejects_bad_case_and_non_object_and_empty():
    with pytest.raises(SuiteError, match="line 1"):
        parse_jsonl('{"id": "a"}', id="c", title="C", max_bytes=10_000, max_cases=10)
    with pytest.raises(SuiteError, match="line 1: not a JSON object"):
        parse_jsonl("[1]", id="c", title="C", max_bytes=10_000, max_cases=10)
    with pytest.raises(SuiteError, match="no cases"):
        parse_jsonl("\n\n", id="c", title="C", max_bytes=10_000, max_cases=10)


def test_messages_for_messages_case_and_non_object_suite():
    case = {"id": "m", "category": "x", "messages": [{"role": "user", "content": "hi"}],
            "grader": {"type": "exact", "answer": "y"}}
    s = parse_suite(_suite(case))
    assert messages_for(s.cases[0]) == [{"role": "user", "content": "hi"}]
    with pytest.raises(SuiteError, match="JSON object"):
        parse_suite([1])


def test_both_prompt_and_messages_refused_and_needle_only_suite():
    both = {**_case(), "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(SuiteError, match="prompt or messages"):
        parse_suite(_suite(both))
    s = parse_suite(_suite(needle={"lengths": [1000], "depths": [0.5], "seed": "s"}))
    assert s.needle is not None and s.cases == []


@pytest.mark.parametrize(
    "grader, loc",
    [
        ({"type": "json_fields", "fields": {}}, "cases.0.grader.json_fields.fields"),
        ({"type": "contains_all", "terms": []}, "cases.0.grader.contains_all.terms"),
        ({"type": "contains_all", "terms": ["a", ""]}, "cases.0.grader.contains_all.terms.1"),
        ({"type": "number", "answer": float("nan")}, "cases.0.grader.number.answer"),
        ({"type": "number", "answer": float("inf")}, "cases.0.grader.number.answer"),
        ({"type": "number", "answer": 1, "tolerance": float("inf")}, "cases.0.grader.number.tolerance"),
        ({"type": "number", "answer": 1, "tolerance": -1}, "cases.0.grader.number.tolerance"),
    ],
)
def test_degenerate_graders_rejected(grader, loc):
    with pytest.raises(SuiteError) as ei:
        parse_suite(_suite(_case(**grader)))
    assert ei.value.message.startswith(loc + ": ")


@pytest.mark.parametrize(
    "needle, loc",
    [
        ({"lengths": [], "depths": [0.5]}, "needle.lengths"),
        ({"lengths": [100], "depths": []}, "needle.depths"),
        ({"lengths": [0], "depths": [0.5]}, "needle.lengths.0"),
        ({"lengths": [100], "depths": [1.5]}, "needle.depths.0"),
        ({"lengths": [100], "depths": [-0.1]}, "needle.depths.0"),
    ],
)
def test_degenerate_needle_rejected(needle, loc):
    with pytest.raises(SuiteError) as ei:
        parse_suite(_suite(needle={**needle, "seed": "s"}))
    assert ei.value.message.startswith(loc + ": ")


def test_needle_boundary_depths_accepted():
    parse_suite(_suite(needle={"lengths": [1], "depths": [0, 1], "seed": "s"}))


def test_needle_expansion_respects_context():
    suite = {s.id: s for s in builtin_suites()}["needle-v1"]
    small = expand(suite, 40_000)
    assert sorted({c.category for c in small}) == ["needle-32768", "needle-8192"]
    assert len(expand(suite, 262_144)) == 12
    assert {c.category for c in expand(suite, None)} == {"needle-8192"}


def test_needle_is_deterministic_and_placed():
    suite = {s.id: s for s in builtin_suites()}["needle-v1"]
    a = {c.id: c for c in expand(suite, 262_144)}
    b = {c.id: c for c in expand(suite, 262_144)}
    assert a["needle-8192-10"].prompt == b["needle-8192-10"].prompt
    p = a["needle-8192-90"].prompt
    code = a["needle-8192-90"].grader.terms[0]
    assert p.index(code) > 0.8 * len(p)
    assert a["needle-8192-10"].prompt.index(a["needle-8192-10"].grader.terms[0]) < 0.2 * len(p)
    assert code.startswith("K") and len(code) == 7 and a["needle-8192-90"].max_tokens == 32
    assert p.endswith("Reply with the code only.")


def test_expand_without_a_needle_returns_the_cases():
    s = parse_suite(_suite(_case()))
    assert [c.id for c in expand(s, None)] == ["a"]


@pytest.mark.parametrize(
    "pattern, msg",
    [
        ("((a+))+$", "nested quantifier"),  # the extra group hid it from the old text check
        ("(a|aa)+$", "repeats an alternation"),
        ("(?:ab|cd)*x", "repeats an alternation"),
        ("(a+){1,999}", "nested quantifier"),
        ("(a+){101}", "nested quantifier"),
        ("((a+){2}){3}", "nested quantifier"),
        ("(a+){4}$", "nested quantifier"),  # bounded, but "a"*200 splits too many ways
        ("(a+b?){4}$", "nested quantifier"),
        ("(?i)(a+A){3}", "nested quantifier"),
        ("(\\w+\\d){3}", "nested quantifier"),
        ("((?=a)a+){3}", "nested quantifier"),
        ("(.x+){3}", "nested quantifier"),
        ("(?=(a+)+)b", "nested quantifier"),
    ],
)
def test_regex_backtracking_shapes_are_refused(pattern, msg):
    with pytest.raises(SuiteError, match=msg):
        parse_suite(_suite(_case(type="regex", pattern=pattern)))


@pytest.mark.parametrize(
    "pattern",
    [
        r"^\s*[^\s.]+(\s+[^\s.]+){4}\.\s*$",  # core-v1: a bounded repeat of plain repeats
        r"(ab)+c",
        r"(a+b+){100}",
        r"(\b[a-z]+[ ,]){3}",
        r"(x\d*){3}",
        r"([^,]+,){3}",
        r"^(?=.*\bred\b)\s*[a-z]+\s*$",
        r"(?:a|b)+",  # the parser folds a one-character alternation into a set
    ],
)
def test_regex_safe_shapes_are_accepted(pattern):
    parse_suite(_suite(_case(type="regex", pattern=pattern)))


def test_core_v1_regexes_still_load():
    core = {s.id: s for s in builtin_suites()}["core-v1"]
    assert sum(c.grader.type == "regex" for c in core.cases) == 3


def test_regex_overlap_helpers_are_conservative():
    from local_llm_admin.eval_suites import _PROBE, _ambiguous, _category, _chars

    assert _PROBE <= _chars("IN", [("UNEXPECTED", None)])
    assert _category("CATEGORY_UNKNOWN") == _PROBE
    assert _ambiguous([(1, 1, frozenset({97})), (0, 5, frozenset())]) is False
