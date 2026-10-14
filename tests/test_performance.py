"""Request-line parsing and correlation (M4 §1), from the recorded Mac log shapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_admin.performance import (
    MAX_OPEN,
    STALE_S,
    Correlator,
    PerfState,
    parse_perf_line,
)

FIX = Path(__file__).parent / "fixtures"
MAC_LOG = (FIX / "mac/mlx-server.log").read_text().splitlines()


def _events(lines: list[str], identity: str = "1:2:0") -> list[dict]:
    out, offset = [], 0
    for line in lines:
        ev = parse_perf_line(line)
        if ev is not None:
            ts = ev["ts"] if ev["ts"] is not None else 0.0
            out.append({**ev, "ts": ts, "dedup": f"{identity}:{offset}"})
        offset += len(line.encode()) + 1
    return out


def _run(lines: list[str], state: PerfState | None = None, **kw):
    corr = Correlator(state or PerfState())
    res = corr.apply(_events(lines, **kw))
    return corr.state, res


# --- parser --------------------------------------------------------------------


def test_parses_every_recorded_request_line_shape():
    kinds = [ev["kind"] for line in MAC_LOG if (ev := parse_perf_line(line))]
    assert kinds == [
        "generation",
        "server_start",
        "req_start",
        "req_queued",
        "req_failed",
        "req_start",
        "req_queued",
        "req_prefill",
        "req_ttft",
        "req_decode",
        "req_done",
        "req_start",
        "req_queued",
        "req_prefill",
        "req_ttft",
        "req_decode",
        "req_done",
    ]


def test_parser_extracts_measured_fields_only():
    queued = parse_perf_line(
        "2026-09-28 22:41:58,429 - INFO - Generation queued: request=735875ffb0"
        " prompt_tokens=17 max_tokens=50 images=1 audio=0 videos=0"
    )
    assert queued is not None
    assert queued["data"] == {"rid": "735875ffb0", "prompt_tokens": 17, "images": 1}
    assert queued["ts"] is not None
    prefill = parse_perf_line(
        "2026-09-28 22:41:58,844 - INFO - Prefill completed: request=735875ffb0"
        " prompt_tokens=17 cached_tokens=0 elapsed=0.408s rate=41.6 tok/s"
    )
    assert prefill is not None and prefill["data"] == {
        "rid": "735875ffb0",
        "prompt_tokens": 17,
        "cached_tokens": 0,
        "prefill_s": 0.408,
        "prefill_tok_s": 41.6,
    }
    done = parse_perf_line(
        "2026-09-28 22:41:58,977 - INFO - Request completed: endpoint=/chat/completions"
        " model=/Volumes/Models/models/active stream=False backend=continuous_batching"
        " prompt_tokens=17 generated_tokens=2 elapsed=0.550s prefill=41.6 tok/s"
        " decode=30.7 tok/s finish_reason=stop in_flight=0"
    )
    assert done is not None and done["data"] == {
        "stream": False,
        "prompt_tokens": 17,
        "generated_tokens": 2,
        "elapsed_s": 0.55,
    }


def test_parser_tolerates_missing_optional_fields():
    ev = parse_perf_line(
        "2026-09-28 22:41:58,844 - INFO - Prefill completed: request=ab12 prompt_tokens=17"
    )
    assert ev is not None and ev["data"] == {
        "rid": "ab12",
        "prompt_tokens": 17,
        "cached_tokens": None,
        "prefill_s": None,
        "prefill_tok_s": None,
    }


@pytest.mark.parametrize(
    "line",
    [
        "",
        "INFO:     Waiting for application startup.",
        "2026-09-28 22:33:32,321 - INFO - Prefill progress: request=7358719640 tokens=2048/2459",
        '"content": "Request completed: prompt_tokens=1"',
        "2026-09-28 22:41:58,429 - INFO - Generation queued: request=zz prompt_tokens=x",
        "2026-09-20 09:01:10,002 INFO Request started id=a1",  # an older, unsupported shape
    ],
)
def test_parser_ignores_unrelated_or_malformed_lines(line):
    assert parse_perf_line(line) is None


def test_generation_marker_names_the_weights():
    ev = parse_perf_line("===== 2026-09-28 22:06:52 start qwen3.8-27b =====")
    assert ev is not None and ev["kind"] == "generation"
    assert ev["data"] == {"name": "qwen3.8-27b"} and ev["ts"] is not None


# --- correlation ---------------------------------------------------------------


def test_recorded_log_gives_one_row_per_request_with_measured_fields():
    state, res = _run(MAC_LOG)
    rows = res.rows
    assert [r["state"] for r in rows] == ["cancelled", "completed", "completed"]
    cancelled, short, long = rows
    assert cancelled["request_id"] == "7358719640" and cancelled["error"] == (
        "stream_closed_before_completion"
    )
    assert cancelled["prompt_tokens"] == 2459 and cancelled["generated_tokens"] is None
    assert cancelled["complete"] == 0
    assert short["model"] == "qwen3.8-27b" and short["attribution"] == "generation"
    assert short["ttft_s"] == 0.464 and short["decode_tok_s"] == 15.3
    assert short["generated_tokens"] == 2 and short["finish_reason"] == "stop"
    assert short["stream"] == 0 and short["complete"] == 1
    assert long["prompt_tokens"] == 2185 and long["cached_tokens"] == 0
    assert long["prefill_tok_s"] == 389.2 and long["stream"] == 1
    # the id-less envelope lands on the decoded row with the same counts
    assert [u["fields"]["elapsed_s"] for u in res.updates] == [0.55, 6.224]
    assert [u["key"] for u in res.updates] == [short["key"], long["key"]]
    assert state.open == {} and state.pending_starts == []


def test_rows_keep_their_log_span_for_bounded_context():
    _, res = _run(MAC_LOG)
    short = res.rows[1]
    assert short["log_identity"] == "1:2:0"
    assert 0 < short["log_start"] < short["log_end"]


def test_interleaved_requests_correlate_by_id():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:00:00,100 - INFO - Generation queued: request=bb prompt_tokens=20",
        "2026-09-28 10:00:01,000 - INFO - Prefill completed: request=bb prompt_tokens=20"
        " cached_tokens=0 elapsed=0.9s rate=22.2 tok/s",
        "2026-09-28 10:00:01,100 - INFO - Prefill completed: request=aa prompt_tokens=10"
        " cached_tokens=0 elapsed=1.1s rate=9.1 tok/s",
        "2026-09-28 10:00:02,000 - INFO - Decode completed: request=aa generated_tokens=5"
        " elapsed=0.5s rate=10.0 tok/s finish_reason=stop",
        "2026-09-28 10:00:03,000 - INFO - Decode completed: request=bb generated_tokens=7"
        " elapsed=0.7s rate=10.0 tok/s finish_reason=length",
    ]
    _, res = _run(lines)
    by_id = {r["request_id"]: r for r in res.rows}
    assert by_id["aa"]["prefill_tok_s"] == 9.1 and by_id["aa"]["generated_tokens"] == 5
    assert by_id["bb"]["prefill_tok_s"] == 22.2 and by_id["bb"]["finish_reason"] == "length"


def test_ambiguous_envelope_is_not_attached():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:00:00,100 - INFO - Generation queued: request=bb prompt_tokens=10",
        "2026-09-28 10:00:02,000 - INFO - Decode completed: request=aa generated_tokens=5"
        " elapsed=0.5s rate=10.0 tok/s finish_reason=stop",
        "2026-09-28 10:00:03,000 - INFO - Decode completed: request=bb generated_tokens=5"
        " elapsed=0.7s rate=10.0 tok/s finish_reason=stop",
        "2026-09-28 10:00:03,100 - INFO - Request completed: endpoint=/chat/completions"
        " stream=True prompt_tokens=10 generated_tokens=5 elapsed=3.1s in_flight=1",
    ]
    _, res = _run(lines)
    assert len(res.rows) == 2 and res.updates == []


def test_failure_with_several_open_requests_is_standalone_and_ambiguous():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:00:00,100 - INFO - Generation queued: request=bb prompt_tokens=10",
        "2026-09-28 10:00:01,000 - WARNING - Request failed: endpoint=/chat/completions"
        " stream=True error=boom in_flight=1",
    ]
    state, res = _run(lines)
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row["request_id"] is None and row["state"] == "error"
    assert row["attribution"] == "ambiguous" and row["error"] == "boom"
    assert set(state.open) == {"aa", "bb"}  # neither is closed on a guess


def test_failure_before_queueing_consumes_its_start():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Request started: endpoint=/chat/completions"
        " model=/Volumes/Models/models/active stream=False in_flight=1",
        "2026-09-28 10:00:00,010 - WARNING - Request failed: endpoint=/chat/completions"
        " stream=False error=template_error in_flight=0",
    ]
    state, res = _run(lines)
    assert [r["state"] for r in res.rows] == ["error"]
    assert res.rows[0]["stream"] == 0 and res.rows[0]["attribution"] == "unknown"
    assert state.pending_starts == []


def test_cancellation_ignores_later_lines_for_the_closed_request():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:00:01,000 - WARNING - Request failed: endpoint=/chat/completions"
        " stream=True error=stream_closed_before_completion in_flight=0",
        "2026-09-28 10:00:02,000 - INFO - Decode completed: request=aa generated_tokens=5"
        " elapsed=0.5s rate=10.0 tok/s finish_reason=stop",
    ]
    _, res = _run(lines)
    assert [r["state"] for r in res.rows] == ["cancelled"]


def test_reused_request_id_closes_the_older_request_as_incomplete():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:05:00,000 - INFO - Generation queued: request=aa prompt_tokens=20",
    ]
    state, res = _run(lines)
    assert [r["state"] for r in res.rows] == ["incomplete"]
    assert res.rows[0]["prompt_tokens"] == 10
    assert state.open["aa"]["prompt_tokens"] == 20


def test_server_restart_closes_open_requests_and_resets_generation():
    lines = [
        "===== 2026-09-28 09:00:00 start a =====",
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "INFO:     Started server process [99]",
        "2026-09-28 10:05:00,000 - INFO - Generation queued: request=bb prompt_tokens=10",
    ]
    state, res = _run(lines)
    assert [r["state"] for r in res.rows] == ["incomplete"]
    assert res.rows[0]["model"] == "a"
    # a process start without a marker: the weights are no longer known
    assert state.open["bb"]["model"] is None and state.open["bb"]["attribution"] == "unknown"


def test_marker_then_process_start_keeps_the_marker_generation():
    state, _ = _run(
        [
            "===== 2026-09-28 09:00:00 start b =====",
            "INFO:     Started server process [99]",
            "2026-09-28 10:05:00,000 - INFO - Generation queued: request=bb prompt_tokens=10",
        ]
    )
    assert state.open["bb"]["model"] == "b"


def test_gap_closes_open_requests_and_forgets_the_generation():
    state, _ = _run(
        [
            "===== 2026-09-28 09:00:00 start a =====",
            "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        ]
    )
    corr = Correlator(state)
    res = corr.apply(
        _events(
            ["2026-09-28 10:05:00,000 - INFO - Generation queued: request=bb prompt_tokens=10"],
            identity="1:3:5",
        ),
        gap=True,
    )
    assert [r["state"] for r in res.rows] == ["incomplete"]
    assert corr.state.open["bb"]["attribution"] == "unknown"


def test_stale_open_requests_become_incomplete():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 12:00:00,000 - INFO - Generation queued: request=bb prompt_tokens=10",
    ]
    assert STALE_S < 7200
    state, res = _run(lines)
    assert [(r["request_id"], r["state"]) for r in res.rows] == [("aa", "incomplete")]
    assert set(state.open) == {"bb"}


def test_open_requests_are_bounded():
    lines = [
        f"2026-09-28 10:00:{i % 60:02d},000 - INFO - Generation queued: request=r{i}"
        " prompt_tokens=1"
        for i in range(MAX_OPEN + 3)
    ]
    state, res = _run(lines)
    assert len(state.open) == MAX_OPEN and len(res.rows) == 3


def test_missing_metrics_stay_null_and_row_is_partial():
    lines = [
        "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10",
        "2026-09-28 10:00:02,000 - INFO - Decode completed: request=aa generated_tokens=5"
        " finish_reason=stop",
    ]
    _, res = _run(lines)
    row = res.rows[0]
    assert row["state"] == "completed" and row["complete"] == 0
    assert row["ttft_s"] is None and row["decode_tok_s"] is None


def test_lines_for_unknown_requests_are_ignored():
    # backfill starting mid-request: never invent a row from a tail
    _, res = _run(
        [
            "2026-09-28 10:00:02,000 - INFO - Decode completed: request=zz generated_tokens=5"
            " elapsed=0.5s rate=10.0 tok/s finish_reason=stop",
            "2026-09-28 10:00:03,000 - INFO - Request completed: endpoint=/chat/completions"
            " stream=True prompt_tokens=1 generated_tokens=5 elapsed=1.0s in_flight=0",
        ]
    )
    assert res.rows == [] and res.updates == []


def test_state_round_trips_through_json():
    state, _ = _run(MAC_LOG[:5])
    again = PerfState.from_json(state.to_json())
    assert again == state
    assert PerfState.from_json(None) == PerfState()
    assert PerfState.from_json("not json") == PerfState()


def test_split_run_matches_single_run():
    """Restart mid-request: persisted state continues exactly where it stopped."""
    events = _events(MAC_LOG)
    whole = Correlator(PerfState()).apply(events)
    for cut in range(len(events) + 1):
        first = Correlator(PerfState())
        a = first.apply(events[:cut])
        second = Correlator(PerfState.from_json(first.state.to_json()))
        b = second.apply(events[cut:])
        assert a.rows + b.rows == whole.rows, cut
        assert a.updates + b.updates == whole.updates, cut


# --- edge inputs -------------------------------------------------------------------


def test_parser_edge_values():
    bad_date = parse_perf_line("===== 2026-13-45 99:99:99 start x =====")
    assert bad_date is not None and bad_date["ts"] is None
    ev = parse_perf_line(
        "2026-09-28 22:41:58,844 - INFO - Prefill completed: request=ab prompt_tokens=1"
        " elapsed=abc rate=1.5"
    )
    assert ev is not None and ev["data"]["prefill_s"] is None and ev["data"]["prefill_tok_s"] == 1.5
    assert (
        parse_perf_line(
            "2026-09-28 22:41:58,429 - INFO - Generation queued: request=bad!id prompt_tokens=1"
        )
        is None
    )
    failed = parse_perf_line(
        "2026-09-28 22:41:58,429 - WARNING - Request failed: error=<script>alert(1)</script>"
    )
    assert failed is not None and failed["data"] == {"stream": None, "error": "error"}


Q = "2026-09-28 10:00:00,000 - INFO - Generation queued: request=aa prompt_tokens=10"


def test_lines_for_closed_or_unknown_ids_change_nothing():
    state, res = _run(
        [
            "2026-09-28 10:00:01,000 - INFO - Prefill completed: request=zz prompt_tokens=1",
            "2026-09-28 10:00:01,000 - INFO - Decode started: request=zz time_to_first_token=1s",
        ]
    )
    assert res.rows == [] and state.open == {}


def test_prefill_without_measurements_keeps_nulls():
    state, _ = _run(
        [Q, "2026-09-28 10:00:01,000 - INFO - Prefill completed: request=aa prompt_tokens=10"]
    )
    row = state.open["aa"]
    assert row["prefill_tok_s"] is None and row["cached_tokens"] is None


def test_envelope_without_counts_or_stream():
    lines = [
        Q,
        "2026-09-28 10:00:02,000 - INFO - Decode completed: request=aa generated_tokens=5"
        " elapsed=0.5s rate=10.0 tok/s finish_reason=stop",
        "2026-09-28 10:00:03,000 - INFO - Request completed: elapsed=3.0s",
        "2026-09-28 10:00:03,000 - INFO - Request completed: prompt_tokens=10"
        " generated_tokens=5 elapsed=3.0s",
    ]
    _, res = _run(lines)
    assert res.updates == [{"key": res.rows[0]["key"], "fields": {"elapsed_s": 3.0}}]


def test_standalone_failures_without_start_or_stream():
    lone = "2026-09-28 10:00:01,000 - WARNING - Request failed: error=boom"
    _, res = _run([lone])
    assert res.rows[0]["stream"] is None and res.rows[0]["attribution"] == "unknown"
    _, res = _run(
        [
            Q,
            "2026-09-28 10:00:00,100 - INFO - Generation queued: request=bb prompt_tokens=1",
            lone,
        ]
    )
    assert res.rows[0]["attribution"] == "ambiguous" and res.rows[0]["stream"] is None


# --- live shapes seen on 2026-09-30 (mlx-vlm 0.7.3) ----------------------------------

T = "2026-09-29 20:47:"
STARTED = f"{T}48,280 - INFO - Request started: endpoint=/chat/completions model=/Volumes/Models/models/active stream=True in_flight=1"
QUEUED = f"{T}48,291 - INFO - Generation queued: request=7466ecbc50 prompt_tokens=4463 max_tokens=32768 images=0 audio=0 videos=0"
TTFT = f"{T}50,695 - INFO - Decode started: request=7466ecbc50 time_to_first_token=2.415s"
FAILED = f"{T}51,192 - WARNING - Request failed: endpoint=/chat/completions model=/Volumes/Models/models/active stream=True error=stream_closed_before_completion in_flight=0"
CANCELLED = f"{T}51,197 - INFO - Generation cancelled: request=7466ecbc50 generated_tokens=47"


def test_parses_generation_cancelled_and_pre_queue_errors():
    ev = parse_perf_line(CANCELLED)
    assert ev is not None and ev["kind"] == "req_cancel"
    assert ev["data"] == {"rid": "7466ecbc50", "generated_tokens": 47}
    endpoint = parse_perf_line(
        "2026-09-29 22:09:41,395 - ERROR - Unexpected error in /chat/completions endpoint:"
        " Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low."
    )
    assert endpoint is not None and endpoint["kind"] == "req_rejected"
    assert endpoint["data"] == {"error": "endpoint_error"}  # never the free-text message
    load = parse_perf_line(
        "2026-09-29 20:45:06,231 - ERROR - Error loading model active: Cannot find an"
        " appropriate cached snapshot folder for the specified revision on the local disk"
    )
    assert load is not None and load["kind"] == "req_rejected"
    assert load["data"] == {"error": "model_load_failed"}


@pytest.mark.parametrize(
    "line",
    [
        # the second line of the same load failure
        "2026-09-29 20:45:06,240 - ERROR - Error loading model in generation thread: 400:"
        " Failed to load model: Cannot find an appropriate cached snapshot folder",
        # the server loading its own weights at startup is not a client request
        "2026-09-29 20:45:06,231 - ERROR - Error loading model /Volumes/Models/models/active: boom",
        "2026-09-29 20:45:06,231 - INFO - Loading model: active",
    ],
)
def test_parser_ignores_non_request_load_lines(line):
    assert parse_perf_line(line) is None


def test_cancel_after_the_failure_line_adds_the_generated_tokens():
    state, res = _run([STARTED, QUEUED, TTFT, FAILED, CANCELLED])
    assert [r["state"] for r in res.rows] == ["cancelled"]
    assert res.rows[0]["generated_tokens"] is None  # closed by the failure line first
    assert res.updates == [{"key": res.rows[0]["key"], "fields": {"generated_tokens": 47}}]
    assert state.open == {}


def test_cancel_by_id_resolves_overlapping_requests_without_double_counting():
    other = f"{T}48,500 - INFO - Generation queued: request=bbb prompt_tokens=10"
    state, res = _run([QUEUED, other, FAILED, CANCELLED])
    assert [(r["request_id"], r["state"], r["generated_tokens"]) for r in res.rows] == [
        ("7466ecbc50", "cancelled", 47)
    ]
    assert set(state.open) == {"bbb"} and state.pending_cancels == []


def test_cancel_without_a_failure_line_is_a_server_side_cancel():
    _, res = _run([QUEUED, CANCELLED])
    assert [(r["state"], r["error"], r["generated_tokens"]) for r in res.rows] == [
        ("cancelled", "generation_cancelled", 47)
    ]


def test_unmatched_cancel_failure_expires_without_a_row():
    other = f"{T}48,500 - INFO - Generation queued: request=bbb prompt_tokens=10"
    state, res = _run(
        [
            QUEUED,
            other,
            FAILED,
            "2026-09-29 22:00:00,000 - INFO - Generation queued: request=ccc prompt_tokens=1",
        ]
    )
    # the two old requests went stale; the failure never became a third, guessed row
    assert sorted(r["request_id"] for r in res.rows) == ["7466ecbc50", "bbb"]
    assert {r["state"] for r in res.rows} == {"incomplete"}
    assert state.pending_cancels == []


def test_pre_queue_rejection_is_one_standalone_error_and_never_closes_a_running_request():
    lines = [
        QUEUED,
        TTFT,
        "2026-09-29 20:47:52,000 - ERROR - Unexpected error in /chat/completions endpoint:"
        " Unexpected reasoning effort high.",
        "2026-09-29 20:47:53,000 - ERROR - Error loading model qwen3-coder: Cannot find it",
        "2026-09-29 20:47:53,010 - ERROR - Error loading model in generation thread: 400: x",
    ]
    state, res = _run(["===== 2026-09-29 20:00:00 start a =====", *lines])
    assert [(r["state"], r["error"], r["request_id"], r["model"]) for r in res.rows] == [
        ("error", "endpoint_error", None, "a"),
        ("error", "model_load_failed", None, "a"),
    ]
    assert set(state.open) == {"7466ecbc50"}  # the running request is untouched


def test_pre_queue_rejection_consumes_its_single_start():
    _, res = _run(
        [
            STARTED,
            "2026-09-29 20:47:48,300 - ERROR - Unexpected error in /chat/completions endpoint: x",
        ]
    )
    row = res.rows[0]
    assert row["stream"] == 1 and row["started"] < row["finished"]


def test_state_from_before_the_new_fields_still_loads():
    old = '{"generation": null, "open": {}, "pending_starts": [], "awaiting": []}'
    assert PerfState.from_json(old).pending_cancels == []


def test_non_cancel_failure_of_the_single_open_request_is_an_error_without_a_follow_up():
    boom = FAILED.replace("stream_closed_before_completion", "boom")
    state, res = _run([QUEUED, boom, CANCELLED])
    assert [(r["state"], r["error"]) for r in res.rows] == [("error", "boom")]
    assert res.updates == [] and state.recent_cancelled == []


def test_cancel_for_an_unknown_id_changes_nothing():
    state, res = _run([CANCELLED])
    assert res.rows == [] and res.updates == [] and state.open == {}
