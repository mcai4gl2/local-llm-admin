"""Strata token usage: ``/metrics`` request records become ``inference_requests`` rows."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from local_llm_admin.config import Settings
from local_llm_admin.engines import StrataClient
from local_llm_admin.performance_store import PerformanceStore
from local_llm_admin.strata_usage import STRATA_SOURCE, StrataUsage, strata_rows

FIX = Path(__file__).parent / "fixtures" / "strata"
METRICS = json.loads((FIX / "metrics.json").read_text())
HEALTH = json.loads((FIX / "health.json").read_text())
SINCE = METRICS["totals"]["since"]
MODEL = "strata:Q2_0"
P = SINCE + 10_000.0  # the poll time


def _metrics(records=None, **totals):
    m = copy.deepcopy(METRICS)
    if records is not None:
        m["requests"] = records
    m["totals"].update(totals)
    return m


def _rec(t, prompt=10, reused=0, out=5, **kw):
    return {
        "time": t,
        "duration_s": 2.0,
        "finish": "stop",
        "prompt_tokens": prompt,
        "reused": reused,
        "output_tokens": out,
        "prompt_ms": 500.0,
        "decode_ms": 1500.0,
        "decode_tok_s": 3.3,
        **kw,
    }


def test_fixture_records_map_to_rows():
    rows, state = strata_rows(METRICS, MODEL, None, now=P)
    assert len(rows) == 4
    newest = rows[0]
    assert newest["key"] == f"{SINCE}:{METRICS['requests'][0]['time']}"
    assert newest["started"] == METRICS["requests"][0]["time"]
    assert newest["finished"] == pytest.approx(newest["started"] + 35.9)
    assert (newest["prompt_tokens"], newest["cached_tokens"], newest["generated_tokens"]) == (
        29029,
        0,
        64,
    )
    assert newest["ttft_s"] == pytest.approx(34.8939)
    assert newest["prefill_tok_s"] == pytest.approx(29029 / 34.8939)
    assert newest["decode_s"] == pytest.approx(0.9894)
    assert (newest["decode_tok_s"], newest["elapsed_s"], newest["finish_reason"]) == (
        64.7,
        35.9,
        "length",
    )
    assert newest["model"] == MODEL and newest["state"] == "completed"
    assert newest["complete"] == 1 and newest["attribution"] == "generation"
    assert sum(r["prompt_tokens"] for r in rows) == METRICS["totals"]["prompt_tokens"]
    assert state == {
        "since": SINCE,
        "requests": 4,
        "prompt_tokens": 32844,
        "reused": 0,
        "output_tokens": 2277,
        "polled": P,
    }


def test_a_second_identical_poll_adds_nothing():
    _rows, state = strata_rows(METRICS, MODEL, None, now=P)
    rows, again = strata_rows(METRICS, MODEL, state, now=P)
    assert rows == [] and again == state


def test_requests_beyond_the_kept_list_become_one_gap_row():
    t0 = SINCE + 10
    first = _metrics(
        [_rec(t0 + 1), _rec(t0)], requests=2, prompt_tokens=20, reused=0, output_tokens=10
    )
    _rows, state = strata_rows(first, MODEL, None, now=P)
    # five more finished; the list keeps only the newest two
    later = _metrics(
        [_rec(t0 + 7, prompt=100, reused=40, out=7), _rec(t0 + 6, prompt=50, out=3)],
        requests=7,
        prompt_tokens=20 + 150 + 300,
        reused=40 + 25,
        output_tokens=10 + 10 + 30,
    )
    rows, state2 = strata_rows(later, MODEL, state, now=P)
    gap = [r for r in rows if r["state"] == "aggregate"]
    assert len(rows) == 3 and len(gap) == 1
    g = gap[0]
    assert g["key"] == f"{SINCE}:gap:7" and g["requests"] == 3
    assert (g["prompt_tokens"], g["cached_tokens"], g["generated_tokens"]) == (300, 25, 30)
    assert g["started"] == P  # they finished after the previous poll
    assert g["ttft_s"] is None and g["decode_tok_s"] is None
    assert state2["requests"] == 7


def test_new_records_are_the_newest_finished_whatever_their_start():
    # parallel slots: a request that started first can finish last (the list is in
    # finish order, newest first; "time" is the start)
    t0 = SINCE + 10
    first = _metrics([_rec(t0 + 5)], requests=1, prompt_tokens=10, reused=0, output_tokens=5)
    _rows, state = strata_rows(first, MODEL, None, now=P)
    later = _metrics(
        [_rec(t0), _rec(t0 + 5)], requests=2, prompt_tokens=20, reused=0, output_tokens=10
    )
    rows, _ = strata_rows(later, MODEL, state, now=P + 60)
    assert [r["started"] for r in rows] == [t0]  # older start, newly finished: not a gap


def test_a_first_poll_counts_what_the_process_did_before_it():
    m = _metrics([_rec(SINCE + 5)], requests=3, prompt_tokens=30, reused=0, output_tokens=15)
    rows, _state = strata_rows(m, MODEL, None, now=P)
    gap = next(r for r in rows if r["state"] == "aggregate")
    assert gap["requests"] == 2 and gap["started"] == SINCE and gap["prompt_tokens"] == 20


def test_a_restart_is_a_new_process_and_never_re_adds_old_totals():
    _rows, state = strata_rows(METRICS, MODEL, None, now=P)
    since = SINCE + 1000
    m = _metrics(
        [_rec(since + 2)], since=since, requests=1, prompt_tokens=10, reused=0, output_tokens=5
    )
    rows, state2 = strata_rows(m, "strata:IQ3_XXS", state, now=P)
    assert [r["key"] for r in rows] == [f"{since}:{since + 2}"]
    assert rows[0]["model"] == "strata:IQ3_XXS"
    assert state2["since"] == since and state2["requests"] == 1


def test_records_without_a_time_are_skipped_and_counted_in_the_gap():
    m = _metrics(
        [{"prompt_tokens": 5}, "junk", _rec(SINCE + 1)],
        requests=3,
        prompt_tokens=15,
        reused=0,
        output_tokens=5,
    )
    rows, _ = strata_rows(m, MODEL, None, now=P)
    assert [r["state"] for r in rows] == ["completed", "aggregate"]
    assert rows[1]["requests"] == 2 and rows[1]["prompt_tokens"] == 5


def test_missing_fields_stay_null():
    m = _metrics(
        [{"time": SINCE + 1}], requests=1, prompt_tokens=None, reused=None, output_tokens=None
    )
    rows, _ = strata_rows(m, MODEL, None, now=P)
    r = rows[0]
    assert r["prompt_tokens"] is None and r["cached_tokens"] is None
    assert r["generated_tokens"] is None and r["finished"] is None and r["prefill_tok_s"] is None


@pytest.mark.parametrize("raw", [{}, {"totals": "x"}, {"totals": {"requests": 3}}])
def test_metrics_without_a_process_start_add_nothing(raw):
    assert strata_rows(raw, MODEL, {"since": 1.0}, now=P) == ([], {"since": 1.0})


def test_counters_that_went_back_add_no_gap():
    _rows, state = strata_rows(METRICS, MODEL, None, now=P)
    m = _metrics([], requests=1, prompt_tokens=1, reused=0, output_tokens=1)
    rows, state2 = strata_rows(m, MODEL, state, now=P)
    assert rows == [] and state2["requests"] == 1


# --- the poller -----------------------------------------------------------------


@pytest.fixture
def poller(tmp_path):
    calls = {"metrics": METRICS, "health": HEALTH, "status": 200, "n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["status"] != 200:
            return httpx.Response(calls["status"])
        body = calls["health"] if request.url.path == "/health" else calls["metrics"]
        return httpx.Response(200, json=body)

    s = Settings(
        strata_root=tmp_path / "strata",
        strata_port=1,
        strata_enabled=True,
        admin_data_dir=tmp_path / "data",
    )
    perf = PerformanceStore(tmp_path / "data/admin.sqlite")
    usage = StrataUsage(s, StrataClient(s, transport=httpx.MockTransport(handler)), perf)
    return usage, perf, calls


def _count(perf):
    return perf.tokens(0, 4e9, tz_offset_min=0, group="day")["totals"]["requests"]


async def test_poll_inserts_once_and_tracks_state(poller):
    usage, perf, _calls = poller
    assert await usage.poll() == 4
    assert await usage.poll() == 0
    assert _count(perf) == 4
    rows = perf.requests(0, 4e9)["requests"]
    assert {r["source"] for r in rows} == {STRATA_SOURCE} and rows[0]["model"] == MODEL
    assert perf.get_kv("strata_usage")[repr(SINCE)]["requests"] == 4  # per process


@pytest.mark.parametrize("status", [401, 500])
async def test_poll_survives_refusals(poller, status):
    usage, perf, calls = poller
    calls["status"] = status
    assert await usage.poll() == 0 and _count(perf) == 0


async def test_poll_needs_strata_itself_on_the_port(poller):
    usage, perf, calls = poller
    calls["health"] = {"status": "ok"}  # some other server
    assert await usage.poll() == 0 and _count(perf) == 0


async def test_poll_survives_a_timeout(tmp_path):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    s = Settings(strata_root=tmp_path / "s", strata_port=1, strata_enabled=True)
    perf = PerformanceStore(tmp_path / "admin.sqlite")
    usage = StrataUsage(s, StrataClient(s, transport=httpx.MockTransport(handler)), perf)
    assert await usage.poll() == 0


async def test_tick_polls_only_while_strata_is_the_engine(poller):
    usage, perf, calls = poller
    await usage.on_tick(None, None)
    await usage.on_tick(None, {"engine": "mlx"})
    assert calls["n"] == 0
    await usage.on_tick(None, {"engine": "strata"})
    assert _count(perf) == 4
    usage.settings = usage.settings.model_copy(update={"strata_enabled": False})
    calls["n"] = 0
    await usage.on_tick(None, {"engine": "strata"})
    assert calls["n"] == 0


async def test_poll_without_metrics_adds_nothing(poller):
    usage, perf, calls = poller
    calls["metrics"] = ["not", "a", "dict"]
    assert await usage.poll() == 0 and _count(perf) == 0


async def test_a_failed_poll_never_stops_the_tick(poller, monkeypatch, caplog):
    usage, perf, _calls = poller

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(perf, "insert_rows", boom)
    await usage.on_tick(None, {"engine": "strata"})
    assert "strata usage poll failed" in caplog.text


async def test_poll_asks_for_the_whole_request_list(poller, monkeypatch):
    usage, _perf, _calls = poller
    paths = []
    real = usage.strata.get_json

    async def spy(path):
        paths.append(path)
        return await real(path)

    monkeypatch.setattr(usage.strata, "get_json", spy)
    await usage.poll()
    assert paths == ["/health", "/metrics?requests=all"]  # by default it lists only 12


def test_a_live_reading_with_a_disconnected_stream_matches_the_totals():
    # Strata 0.1.44.1, /metrics?requests=all: a completed, a length and a client
    # disconnect; every finished request is listed, cut-off ones with their output so far
    live = json.loads((FIX / "metrics-requests-all.json").read_text())
    rows, _state = strata_rows(live, MODEL, None, now=P)
    assert [r["finish_reason"] for r in rows] == ["disconnect", "length", "stop"]
    t = live["totals"]
    assert sum(r["requests"] for r in rows) == t["requests"]
    assert sum(r["prompt_tokens"] for r in rows) == t["prompt_tokens"]
    assert sum(r["generated_tokens"] for r in rows) == t["output_tokens"]


# --- the final reading the wrapper saves at stop ------------------------------------


class _Snap:
    def __init__(self, mounted=True):
        self.drive_mounted = mounted


def _final(usage, metrics, pid=1, raw=None):
    run = usage.settings.strata_root / "run"
    run.mkdir(parents=True, exist_ok=True)
    path = run / f"metrics-final-{pid}.json"
    path.write_text(raw if raw is not None else json.dumps(metrics))
    return path


def _more(extra, since=SINCE):
    base = copy.deepcopy(METRICS)
    t = base["totals"]
    t["since"] = since
    t["requests"] += len(extra)
    t["prompt_tokens"] += sum(r["prompt_tokens"] for r in extra)
    t["output_tokens"] += sum(r["output_tokens"] for r in extra)
    base["requests"] = extra + base["requests"]
    return base


async def test_the_final_reading_adds_what_finished_after_the_last_poll(poller):
    usage, perf, _calls = poller
    await usage.on_tick(_Snap(), {"engine": "strata"})
    assert _count(perf) == 4
    path = _final(usage, _more([_rec(SINCE + 900), _rec(SINCE + 901, out=9)]))
    await usage.on_tick(_Snap(), {"engine": "mlx"})  # after the switch: not polled live
    assert _count(perf) == 6 and not path.exists()
    t = perf.tokens(0, 4e9, tz_offset_min=0, group="day")["totals"]
    assert t["output_tokens"] == 2277 + 14
    assert {r["model"] for r in perf.requests(0, 4e9, limit=100)["requests"]} == {MODEL}


async def test_a_final_reading_from_an_old_process_counts_once(poller):
    usage, perf, calls = poller
    await usage.poll()  # process A: 4 requests
    later = SINCE + 5000
    calls["metrics"] = _metrics(
        [_rec(later + 1)], since=later, requests=1, prompt_tokens=10, reused=0, output_tokens=5
    )
    await usage.poll()  # process B is polled before A's final file is read
    _final(usage, _more([_rec(SINCE + 900)]), pid=1)
    _final(usage, _more([_rec(SINCE + 900)]), pid=2)  # the same reading twice: once
    await usage.on_tick(_Snap(), {"engine": "strata"})
    assert _count(perf) == 4 + 1 + 1


async def test_bad_or_huge_final_files_are_dropped(poller, monkeypatch):
    import local_llm_admin.strata_usage as su

    usage, perf, _calls = poller
    bad = _final(usage, None, pid=1, raw="{not json")
    odd = _final(usage, None, pid=2, raw="[1, 2]")
    monkeypatch.setattr(su, "MAX_FINAL_BYTES", 10)
    big = _final(usage, _more([_rec(SINCE + 1)]), pid=3)
    assert await usage.read_final() == 0
    assert not bad.exists() and not odd.exists() and not big.exists() and _count(perf) == 0


async def test_final_files_wait_for_the_drive_and_are_bounded(poller, monkeypatch):
    import local_llm_admin.strata_usage as su

    usage, perf, _calls = poller
    for pid in range(3):
        _final(usage, _more([_rec(SINCE + 900 + pid)]), pid=pid)
    await usage.on_tick(_Snap(mounted=None), None)  # drive unknown: not read
    assert _count(perf) == 0
    monkeypatch.setattr(su, "MAX_FINALS", 2)
    left = lambda: len(list((usage.settings.strata_root / "run").glob("metrics-final-*")))  # noqa: E731
    assert await usage.read_final() == 5 and left() == 1  # oldest first, two per tick
    assert await usage.read_final() == 0 and left() == 0  # the same counts: nothing new


async def test_no_final_files_and_no_run_directory(poller):
    usage, _perf, _calls = poller
    assert await usage.read_final() == 0


def test_process_states_are_bounded(tmp_path):
    from local_llm_admin.strata_usage import MAX_PROCESSES, _remember

    states = {repr(float(i)): {"since": float(i)} for i in range(MAX_PROCESSES + 3)}
    kept = _remember(states)
    assert len(kept) == MAX_PROCESSES and repr(0.0) not in kept


async def test_a_final_reading_without_a_process_start_is_dropped(poller):
    usage, perf, _calls = poller
    path = _final(usage, {"requests": [_rec(SINCE)], "totals": {"requests": 1}})
    assert await usage.read_final() == 0 and not path.exists() and _count(perf) == 0


async def test_an_unreadable_run_directory_is_skipped(poller, monkeypatch):
    usage, _perf, _calls = poller
    _final(usage, _more([_rec(SINCE + 1)]))

    def vanished(self, pattern):
        raise FileNotFoundError("gone between the listing and its stat")

    monkeypatch.setattr(Path, "glob", vanished)
    assert await usage.read_final() == 0
