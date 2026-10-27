"""Mac VM units and recovery review must not turn unknown host memory into a pass."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops/splash-phase0"))
import reclamation as r  # noqa: E402

RAW = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n" + "\n".join(
    f"{label}: {i + 1}." for i, label in enumerate(r.PAGE_FIELDS | r.COUNTER_FIELDS)
)


def snapshot(available: int = 50 * 1024**3) -> dict[str, Any]:
    return dict(
        total_bytes=64 * 1024**3,
        available_bytes=available,
        vm=r.parse_vm_stat(RAW),
        other_rss_bytes=100,
        other_uss_bytes=50,
        other_uss_missing=0,
        boot_time=1.0,
        span_s=0.1,
    )


def captures() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline = [snapshot() for _ in range(3)]
    stops = [dict(target_s=t, observed_s=float(t) + 0.1, snapshot=snapshot()) for t in r.OFFSETS]
    return baseline, stops


def review(b: list[dict[str, Any]], s: list[dict[str, Any]]) -> dict[str, Any]:
    return r.review_reclamation(b, s, complete=True, ownership_ok=True, functional_ok=True)


def test_page_bytes_counters_and_overlap() -> None:
    vm = r.parse_vm_stat(RAW + "\nUnknown counter: 55.\n")
    assert vm["page_size"] == 16384
    assert vm["free_bytes"] == 16384
    assert vm["pageins_pages"] == len(r.PAGE_FIELDS) + 1
    assert not any(k.startswith("total") for k in vm)
    assert "file_backed_bytes" in vm and "active_bytes" in vm


@pytest.mark.parametrize(
    "raw",
    [
        "garbage",
        RAW.replace("16384", "0", 1),
        RAW.replace("16384", "123", 1),
        RAW + "\nPages free: 2.",
        RAW.replace("Pages active: 2.", "Pages active: -2."),
        RAW.replace("Pages active: 2.", "Pages active: word."),
        RAW.replace("Pages active: 2.\n", ""),
    ],
)
def test_refuse_bad_vm_stat(raw: str) -> None:
    with pytest.raises(ValueError):
        r.parse_vm_stat(raw)


def test_complete_recovery_and_allowlisted_output() -> None:
    b, s = captures()
    b[0]["deployment_name"] = "secret"
    s[0]["snapshot"]["deployment_name"] = "secret"
    result = review(b, s)
    assert result["gate"] == "pass" and result["recovered_90_s"]
    assert result["available_gap_bytes"] == 0 and result["vm_delta_bytes"]["wired_bytes"] == 0
    assert "secret" not in str(result)


def test_extended_recovery_cannot_pass_90_seconds() -> None:
    b, s = captures()
    for point in s[:-1]:
        point["snapshot"]["available_bytes"] = 40 * 1024**3
    result = review(b, s)
    assert result["gate"] == "inconclusive" and not result["recovered_90_s"]
    assert result["latest_above_threshold"]


def test_last_three_90_second_samples_and_actual_time() -> None:
    b, s = captures()
    for point in s[:3]:
        point["snapshot"]["available_bytes"] = 40 * 1024**3
    assert review(b, s)["recovered_90_s"]
    s[5]["observed_s"] = 91.0
    assert not review(b, s)["recovered_90_s"]


def test_baseline_drift_confounds_pass() -> None:
    b, s = captures()
    b[0]["available_bytes"] -= 2 * 1024**3
    assert review(b, s)["gate"] == "inconclusive"


def test_known_functional_failure() -> None:
    b, s = captures()
    assert (
        r.review_reclamation(b, s, complete=True, ownership_ok=False, functional_ok=True)["gate"]
        == "fail"
    )
    assert (
        r.review_reclamation(b, s, complete=True, ownership_ok=True, functional_ok=False)["gate"]
        == "fail"
    )
    assert (
        r.review_reclamation(b, s, complete=False, ownership_ok=True, functional_ok=True)["gate"]
        == "inconclusive"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "baseline",
        "missing",
        "order",
        "target",
        "early",
        "late",
        "boot",
        "negative",
        "bool",
        "total",
        "counter",
        "missing_counter",
        "unknown_uss",
        "nan",
    ],
)
def test_bad_evidence_is_inconclusive(mutation: str) -> None:
    b, s = captures()
    if mutation == "baseline":
        b.pop()
    elif mutation == "missing":
        s.pop()
    elif mutation == "order":
        s.reverse()
    elif mutation == "target":
        s[0]["target_s"] = 3
    elif mutation == "early":
        s[0]["observed_s"] = 1.0
    elif mutation == "late":
        s[0]["observed_s"] = 100.0
    elif mutation == "boot":
        s[0]["snapshot"]["boot_time"] = 2.0
    elif mutation == "negative":
        s[0]["snapshot"]["available_bytes"] = -1
    elif mutation == "bool":
        s[0]["snapshot"]["available_bytes"] = True
    elif mutation == "total":
        s[0]["snapshot"]["total_bytes"] = 1
    elif mutation == "counter":
        s[0]["snapshot"]["vm"]["wired_bytes"] = -1
    elif mutation == "missing_counter":
        s[0]["snapshot"]["vm"].pop("wired_bytes")
    elif mutation == "unknown_uss":
        s[0]["snapshot"]["other_uss_bytes"] = "unknown"
    else:
        s[0]["snapshot"]["span_s"] = float("nan")
    assert review(b, s)["gate"] == "inconclusive"


def test_uss_missing_stays_unknown() -> None:
    b, s = captures()
    s[5]["snapshot"]["other_uss_missing"] = 1
    s[5]["snapshot"]["other_uss_bytes"] = None
    result = review(b, s)
    assert result["gate"] == "pass" and result["other_uss_delta_bytes"] is None


def test_vm_delta_is_not_sum_of_overlapping_categories() -> None:
    b, s = captures()
    for point in s:
        point["snapshot"]["vm"]["active_bytes"] += 2 * 1024**3
        point["snapshot"]["vm"]["file_backed_bytes"] += 2 * 1024**3
    result = review(b, s)
    assert (
        result["vm_delta_bytes"]["active_bytes"]
        == result["vm_delta_bytes"]["file_backed_bytes"]
        == 2 * 1024**3
    )
    assert "allocated_delta" not in result


def test_missing_other_process_rss_is_unknown() -> None:
    b, s = captures()
    s[5]["snapshot"]["other_rss_bytes"] = None
    result = review(b, s)
    assert result["gate"] == "pass" and result["other_rss_delta_bytes"] is None
