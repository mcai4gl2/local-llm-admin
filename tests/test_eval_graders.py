"""M10: deterministic graders."""

import pytest

from local_llm_admin.eval_graders import final_answer, grade
from local_llm_admin.eval_suites import (
    Choice,
    ContainsAll,
    Exact,
    JsonFields,
    Judge,
    Number,
    Regex,
    ToolCall,
)


def test_final_answer_strips_think_blocks():
    assert final_answer("<think>hmm 41?</think>\n42", "stop", 0) == ("42", None)


def test_final_answer_stopped_while_thinking():
    assert final_answer("<think>still going", "length", 0) == (
        None,
        "no answer (stopped while thinking)",
    )
    assert final_answer("", "length", 900) == (None, "no answer (stopped while thinking)")


def test_final_answer_empty():
    assert final_answer("", "stop", 0) == (None, "empty answer")


@pytest.mark.parametrize(
    "text, ok", [("ananab", True), (" Ananab. ", True), ("ananab!\nDone", False)]
)
def test_exact_normalised(text, ok):
    assert grade(Exact(type="exact", answer="ananab"), text, None).passed is ok


def test_regex_and_length_cap():
    spec = Regex(type="regex", pattern=r"^\s*READY\s*$")
    assert grade(spec, "READY", None).passed
    assert not grade(spec, "READY" + " " * 30_000, None).passed  # beyond the cap: not matched


@pytest.mark.parametrize(
    "text, ok",
    [
        ("C", True),
        ("(C)", True),
        ("C) they weigh the same", True),
        ("The answer is C.", True),
        ("Answer: c", True),
        ("I think B, no wait, the answer is C", True),
        ("B", False),
        ("none", False),
    ],
)
def test_choice(text, ok):
    assert grade(Choice(type="choice", answer="C"), text, None).passed is ok


@pytest.mark.parametrize(
    "text, ok",
    [
        ("72", True),
        ("Speed = 180/2.5\n72 km/h", True),
        ("72.0", True),
        ("1,275", False),
        ("about 70", False),
    ],
)
def test_number_takes_the_last_number(text, ok):
    assert grade(Number(type="number", answer=72), text, None).passed is ok


def test_number_with_thousands_separator():
    assert grade(Number(type="number", answer=1275), "Total:\n1,275", None).passed


def test_number_without_any_number():
    assert not grade(Number(type="number", answer=1), "none", None).passed


def test_json_fields_fenced_nested_and_typed():
    spec = JsonFields(type="json_fields", fields={"from.code": "LIS", "total": 43, "name": "Tokyo"})
    text = 'Here:\n```json\n{"from": {"code": "lis"}, "total": "43.00", "name": " Tokyo "}\n```'
    g = grade(spec, text, None)
    assert g.passed and g.score == 1.0


def test_json_fields_partial_credit():
    spec = JsonFields(type="json_fields", fields={"a": 1, "b": 2})
    g = grade(spec, '{"a": 1, "b": 3}', None)
    assert not g.passed and g.score == 0.5 and "b" in g.detail


def test_json_fields_no_json():
    assert grade(JsonFields(type="json_fields", fields={"a": 1}), "sorry", None).score == 0.0


def test_contains_all():
    spec = ContainsAll(type="contains_all", terms=["K7-1234"])
    assert grade(spec, "The code is k7-1234.", None).passed


def _call(name, args):
    return [{"type": "function", "function": {"name": name, "arguments": args}}]


def test_tool_call_exact_args_and_case():
    spec = ToolCall(
        type="tool_call",
        name="convert_currency",
        arguments={"amount": 250, "from_currency": "USD", "to_currency": "EUR"},
    )
    assert grade(
        spec,
        None,
        _call(
            "convert_currency", '{"amount": 250.0, "from_currency": "usd", "to_currency": "EUR"}'
        ),
    ).passed
    assert not grade(
        spec,
        None,
        _call("convert_currency", '{"amount": 25, "from_currency": "USD", "to_currency": "EUR"}'),
    ).passed


def test_tool_call_bad_json_and_wrong_name():
    spec = ToolCall(type="tool_call", name="get_weather", arguments={"city": "Paris"}, partial=True)
    assert not grade(spec, None, _call("get_weather", "{city: Paris")).passed
    assert not grade(spec, None, _call("create_event", '{"city": "Paris"}')).passed
    assert grade(
        spec, None, _call("get_weather", '{"city": "Paris", "unit": "celsius"}')
    ).passed  # partial


def test_no_tool_call_expected():
    spec = ToolCall(type="tool_call", name=None)
    assert grade(spec, "4", None).passed and grade(spec, "4", []).passed
    assert not grade(spec, None, _call("get_weather", "{}")).passed


def test_tool_call_missing_and_non_object_arguments():
    spec = ToolCall(type="tool_call", name="get_weather", arguments={"city": "Paris"})
    assert not grade(spec, "hi", None).passed
    assert not grade(spec, None, _call("get_weather", "[1]")).passed
    assert grade(
        spec, None, _call("get_weather", {"city": "paris"})
    ).passed  # arguments already a dict
    assert not grade(
        spec, None, _call("get_weather", '{"city": "Paris", "x": 1}')
    ).passed  # extra key, not partial
    assert not grade(spec, None, _call("get_weather", '{"city": "Rome"}')).passed


