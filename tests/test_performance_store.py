"""Durable request rows: the ingestion sink, replay, restart, rotation and queries (M4 §1)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.ingestion import IngestStore
from local_llm_admin.performance_store import (
    PERF_SOURCE,
    PerfLogs,
    PerformanceStore,
    archive_paths,
    percentile,
)

FIX = Path(__file__).parent / "fixtures"
MAC_LOG = (FIX / "mac/mlx-server.log").read_bytes()
# the same log without its "===== ... start ... =====" generation marker
NO_MARKER = b"".join(ln for ln in MAC_LOG.splitlines(keepends=True) if not ln.startswith(b"====="))
NOW = datetime(2026, 9, 29, 12, 0, 0).timestamp()
COLS = ("request_id", "state", "prompt_tokens", "generated_tokens", "elapsed_s", "model")


@pytest.fixture
def env(tmp_path):
    settings = Settings(mlx_root=tmp_path / "Models", admin_data_dir=tmp_path / "data")
    settings.mlx_log_dir.mkdir(parents=True)
    db = tmp_path / "data/admin.sqlite"
    ingest, perf = IngestStore(db), PerformanceStore(db)
    return settings, ingest, perf


def _logs(env, **kw):
    settings, ingest, perf = env
    return PerfLogs(settings, ingest, perf, now=lambda: NOW, **kw)


def _log(env) -> Path:
    return env[0].mlx_log_dir / "mlx-server.log"


def _rows(perf):
    return [tuple(r[c] for c in COLS) for r in perf.requests(0, 2e9, limit=100)["requests"]][::-1]


def _drain(logs, n=50):
    for _ in range(n):
        r = logs.poll()
        if r.caught_up:
            return r
    raise AssertionError("did not catch up")


def test_recorded_log_becomes_request_rows(env):
    _log(env).write_bytes(MAC_LOG)
    _drain(_logs(env))
    assert _rows(env[2]) == [
        ("7358719640", "cancelled", 2459, None, None, "qwen3.8-27b"),
        ("735875ffb0", "completed", 17, 2, 0.55, "qwen3.8-27b"),
        ("735875ec00", "completed", 2185, 10, 6.224, "qwen3.8-27b"),
    ]
    assert env[1].events(kinds=("req_queued",)) == []  # request lines never fill log_events


def test_split_writes_and_partial_utf8_give_the_same_rows(env):
    log = _log(env)
    log.write_bytes(b"")
    logs = _logs(env, batch_bytes=97)
    logs.poll()
    data = MAC_LOG.replace(b"stream=True", "stream=True é中".encode())
    for i in range(0, len(data), 61):  # cuts through lines and multibyte characters
        with log.open("ab") as fh:
            fh.write(data[i : i + 61])
        logs.poll()
    _drain(logs)
    assert [r[:5] for r in _rows(env[2])] == [
        ("7358719640", "cancelled", 2459, None, None),
        ("735875ffb0", "completed", 17, 2, 0.55),
        ("735875ec00", "completed", 2185, 10, 6.224),
    ]


def test_replay_is_idempotent(env):
    settings, ingest, perf = env
    _log(env).write_bytes(MAC_LOG)
    _drain(_logs(env))
    first = _rows(perf)
    # lose the cursor and the correlation state: replay from the start of the file
    with ingest._conn() as c:
        c.execute("DELETE FROM log_cursors")
        c.execute("DELETE FROM kv")
    _drain(_logs(env))
    assert _rows(perf) == first


def test_restart_mid_request_resumes_from_persisted_state(env):
    log = _log(env)
    lines = MAC_LOG.splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:15]))  # stops inside the second request
    _drain(_logs(env))
    assert [r[1] for r in _rows(env[2])] == ["cancelled"]
    with log.open("ab") as fh:
        fh.write(b"".join(lines[15:]))
    _drain(_logs(env))  # a new process: fresh objects, same database
    assert [r[1] for r in _rows(env[2])] == ["cancelled", "completed", "completed"]


def test_rotation_after_the_writer_stops_drains_the_tail(env):
    log = _log(env)
    lines = MAC_LOG.splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:15]))
    logs = _logs(env)
    _drain(logs)
    with log.open("ab") as fh:
        fh.write(b"".join(lines[15:]))  # written, then the server stops unread
    log.rename(log.with_name("mlx-server.log.20261001-030000"))
    log.write_bytes(b"===== 2026-10-01 03:00:01 start other =====\n")
    _drain(logs)
    rows = _rows(env[2])
    assert [r[1] for r in rows] == ["cancelled", "completed", "completed"]
    assert env[2].state(PERF_SOURCE).generation["name"] == "other"


def test_rotation_gap_makes_open_requests_incomplete(env):
    log = _log(env)
    lines = MAC_LOG.splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:15]))
    logs = _logs(env)
    _drain(logs)
    log.rename(log.with_name("elsewhere.log"))  # not an archive name: tail unreachable
    log.write_bytes(b"")
    _drain(logs)
    assert [r[1] for r in _rows(env[2])] == ["cancelled", "incomplete"]
    assert env[2].state(PERF_SOURCE).generation is None


def test_first_sight_seeds_the_generation_from_before_the_backfill_window(env):
    log = _log(env)
    body = NO_MARKER
    filler = b"INFO:     noise\n" * 200
    log.write_bytes(b"===== 2026-09-28 22:06:52 start seeded =====\n" + filler + body)
    _drain(_logs(env, backfill_bytes=len(body) + 10))
    assert {r[5] for r in _rows(env[2])} == {"seeded"}


def test_first_sight_without_a_marker_is_unknown(env):
    _log(env).write_bytes(NO_MARKER)
    _drain(_logs(env))
    assert {r[5] for r in _rows(env[2])} == {None}
    assert {r["attribution"] for r in env[2].requests(0, 2e9)["requests"]} == {"unknown"}


def test_missing_log_is_not_ok(env):
    assert not _logs(env).poll().ok


def test_archive_paths_are_the_rotated_names_only(tmp_path):
    for name in (
        "mlx-server.log",
        "mlx-server.log.20261001-030000",
        "mlx-server.log.x",
        "mlx-server.log.20260901-030000",
        "other.log.20261001-030000",
    ):
        (tmp_path / name).write_text("")
    os.symlink(tmp_path / "mlx-server.log", tmp_path / "mlx-server.log.20261002-030000")
    assert [p.name for p in archive_paths(tmp_path, "mlx-server.log")] == [
        "mlx-server.log.20261001-030000",
        "mlx-server.log.20260901-030000",
    ]
    assert archive_paths(tmp_path / "missing", "mlx-server.log") == []


# --- queries -------------------------------------------------------------------


def _row(i, **kw):
    base = {
        "key": f"k{i}",
        "request_id": f"r{i}",
        "started": NOW + i,
        "finished": NOW + i + 1,
        "model": "a",
        "attribution": "generation",
        "stream": 1,
        "images": 0,
        "prompt_tokens": 10,
        "cached_tokens": 0,
        "generated_tokens": 5,
        "prefill_s": 0.1,
        "prefill_tok_s": 100.0,
        "ttft_s": 1.0,
        "decode_s": 0.5,
        "decode_tok_s": 10.0,
        "elapsed_s": 1.5,
        "finish_reason": "stop",
        "state": "completed",
        "error": None,
        "complete": 1,
        "log_identity": "1:2:0",
        "log_start": 0,
        "log_end": 10,
    }
    return {**base, **kw}


def test_percentile_uses_nearest_rank_and_handles_empty():
    assert percentile([], 0.5) is None
    assert percentile([3.0], 0.9) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile([float(i) for i in range(1, 11)], 0.9) == 9.0


def test_summary_buckets_by_ops_day_and_reports_measured_only(env):
    perf = env[2]
    day = 86400
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, ttft_s=1.0, decode_tok_s=10.0),
            _row(2, ttft_s=3.0, decode_tok_s=20.0),
            _row(3, ttft_s=None, decode_tok_s=None, complete=0),
            _row(4, state="error", error="boom", ttft_s=None, decode_tok_s=None, complete=0),
            _row(5, state="cancelled", ttft_s=30.0),
            _row(6, started=NOW + day, model="b", ttft_s=2.0),
        ],
    )
    out = perf.summary(NOW - 10, NOW + 2 * day, tz_offset_min=-480, slow_ttft_s=10.0)
    first, second = out["days"]
    assert first["requests"] == 5 and first["errors"] == 1 and first["cancelled"] == 1
    assert first["partial"] == 2 and first["slow"] == 1
    assert first["ttft_p50"] == 3.0 and first["ttft_p90"] == 30.0
    assert first["decode_p50"] == 10.0
    assert second["requests"] == 1 and second["models"] == ["b"]
    assert out["totals"]["requests"] == 6
    only_b = perf.summary(NOW - 10, NOW + 2 * day, tz_offset_min=-480, model="b")
    assert only_b["totals"]["requests"] == 1


def test_summary_of_nothing_has_no_percentiles(env):
    out = env[2].summary(NOW, NOW + 86400, tz_offset_min=-480)
    assert out["totals"]["requests"] == 0 and out["totals"]["ttft_p50"] is None


def test_requests_page_filters_and_paginates(env):
    perf = env[2]
    perf.insert_rows(
        PERF_SOURCE, [_row(i, state="error" if i % 2 else "completed") for i in range(10)]
    )
    page = perf.requests(0, 2e9, limit=3)
    assert [r["request_id"] for r in page["requests"]] == ["r9", "r8", "r7"]
    nxt = perf.requests(0, 2e9, limit=3, before=page["next"])
    assert [r["request_id"] for r in nxt["requests"]] == ["r6", "r5", "r4"]
    errs = perf.requests(0, 2e9, state="error", limit=100)
    assert {r["state"] for r in errs["requests"]} == {"error"} and errs["next"] is None
    assert perf.requests(0, 2e9, model="zzz")["requests"] == []
    assert perf.get(page["requests"][0]["id"])["request_id"] == "r9"
    assert perf.get(999) is None


def test_insert_is_unique_per_key(env):
    perf = env[2]
    assert perf.insert_rows(PERF_SOURCE, [_row(1)]) == 1
    assert perf.insert_rows(PERF_SOURCE, [_row(1)]) == 0


def test_prune_is_bounded(env):
    perf = env[2]
    perf.insert_rows(PERF_SOURCE, [_row(i, started=NOW - 100 * 86400 + i) for i in range(5)])
    perf.insert_rows(PERF_SOURCE, [_row(9)])
    assert perf.prune(NOW - 90 * 86400, limit=3) == 3
    assert perf.prune(NOW - 90 * 86400, limit=3) == 2
    assert len(perf.requests(0, 2e9)["requests"]) == 1


def test_last_ingest_time_comes_from_the_cursor(env):
    assert env[2].last_ingest(PERF_SOURCE) is None
    _log(env).write_bytes(MAC_LOG)
    _drain(_logs(env))
    assert env[2].last_ingest(PERF_SOURCE) == NOW


# --- seeding and context edges -----------------------------------------------------


def _seeded(env, head: bytes):
    _log(env).write_bytes(head + b"INFO:     noise\n" * 50 + NO_MARKER)
    _logs(env, backfill_bytes=len(NO_MARKER) + 10).poll()
    return env[2].state(PERF_SOURCE).generation


def test_seed_marker_then_its_process_then_a_markerless_restart(env):
    marker = b"===== 2026-09-28 22:06:52 start seeded =====\n"
    assert _seeded(env, marker + b"INFO:     Waiting for application startup.\n") is not None
    assert PerformanceStore(env[2].path).state(PERF_SOURCE).generation["name"] == "seeded"


def test_seed_forgets_the_marker_after_a_second_process_start(tmp_path):
    settings = Settings(mlx_root=tmp_path / "Models", admin_data_dir=tmp_path / "data")
    settings.mlx_log_dir.mkdir(parents=True)
    perf = PerformanceStore(tmp_path / "data/admin.sqlite")
    log = settings.mlx_log_dir / "mlx-server.log"
    log.write_bytes(
        b"===== 2026-09-28 22:06:52 start seeded =====\n"
        b"INFO:     Started server process [1]\n"
        b"INFO:     Started server process [2]\n" + b"x\n" * 100
    )
    perf.seed(PERF_SOURCE, log, 50)
    assert perf.state(PERF_SOURCE).generation is None
    perf.seed(PERF_SOURCE, settings.mlx_log_dir / "missing.log", 50)
    assert perf.state(PERF_SOURCE).generation is None


def test_context_edges(env):
    from local_llm_admin.performance_store import request_context

    settings = env[0]
    log = _log(env)
    log.write_bytes(b"first\nsecond-without-newline")
    st = log.stat()
    ident = f"{st.st_dev}:{st.st_ino}:0"
    row = {"log_identity": ident, "log_start": 6, "log_end": 6}
    assert request_context(settings.mlx_log_dir, row, None)["lines"] == ["second-without-newline"]
    assert not request_context(settings.mlx_log_dir, {**row, "log_identity": "junk"}, None)[
        "available"
    ]
    assert not request_context(settings.mlx_log_dir, {**row, "log_start": 999}, None)["available"]
    truncated = {"dev": st.st_dev, "inode": st.st_ino, "gap_at": 123.0}
    assert not request_context(settings.mlx_log_dir, row, truncated)["available"]
    log.unlink()
    assert not request_context(settings.mlx_log_dir, row, None)["available"]


def test_seed_scans_backwards_and_is_bounded(tmp_path, monkeypatch):
    import local_llm_admin.performance_store as ps

    settings = Settings(mlx_root=tmp_path / "Models", admin_data_dir=tmp_path / "data")
    settings.mlx_log_dir.mkdir(parents=True)
    perf = PerformanceStore(tmp_path / "data/admin.sqlite")
    log = settings.mlx_log_dir / "mlx-server.log"
    old = b"===== 2026-09-01 00:00:00 start old =====\n"
    marker = b"===== 2026-09-28 22:06:52 start near =====\nINFO:     Started server process [1]\n"
    filler = b"2026-09-28 22:07:00,000 - INFO - noise line for the seed scan\n" * 40000  # ~2.4 MiB
    log.write_bytes(old + filler + marker + filler + b"tail\n" * 10)
    perf.seed(PERF_SOURCE, log, 50)
    gen = perf.state(PERF_SOURCE).generation
    assert gen["name"] == "near" and gen["fresh"] is False
    monkeypatch.setattr(ps, "MAX_SEED_SCAN", 1024 * 1024)  # the marker is 2.4 MiB back
    perf.seed(PERF_SOURCE, log, 50)
    assert perf.state(PERF_SOURCE).generation is None


def test_prune_bounded_stops_after_its_batches(env):
    perf = env[2]
    perf.insert_rows(PERF_SOURCE, [_row(i, started=NOW - 100 * 86400 + i) for i in range(7)])
    assert perf.prune_bounded(NOW - 90 * 86400, batch=2, batches=2) == 4
    assert perf.prune_bounded(NOW - 90 * 86400, batch=2, batches=5) == 3


def test_live_cancel_and_rejection_shapes_reach_the_table(env):
    lines = [
        "===== 2026-09-29 20:00:00 start qwen3.6-35b-a3b =====",
        "2026-09-29 20:47:48,280 - INFO - Request started: endpoint=/chat/completions"
        " model=/Volumes/Models/models/active stream=True in_flight=1",
        "2026-09-29 20:47:48,291 - INFO - Generation queued: request=7466ecbc50"
        " prompt_tokens=4463 max_tokens=32768 images=0 audio=0 videos=0",
        "2026-09-29 20:47:51,192 - WARNING - Request failed: endpoint=/chat/completions"
        " model=/Volumes/Models/models/active stream=True"
        " error=stream_closed_before_completion in_flight=0",
        "2026-09-29 20:47:51,197 - INFO - Generation cancelled: request=7466ecbc50"
        " generated_tokens=47",
        "2026-09-29 22:09:41,395 - ERROR - Unexpected error in /chat/completions endpoint:"
        " Unexpected reasoning effort high.",
        "Traceback (most recent call last):",
        'INFO:     192.0.2.11:53020 - "POST /v1/chat/completions HTTP/1.1" 500 Internal'
        " Server Error",
    ]
    _log(env).write_text("\n".join(lines) + "\n")
    _drain(_logs(env))
    rows = env[2].requests(0, 2e9)["requests"][::-1]
    assert [(r["state"], r["error"], r["generated_tokens"]) for r in rows] == [
        ("cancelled", "stream_closed_before_completion", 47),
        ("error", "endpoint_error", None),
    ]
    assert {r["model"] for r in rows} == {"qwen3.6-35b-a3b"}


def test_request_context_after_a_remount(env, monkeypatch):
    """Rows keep the identity recorded before a remount; the live file is still found
    when the cursor's fingerprint matches it."""
    from local_llm_admin import performance_store
    from local_llm_admin.ingestion import file_head
    from local_llm_admin.performance_store import request_context

    settings = env[0]
    log = _log(env)
    log.write_bytes(b"first\nsecond\n")
    st = os.stat(log)
    row = {"log_identity": f"{st.st_dev}:{st.st_ino}:0", "log_start": 6, "log_end": 12}
    with log.open("rb") as fh:
        head = file_head(fh.fileno(), st.st_size)
    cursor = {"dev": st.st_dev, "inode": st.st_ino, "gap_at": None, "head": head}
    real = os.fstat
    monkeypatch.setattr(
        performance_store.os,
        "fstat",
        lambda fd: os.stat_result(
            (real(fd).st_mode, real(fd).st_ino, real(fd).st_dev + 5, *tuple(real(fd))[3:])
        ),
    )
    assert request_context(settings.mlx_log_dir, row, cursor)["lines"] == ["second"]
    # no fingerprint, or another file's: not found
    assert (
        request_context(settings.mlx_log_dir, row, {**cursor, "head": None})["available"] is False
    )
    assert (
        request_context(settings.mlx_log_dir, row, {**cursor, "head": "5:00"})["available"] is False
    )


