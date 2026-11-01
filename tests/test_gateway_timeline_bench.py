"""Live experiment comparison refuses mismatched or incomplete workloads."""

from __future__ import annotations

import pytest

from experiments.gateway_timeline.bench import compare


def _phase(name: str, scale: float) -> dict[str, object]:
    return {"phase": name, "payload_sha256": "a" * 64, "schedule": {"serial": 2,
            "concurrent_pairs": 1, "cancelled": 1}, "samples": [
        {"case": "serial", "status": 200, "first_event_s": 1 * scale,
         "first_content_s": 2 * scale, "total_s": 3 * scale, "cancelled": False},
        {"case": "serial", "status": 200, "first_event_s": 2 * scale,
         "first_content_s": 3 * scale, "total_s": 4 * scale, "cancelled": False},
        {"case": "concurrent", "status": 200, "first_event_s": 3 * scale,
         "first_content_s": 4 * scale, "total_s": 5 * scale, "cancelled": False},
        {"case": "concurrent", "status": 200, "first_event_s": 4 * scale,
         "first_content_s": 5 * scale, "total_s": 6 * scale, "cancelled": False},
        {"case": "cancelled", "status": 200, "first_event_s": 100 * scale,
         "first_content_s": None, "total_s": 101 * scale, "cancelled": True},
    ]}


def test_compare_reports_measured_latency_and_excludes_cancellation() -> None:
    result = compare(_phase("off", 1), _phase("on", 2))
    assert result["off"]["serial"]["first_event_p95_s"] == pytest.approx(1.95)
    assert result["on"]["serial"]["total_p95_s"] == pytest.approx(7.9)
    assert result["off"]["cancelled"]["count"] == 1
    assert result["limitations"]


def test_compare_refuses_mismatched_workload() -> None:
    on = _phase("on", 1)
    on["payload_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="payload"):
        compare(_phase("off", 1), on)
