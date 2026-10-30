"""Qualification decisions use frozen references and never turn missing evidence into a pass."""

from local_llm_admin.ocr_qualification import compare_load, compare_memory, evaluate

from .ocr_fixtures import result


def test_numeric_exactness_and_incomplete_pages_fail_independently_of_cer():
    body = result()
    reference = {"pages": ["Page 1"], "max_cer": 0.05, "numeric_fields": ["123.45"]}
    score = evaluate(reference, body)
    assert score["cer"] == 0 and not score["passed"] and not score["numeric_exact"]
    body["complete"] = False
    assert not evaluate({**reference, "numeric_fields": []}, body)["complete"]


def test_empty_reference_requires_empty_output_and_page_order():
    body = result()
    body["pages"][0]["text"] = ""
    body["pages"][0]["markdown"] = ""
    body["pages"][0]["empty"] = True
    assert evaluate({"pages": [""], "max_cer": 0, "numeric_fields": []}, body)["passed"]
    body["pages"][0]["number"] = 2
    assert not evaluate({"pages": [""], "max_cer": 0}, body)["passed"]


def test_load_qualification_refuses_missing_baseline_and_regressions():
    assert not compare_load({}, {})["passed"]
    baseline = {"chat_p95_ms": 100, "chat_tokens_s": 20, "embed_p95_ms": 100, "errors": 0,
                "chat_requests": 30, "embed_requests": 30}
    assert compare_load(baseline, baseline)["passed"]
    assert not compare_load(baseline, {**baseline, "chat_p95_ms": 126})["passed"]
    assert not compare_load(baseline, {**baseline, "chat_tokens_s": 17})["passed"]
    assert not compare_load(baseline, {**baseline, "embed_requests": 29})["passed"]


def test_unknown_memory_or_elevated_pressure_cannot_qualify():
    assert not compare_memory({})["passed"]
    sample = {"pressure_normal": True, "ocr_peak_gb": 10, "reservation_gb": 12,
              "aggregate_peak_gb": 50, "ram_gb": 64, "wired_limit_gb": 58, "headroom_gb": 6}
    assert compare_memory(sample)["passed"]
    assert not compare_memory({**sample, "pressure_normal": False})["passed"]
    assert not compare_memory({**sample, "reservation_gb": 9})["passed"]
    assert not compare_memory({**sample, "aggregate_peak_gb": 59})["passed"]