# --- token usage (rollup, fold on prune, origin) ----------------------------------

DAY = 86400.0
TZ = -480  # UTC+8


def _local(ts: float) -> str:
    from local_llm_admin.jobs import ops_tz

    return datetime.fromtimestamp(ts, ops_tz(TZ)).date().isoformat()


def _sum_keys(row):
    keys = ("requests", "measured", "unmeasured", "cached_unknown")
    return {k: row[k] for k in (*keys, "input_tokens", "cached_tokens", "output_tokens")}


def test_tokens_of_an_empty_range_are_zero(env):
    out = env[2].tokens(NOW, NOW + DAY, tz_offset_min=TZ, group="day")
    assert out["rows"] == []
    assert _sum_keys(out["totals"]) == {
        "requests": 0,
        "measured": 0,
        "unmeasured": 0,
        "cached_unknown": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
    }


def test_tokens_add_up_measured_rows_and_count_the_rest(env):
    perf = env[2]
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, prompt_tokens=100, cached_tokens=40, generated_tokens=10),
            _row(2, prompt_tokens=50, cached_tokens=None, generated_tokens=5),
            # cancelled with partial output: counts what the engine logged
            _row(3, state="cancelled", prompt_tokens=30, cached_tokens=0, generated_tokens=2),
            # no counts at all: unmeasured, adds nothing
            _row(4, prompt_tokens=None, cached_tokens=None, generated_tokens=None, complete=0),
            # prompt only (cancelled before decode): unmeasured, its input still counts
            _row(5, state="cancelled", generated_tokens=None, prompt_tokens=7, complete=0),
        ],
    )
    t = perf.tokens(NOW - 10, NOW + DAY, tz_offset_min=TZ, group="day")["totals"]
    assert _sum_keys(t) == {
        "requests": 5,
        "measured": 3,
        "unmeasured": 2,
        "cached_unknown": 1,
        "input_tokens": 187,
        "cached_tokens": 40,
        "output_tokens": 17,
    }