def test_same_value_kinds():
    spec = JsonFields(
        type="json_fields", fields={"ok": True, "n": 1, "l": [1, "a"], "d": {"x": 1}, "z": None}
    )
    good = '{"ok": true, "n": "1", "l": [1, "A"], "d": {"x": 1}, "z": null}'
    assert grade(spec, good, None).passed
    bad = '{"ok": 1, "n": "x", "l": [1], "d": {"x": 1, "y": 2}, "z": 0}'
    assert grade(spec, bad, None).score == 0.0
    assert not grade(JsonFields(type="json_fields", fields={"s": "a"}), '{"s": 3}', None).passed
    assert not grade(JsonFields(type="json_fields", fields={"a.b": 1}), '{"a": 1}', None).passed


def test_json_object_skips_stray_braces():
    from local_llm_admin.eval_graders import json_object

    assert json_object('x {y} ```json\n{"a": 1}\n```') == {"a": 1} and json_object("none") is None
    spec = JsonFields(type="json_fields", fields={"a": 1})
    assert grade(spec, 'use {curly} then {"a": 1}', None).passed


def test_unknown_spec_is_a_bug():
    with pytest.raises(AssertionError):
        grade(object(), "x", None)  # type: ignore[arg-type]


def test_contains_all_missing_and_case_sensitive():
    spec = ContainsAll(type="contains_all", terms=["Abc", "x"], case_sensitive=True)
    g = grade(spec, "abc x", None)
    assert not g.passed and "Abc" in g.detail


def test_judge_specs_are_not_graded_here():
    assert grade(Judge(type="judge", rubric="r"), "text", None) is None


def test_no_tool_call_expected_with_null_function():
    g = grade(ToolCall(type="tool_call", name=None), "", [{"function": None}])
    assert g.passed is False


def test_the_cut_off_reason_is_a_shared_constant():
    from local_llm_admin.eval_graders import STOPPED_THINKING

    assert final_answer("<think>x", "length", 0) == (None, STOPPED_THINKING)


def test_final_answer_drops_reasoning_before_an_unopened_close_tag():
    # templates that put <think> in the prompt return only the closing tag
    assert final_answer("so it is 41... no</think>\n42", "stop", 0) == ("42", None)
    assert final_answer("<think>a</think>b</think> c", "stop", 0) == ("c", None)


def test_thinking_cut_off_without_tags_is_stopped_while_thinking():
    from local_llm_admin.eval_graders import STOPPED_THINKING

    assert final_answer("the cat, the dog", "length", 0, thinking=True) == (None, STOPPED_THINKING)
    # thinking off, or reasoning the server split out: a cut-off answer is still an answer
    assert final_answer("the cat, the dog", "length", 0) == ("the cat, the dog", None)
    assert final_answer("the cat", "length", 120, thinking=True) == ("the cat", None)
    assert final_answer("x</think>the cat", "length", 0, thinking=True) == ("the cat", None)


def test_grade_response_never_grades_cut_off_reasoning():
    from local_llm_admin.eval_graders import grade_response

    spec = ContainsAll(type="contains_all", terms=["cat", "dog"])
    cut = grade_response(spec, "the cat, the dog", "length", 0, None, thinking=True)
    assert cut["score"] == 0.0 and "stopped while thinking" in cut["grade_detail"]
    late = grade_response(spec, "about dogs</think>a cat", "stop", 0, None, thinking=True)
    assert late["passed"] == 0 and "dog" in late["grade_detail"]


@pytest.mark.parametrize(
    "text, answer, ok",
    [
        ("Answer: I believe it is B", "I", False),  # "I" the pronoun is not option I
        ("Answer: I", "I", True),
        ("The answer is a trick question; B", "A", False),  # "a" the article
        ("The answer is C because it is heavier", "C", True),
        ("answer: c.", "C", True),
        ("Answer: c\nBecause the weights match", "C", True),  # alone on its line
        ("Answer Is B", "B", True),  # keywords in any case
        ("Answer: I believe it is B", "B", True),
        ("Answer: I'm sure it's D", "D", True),
        ("Answer: C\nThis is A common mistake", "C", True),  # explicit keyword beats "is"
        ("This is A-grade work", "A", False),  # "A" is not standalone
        ("This is A common mistake", "A", False),
        # no "answer"/"choice"; "is D that" is not standalone, so the leading "B." decides
        ("I think it is a trap", "I", False),  # the pronoun, not option I
        ("A good answer is hard", "A", False),  # the article, not option A
        ("B. It is D that people pick wrongly", "B", True),
        ("B. It is D that people pick wrongly", "D", False),
    ],
)
def test_choice_letter_is_case_sensitive_except_alone(text, answer, ok):
    assert grade(Choice(type="choice", answer=answer), text, None).passed is ok


def test_regex_timeout_is_a_zero_grade():
    import time

    from local_llm_admin import eval_graders

    spec = Regex(type="regex", pattern="(a|aa)+$")
    t0 = time.monotonic()
    g = grade(spec, "a" * 40 + "b", None)
    assert (g.score, g.passed, g.detail) == (0.0, False, "regex timed out after 1 s")
    assert time.monotonic() - t0 < eval_graders.REGEX_TIMEOUT_S + 1.5


async def test_regex_timeout_leaves_the_event_loop_running():
    import asyncio
    import time

    spec = Regex(type="regex", pattern="(a|aa)+$")
    gaps: list[float] = []
    stop = False

    async def ticker() -> None:
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    g = await asyncio.to_thread(grade, spec, "a" * 40 + "b", None)
    stop = True
    await task
    assert not g.passed and "timed out" in g.detail
    assert len(gaps) > 20 and max(gaps) < 0.3  # the GIL is released while matching


def test_regex_that_stdlib_re_backtracks_on_is_fast_with_regex():
    spec = Regex(type="regex", pattern="a?" * 25 + "a" * 25)
    assert grade(spec, "a" * 25, None).passed
