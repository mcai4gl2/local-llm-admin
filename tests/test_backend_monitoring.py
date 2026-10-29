"""Backend intent, serving availability and health retain separate scopes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.alerts import AlertStore, Evaluator
from local_llm_admin.backend_availability import BackendAvailabilityStore, backend_state
from local_llm_admin.ingestion import IngestStore

from .test_alerts import _snap


def snap(**kw: Any) -> Any:
    return replace(
        _snap(engine="splash", intent="splash:stock", mlx_serving=None),
        backends={
            "splash": {
                "label": "Splash",
                "enabled": True,
                "state": "ready",
                "ready": True,
                "profile": "stock",
                "fingerprint": "fp",
            }
        },
        **kw,
    )


def test_missing_profile_owner_is_alerted_with_registry_label(tmp_path: Path) -> None:
    alerts = AlertStore(tmp_path / "a.sqlite")
    evaluator = Evaluator(alerts, IngestStore(alerts.path))
    s = snap(engine="none")
    evaluator.evaluate(s)
    evaluator.evaluate(replace(s, wall=s.wall + 31, mono=s.mono + 31))
    row = next(a for a in alerts.list("open", 100) if a["key"] == "engine_down")
    assert "Splash" in row["title"] and "not restarted automatically" in row["detail"]
    assert "MLX" not in row["title"]


@pytest.mark.parametrize("state", ["starting", "released", "recovering"])
def test_unloaded_intended_backend_has_grace_then_health_alert(tmp_path: Path, state: str) -> None:
    alerts = AlertStore(tmp_path / "a.sqlite")
    evaluator = Evaluator(alerts, IngestStore(alerts.path))
    s = snap()
    s.backends["splash"].update(state=state, ready=False)
    evaluator.evaluate(s)
    assert not any(a["key"] == "engine_unhealthy" for a in alerts.list("open", 100))
    evaluator.evaluate(replace(s, wall=s.wall + 301, mono=s.mono + 301))
    row = next(a for a in alerts.list("open", 100) if a["key"] == "engine_unhealthy")
    assert "Splash" in row["title"]
    evaluator.evaluate(snap(wall=s.wall + 302, mono=s.mono + 302))
    assert not any(a["key"] == "engine_unhealthy" for a in alerts.list("open", 100))


@pytest.mark.parametrize(
    ("engine", "status", "expected"),
    [
        ("splash", "ready", "serving"),
        ("splash", "starting", "loading"),
        ("splash", "released", "released"),
        ("splash", "recovering", "recovering"),
        ("splash", "unknown", "unknown"),
        ("none", "absent", "down"),
        ("conflict", "ready", "conflict"),
        ("unknown", "ready", "unknown"),
    ],
)
def test_scoped_availability_semantics(engine: str, status: str, expected: str) -> None:
    s = snap(engine=engine)
    s.backends["splash"].update(state=status, ready=status == "ready")
    assert backend_state(s, "splash")["state"] == expected
    assert backend_state(snap(intent="off", engine="none"), "splash")["state"] == "off"
    assert backend_state(snap(job_running=True), "splash")["state"] == "maintenance"


def test_new_availability_never_rewrites_mlx_or_merges_profiles(tmp_path: Path) -> None:
    store = BackendAvailabilityStore(tmp_path / "a.sqlite")
    for t, state, profile, fp in [
        (100, "serving", "stock", "a"),
        (110, "released", "stock", "a"),
        (120, "recovering", "modified", "b"),
        (130, "serving", "stock", "c"),
    ]:
        store.add(
            "splash", t, {"state": state, "intended": 1, "profile": profile, "fingerprint": fp}
        )
    report = store.query("splash", 90, 150, interval_s=10, tz_offset_min=0, now=150)
    assert len(report["profiles"]) == 3
    assert report["totals"]["availability"] == 0.5
    assert report["totals"]["unknown_s"] == 20
    assert (
        store.query("splash", 0, 90, interval_s=10, tz_offset_min=0, now=150)["totals"][
            "availability"
        ]
        is None
    )
    assert store.prune(120) == 2


def test_profile_mismatch_unknown_intent_and_filtered_history(tmp_path: Path) -> None:
    s = snap()
    s.backends["splash"]["profile"] = "different"
    assert backend_state(s, "splash")["state"] == "unknown"
    assert backend_state(snap(intent=None), "splash")["intended"] is None
    store = BackendAvailabilityStore(tmp_path / "a.sqlite")
    store.add("splash", 100, {"state": "serving", "intended": 1, "profile": "stock"})
    assert (
        store.query("splash", 90, 120, interval_s=10, tz_offset_min=0, now=120, profile="other")[
            "profiles"
        ]
        == []
    )


@pytest.mark.parametrize(
    ("enabled", "state", "ready", "expected"),
    [
        (False, "absent", False, "fail"),
        (True, "unknown", None, "unknown"),
        (True, "released", False, "warn"),
        (True, "ready", True, "ok"),
    ],
)
def test_diagnostics_uses_cached_backend_readiness(
    enabled: bool, state: str, ready: bool | None, expected: str
) -> None:
    from types import SimpleNamespace

    from local_llm_admin.diagnostics import Diagnostics

    diag = object.__new__(Diagnostics)
    diag.control = SimpleNamespace(intent=lambda: {"value": "splash:stock"})
    diag.backend_health = lambda: {
        "splash": {"label": "Splash", "enabled": enabled, "state": state, "ready": ready}
    }
    assert diag._serving_backend()["status"] == expected


def test_profile_reports_stop_at_other_profile_and_fingerprint_boundaries(tmp_path: Path) -> None:
    store = BackendAvailabilityStore(tmp_path / "a.sqlite")
    for ts, profile, fingerprint in [
        (100, "stock", "a"),
        (105, "modified", "b"),
        (110, "stock", "c"),
    ]:
        store.add(
            "splash",
            ts,
            {"state": "serving", "intended": 1, "profile": profile, "fingerprint": fingerprint},
        )
    report = store.query("splash", 100, 120, interval_s=10, tz_offset_min=0, now=120)
    scopes = {
        (r["profile"], r["fingerprint"]): r["totals"]["intended_s"] for r in report["profiles"]
    }
    assert scopes == {("stock", "a"): 5, ("modified", "b"): 5, ("stock", "c"): 10}
    filtered = store.query(
        "splash", 100, 120, interval_s=10, tz_offset_min=0, now=120, profile="stock"
    )
    assert filtered["totals"]["intended_s"] == 15
    assert filtered["totals"]["unknown_s"] == 5