def test_tokens_bucket_by_the_ops_day_and_group_by_model(env):
    perf = env[2]
    from local_llm_admin.jobs import ops_tz

    # the last second of an ops day (UTC+8); one second later is the next one
    edge = datetime(2026, 9, 29, 23, 59, 59, tzinfo=ops_tz(TZ)).timestamp()
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, started=edge, model="a", prompt_tokens=1, generated_tokens=1),
            _row(2, started=edge + 1, model="a", prompt_tokens=2, generated_tokens=2),
            _row(3, started=edge + 2, model="b", prompt_tokens=4, generated_tokens=4),
            _row(4, started=edge + 3, model=None, prompt_tokens=8, generated_tokens=8),
        ],
    )
    frm, to = edge - DAY, edge + DAY
    days = perf.tokens(frm, to, tz_offset_min=TZ, group="day")["rows"]
    assert [(d["date"], d["input_tokens"]) for d in days] == [
        (_local(edge), 1),
        (_local(edge + 1), 14),
    ]
    assert _local(edge) != _local(edge + 1)
    models = perf.tokens(frm, to, tz_offset_min=TZ, group="model")["rows"]
    assert [(m["model"], m["requests"], m["first"], m["last"]) for m in models] == [
        ("", 1, _local(edge + 3), _local(edge + 3)),
        ("a", 2, _local(edge), _local(edge + 1)),
        ("b", 1, _local(edge + 2), _local(edge + 2)),
    ]
    both = perf.tokens(frm, to, tz_offset_min=TZ, group="day_model")["rows"]
    assert [(r["date"], r["model"]) for r in both] == [
        (_local(edge), "a"),
        (_local(edge + 1), ""),
        (_local(edge + 1), "a"),
        (_local(edge + 1), "b"),
    ]
    only_b = perf.tokens(frm, to, tz_offset_min=TZ, group="day", model="b")
    assert only_b["totals"]["input_tokens"] == 4
    with pytest.raises(ValueError):
        perf.tokens(frm, to, tz_offset_min=TZ, group="week")  # type: ignore[arg-type]


