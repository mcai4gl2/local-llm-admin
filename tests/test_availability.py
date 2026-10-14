"""Availability sampling, honest unknowns and retention (M4 §2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from local_llm_admin.alerts import Snapshot
from local_llm_admin.availability import (
    AvailabilityRecorder,
    AvailabilityStore,
    classify,
    summarize,
)
from local_llm_admin.config import Settings

HKT = timezone(timedelta(hours=8))
MIDNIGHT = datetime(2026, 10, 1, tzinfo=HKT).timestamp()  # 2026-10-01 00:00 HKT
IV = 60.0

SNAP = Snapshot(
    wall=MIDNIGHT,
    mono=0.0,
    drive_mounted=True,
    access_error=None,
    access_known=True,
    engine="mlx",
    mlx_serving=True,
    intent="mlx:a",
    job_running=False,
    lock=None,
    pressure="normal",
    litellm_ok=None,
    suspended=None,
)
GEN = {"pid": 7, "started": MIDNIGHT - 3600}


# --- classification --------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "gen", "expected"),
    [
        ({}, GEN, ("serving", 1)),
        ({"mlx_serving": False}, GEN, ("down", 1)),
        ({"mlx_serving": None}, GEN, ("down", 1)),
        ({"mlx_serving": False}, {"pid": 7, "started": MIDNIGHT - 30}, ("loading", 1)),
        ({"mlx_serving": False, "job_running": True}, GEN, ("loading", 1)),
        ({"mlx_serving": False}, None, ("down", 1)),
        ({"engine": "comfyui", "intent": "comfyui"}, None, ("comfyui", 0)),
        ({"engine": "none", "intent": "off"}, None, ("off", 0)),
        ({"engine": "none", "intent": "mlx:a"}, None, ("down", 1)),
        ({"engine": "none", "intent": "mlx:a", "job_running": True}, None, ("loading", 1)),
        ({"engine": "none", "intent": "unknown"}, None, ("unknown", None)),
        ({"engine": "none", "intent": None}, None, ("unknown", None)),
        ({"engine": "conflict"}, None, ("conflict", 1)),
        ({"engine": "unknown"}, None, ("unknown", 1)),
        ({"drive_mounted": None}, GEN, ("unknown", 1)),  # probe timed out (e.g. pressure)
        ({"drive_mounted": False}, GEN, ("unknown", 1)),
    ],
)
def test_classify(changes, gen, expected):
    assert classify(replace(SNAP, **changes), gen, load_grace_s=300) == expected


# --- summaries -------------------------------------------------------------------


def _s(ts, state="serving", intended=1):
    return {"ts": ts, "state": state, "mlx_intended": intended}


def _run(start, n, state="serving", intended=1):
    return [_s(start + k * IV, state, intended) for k in range(n)]


def test_full_coverage_serving_is_100_percent():
    out = summarize(
        _run(MIDNIGHT, 60),
        MIDNIGHT,
        MIDNIGHT + 3600,
        interval_s=IV,
        tz_offset_min=-480,
        now=MIDNIGHT + 3600,
    )
    t = out["totals"]
    assert t["availability"] == 1.0 and t["coverage"] == 1.0 and t["unknown_s"] == 0


def test_down_while_intended_lowers_availability():
    samples = _run(MIDNIGHT, 30) + _run(MIDNIGHT + 1800, 30, "down")
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 3600, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 3600
    )["totals"]
    assert t["availability"] == 0.5 and t["seconds"]["down"] == 1800


def test_intentional_off_and_comfyui_are_not_downtime():
    samples = (
        _run(MIDNIGHT, 20)
        + _run(MIDNIGHT + 1200, 20, "off", 0)
        + _run(MIDNIGHT + 2400, 20, "comfyui", 0)
    )
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 3600, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 3600
    )["totals"]
    assert t["availability"] == 1.0
    assert t["intended_s"] == 1200 and t["seconds"]["off"] == 1200
    assert t["seconds"]["comfyui"] == 1200 and t["coverage"] == 1.0


def test_zero_denominator_gives_no_percentage():
    t = summarize(
        _run(MIDNIGHT, 60, "off", 0),
        MIDNIGHT,
        MIDNIGHT + 3600,
        interval_s=IV,
        tz_offset_min=-480,
        now=MIDNIGHT + 3600,
    )["totals"]
    assert t["availability"] is None and t["intended_s"] == 0
    empty = summarize(
        [], MIDNIGHT, MIDNIGHT + 3600, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 3600
    )["totals"]
    assert empty["availability"] is None and empty["coverage"] == 0.0


def test_gap_longer_than_two_intervals_is_unknown_and_never_improves_uptime():
    # serving, admin down for 20 min, then down for 10 min
    samples = _run(MIDNIGHT, 10) + _run(MIDNIGHT + 1800, 10, "down")
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 2400, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 2400
    )["totals"]
    assert t["seconds"]["serving"] == 600  # the last pre-gap sample covers one interval
    assert t["unknown_s"] == 1200
    assert t["availability"] == 0.5 and t["coverage"] == 0.5


def test_short_gap_within_two_intervals_is_bridged():
    samples = [_s(MIDNIGHT), _s(MIDNIGHT + 110), _s(MIDNIGHT + 170)]
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 230, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 230
    )["totals"]
    assert t["unknown_s"] == 0 and t["seconds"]["serving"] == 230


def test_midnight_is_split_between_days():
    samples = _run(MIDNIGHT - 1800, 60)
    out = summarize(
        samples,
        MIDNIGHT - 86400,
        MIDNIGHT + 86400,
        interval_s=IV,
        tz_offset_min=-480,
        now=MIDNIGHT + 1800,
    )
    day1, day2 = out["days"]
    assert (day1["date"], day2["date"]) == ("2026-09-30", "2026-10-01")
    assert day1["seconds"]["serving"] == 1800 and day2["seconds"]["serving"] == 1800
    # the future part of today is not counted as unknown
    assert day2["unknown_s"] == 0 and day2["coverage"] == 1.0


def test_clock_rollback_never_counts_time_twice():
    # a clock that jumped back 10 minutes: two interleaved timelines of samples
    samples = _run(MIDNIGHT, 30) + _run(MIDNIGHT + 1200, 20, "down")
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 2400, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 2400
    )["totals"]
    assert t["observed_s"] <= 2400 and t["coverage"] <= 1.0


def test_unknown_samples_count_as_unobserved():
    samples = _run(MIDNIGHT, 30) + _run(MIDNIGHT + 1800, 30, "unknown", None)
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 3600, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 3600
    )["totals"]
    assert t["availability"] == 1.0 and t["coverage"] == 0.5 and t["unknown_s"] == 1800


def test_sample_before_the_window_covers_its_start():
    samples = [_s(MIDNIGHT - 30)] + _run(MIDNIGHT + 30, 2)
    t = summarize(
        samples, MIDNIGHT, MIDNIGHT + 150, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 150
    )["totals"]
    assert t["unknown_s"] == 0 and t["observed_s"] == 150


def test_segments_merge_runs_and_leave_gaps():
    samples = _run(MIDNIGHT, 3) + _run(MIDNIGHT + 180, 2, "down") + _run(MIDNIGHT + 1800, 1)
    segs = summarize(
        samples, MIDNIGHT, MIDNIGHT + 1860, interval_s=IV, tz_offset_min=-480, now=MIDNIGHT + 1860
    )["segments"]
    assert segs == [
        {"start": MIDNIGHT, "end": MIDNIGHT + 180, "state": "serving"},
        {"start": MIDNIGHT + 180, "end": MIDNIGHT + 300, "state": "down"},
        {"start": MIDNIGHT + 1800, "end": MIDNIGHT + 1860, "state": "serving"},
    ]


# --- store and recorder ------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return AvailabilityStore(tmp_path / "admin.sqlite")


def test_store_round_trip_includes_one_sample_before_the_window(store):
    for ts in (MIDNIGHT - 500, MIDNIGHT - 60, MIDNIGHT + 60, MIDNIGHT + 99999):
        store.add(ts, "serving", 1, "7@1", "a", "normal")
    got = store.samples(MIDNIGHT, MIDNIGHT + 3600, lookback_s=2 * IV)
    assert [s["ts"] for s in got] == [MIDNIGHT - 60, MIDNIGHT + 60]
    assert got[0]["model"] == "a" and got[0]["generation"] == "7@1"


def test_prune_is_bounded(store):
    for k in range(5):
        store.add(MIDNIGHT - 100 * 86400 + k, "serving", 1, None, None, None)
    store.add(MIDNIGHT, "serving", 1, None, None, None)
    assert store.prune(MIDNIGHT - 90 * 86400, limit=3) == 3
    assert store.prune(MIDNIGHT - 90 * 86400, limit=3) == 2
    assert len(store.samples(0, 2e9)) == 1


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _recorder(store, clock):
    return AvailabilityRecorder(store, Settings(), clock=clock)


def test_recorder_samples_once_per_interval(store):
    clock = Clock(MIDNIGHT)
    rec = _recorder(store, clock)
    observed = {"engine": "mlx", "mlx_name": "a", "generation": GEN}
    for step in (0, 15, 30, 45, 60, 75):
        clock.t = MIDNIGHT + step
        rec.on_tick(replace(SNAP, wall=clock.t), observed)
    rows = store.samples(0, 2e9)
    assert [r["ts"] for r in rows] == [MIDNIGHT, MIDNIGHT + 60]
    assert rows[0]["model"] == "a" and rows[0]["generation"] == f"7@{GEN['started']}"
    assert rows[0]["pressure"] == "normal"


def test_recorder_samples_again_after_the_clock_goes_back(store):
    clock = Clock(MIDNIGHT)
    rec = _recorder(store, clock)
    rec.on_tick(SNAP, None)
    clock.t = MIDNIGHT - 600
    rec.on_tick(replace(SNAP, wall=clock.t), None)
    assert len(store.samples(0, 2e9)) == 2


def test_recorder_does_not_name_a_model_it_cannot_see(store):
    rec = _recorder(store, Clock(MIDNIGHT))
    rec.on_tick(replace(SNAP, engine="unknown", drive_mounted=None), None)
    row = store.samples(0, 2e9)[0]
    assert row["state"] == "unknown" and row["model"] is None and row["generation"] is None


def test_recorder_prunes_daily_by_retention(store):
    clock = Clock(MIDNIGHT)
    store.add(MIDNIGHT - 100 * 86400, "serving", 1, None, None, None)
    pruned: list[float] = []
    rec = AvailabilityRecorder(
        store, Settings(), clock=clock, prune_extra=lambda before: pruned.append(before)
    )
    rec.on_tick(SNAP, None)
    assert [s["ts"] for s in store.samples(0, 2e9)] == [MIDNIGHT]
    assert pruned == [MIDNIGHT - 90 * 86400]
    clock.t += 3600
    rec.on_tick(replace(SNAP, wall=clock.t), None)
    assert len(pruned) == 1  # once a day


def test_daily_prune_continues_in_bounded_batches(store, monkeypatch):
    import local_llm_admin.availability as av

    monkeypatch.setattr(av, "PRUNE_BATCH", 2)
    monkeypatch.setattr(av, "PRUNE_BATCHES", 2)
    for k in range(7):
        store.add(MIDNIGHT - 100 * 86400 + k, "serving", 1, None, None, None)
    _recorder(store, Clock(MIDNIGHT)).on_tick(SNAP, None)
    # two batches of two this day; the rest waits for the next daily run
    assert len(store.samples(0, 2e9)) == 1 + 3