def test_summary_carries_token_sums(env):
    perf = env[2]
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, prompt_tokens=10, cached_tokens=4, generated_tokens=3),
            _row(2, prompt_tokens=None, cached_tokens=None, generated_tokens=None),
        ],
    )
    out = perf.summary(NOW - 10, NOW + DAY, tz_offset_min=TZ)
    for part in (out["totals"], out["days"][0]):
        assert (part["input_tokens"], part["cached_tokens"], part["output_tokens"]) == (10, 4, 3)


def _usage(perf, frm=0.0, to=4e9, **kw):
    out = perf.tokens(frm, to, tz_offset_min=TZ, group="day_model", **kw)
    return [_sum_keys(r) | {"date": r["date"], "model": r["model"]} for r in out["rows"]]


def test_prune_folds_token_totals_so_nothing_is_lost(env):
    perf = env[2]
    old = NOW - 100 * DAY
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, started=old, prompt_tokens=100, cached_tokens=60, generated_tokens=9),
            _row(2, started=old + 60, prompt_tokens=20, cached_tokens=None, generated_tokens=1),
            _row(3, started=old + 120, prompt_tokens=None, generated_tokens=None),
            _row(4, started=old + 180, model=None, prompt_tokens=5, generated_tokens=5),
            _row(5, started=NOW, prompt_tokens=1, cached_tokens=0, generated_tokens=1),
        ],
    )
    before = _usage(perf)
    totals = perf.tokens(0, 4e9, tz_offset_min=TZ, group="model")
    assert perf.prune(NOW - 90 * DAY, limit=2, tz_offset_min=TZ) == 2
    assert _usage(perf) == before  # half the old day folded, half still live
    assert perf.prune(NOW - 90 * DAY, tz_offset_min=TZ) == 2
    assert perf.prune(NOW - 90 * DAY, tz_offset_min=TZ) == 0  # again: nothing to fold
    assert len(perf.requests(0, 4e9)["requests"]) == 1
    assert _usage(perf) == before
    after = perf.tokens(0, 4e9, tz_offset_min=TZ, group="model")
    assert after == totals  # first/last days survive too
    # the range still applies to folded days
    assert (
        perf.tokens(NOW - 10, NOW + DAY, tz_offset_min=TZ, group="day")["totals"]["requests"] == 1
    )


def test_prune_bounded_folds_with_the_ops_timezone(env):
    perf = env[2]
    old = NOW - 100 * DAY
    perf.insert_rows(PERF_SOURCE, [_row(i, started=old + i) for i in range(3)])
    before = _usage(perf)
    assert perf.prune_bounded(NOW - 90 * DAY, batch=2, batches=5, tz_offset_min=TZ) == 3
    assert _usage(perf) == before


def test_request_totals_keep_folded_tokens_and_add_cached(env):
    from local_llm_admin.inventory import InventoryStore

    perf = env[2]
    old = NOW - 100 * DAY
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(1, started=old, prompt_tokens=10, cached_tokens=4, generated_tokens=2),
            _row(2, started=NOW, prompt_tokens=1, cached_tokens=1, generated_tokens=1),
            _row(3, started=NOW + 1, model=None, attribution="unknown"),
        ],
    )
    inv = InventoryStore(perf.path)
    before = inv.request_totals()
    assert before == {
        "a": {
            "count": 2,
            "prompt_tokens": 11,
            "cached_tokens": 5,
            "generated_tokens": 3,
            "first": old,
            "last": NOW,
        }
    }
    perf.prune(NOW - 90 * DAY, tz_offset_min=TZ)
    assert inv.request_totals() == before
    assert inv.requests_since() == old


def _quality_run(perf, started, finished, run_id="q1"):
    import sqlite3

    with sqlite3.connect(perf.path) as c:
        c.execute(
            "INSERT INTO quality_runs (id, kind, scope, status, started, finished)"
            " VALUES (?, 'eval', 'one', ?, ?, ?)",
            (run_id, "running" if finished is None else "succeeded", started, finished),
        )


def test_rows_inside_a_quality_run_are_tagged_quality(env):
    perf = env[2]
    _quality_run(perf, NOW + 10, NOW + 20)
    _quality_run(perf, NOW + 100, None, run_id="open")
    perf.insert_rows(
        PERF_SOURCE,
        [
            _row(i, started=NOW + i)
            for i in (9, 10, 15, 20, 21, 99, 100, 500)  # edges inclusive; open run
        ],
    )
    got = {r["started"] - NOW: r["origin"] for r in perf.requests(0, 4e9, limit=100)["requests"]}
    assert got == {
        9: "client",
        10: "quality",
        15: "quality",
        20: "quality",
        21: "client",
        99: "client",
        100: "quality",
        500: "quality",
    }
    q = perf.tokens(0, 4e9, tz_offset_min=TZ, group="day", origin="quality")["totals"]
    c = perf.tokens(0, 4e9, tz_offset_min=TZ, group="day", origin="client")["totals"]
    assert (q["requests"], c["requests"]) == (5, 3)
    perf.prune(NOW + 1000, tz_offset_min=TZ)  # folded rows keep their origin
    q2 = perf.tokens(0, 4e9, tz_offset_min=TZ, group="day", origin="quality")["totals"]
    assert q2["requests"] == 5


def test_aggregate_rows_count_their_requests_but_skip_timing(env):
    perf = env[2]
    perf.insert_rows(
        "strata",
        [
            _row(1, ttft_s=1.0),
            _row(
                2,
                state="aggregate",
                requests=3,
                prompt_tokens=30,
                cached_tokens=0,
                generated_tokens=6,
                ttft_s=None,
                decode_tok_s=None,
            ),
        ],
    )
    t = perf.tokens(0, 4e9, tz_offset_min=TZ, group="day")["totals"]
    assert (t["requests"], t["measured"], t["input_tokens"]) == (4, 4, 40)
    s = perf.summary(0, 4e9, tz_offset_min=TZ)["totals"]
    assert s["requests"] == 1 and s["partial"] == 0
    assert [r["state"] for r in perf.requests(0, 4e9)["requests"]] == ["completed"]
