import os

import pytest

from local_llm_admin.ingestion import (
    Ingestor,
    IngestStore,
    error_hint,
    normalise_error,
    parse_download_line,
    parse_mlx_line,
)

NOW = 1_790_000_000.0  # 2026-09-21


def _raw(_unused):
    """A parser that turns every line into an event (for cursor tests)."""
    return lambda line: {"kind": "line", "message": line, "ts": None}


@pytest.fixture
def store(tmp_path):
    return IngestStore(tmp_path / "admin.sqlite")


def _ing(store, path, **kw):
    kw.setdefault("now", lambda: NOW)
    return Ingestor(store, "src", path, _raw(None), **kw)


def _msgs(result):
    return [e["message"] for e in result.events]


def test_reads_complete_lines_and_keeps_partial_ones(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"one\ntw")
    ing = _ing(store, p, backfill_bytes=0)
    assert _msgs(ing.poll()) == []  # baseline at EOF on first sight
    with p.open("ab") as fh:
        fh.write(b"o\nthree\nfo")
    assert _msgs(ing.poll()) == ["three"]  # the line cut by the baseline is skipped
    with p.open("ab") as fh:
        fh.write(b"ur\n")
    r = ing.poll()
    assert _msgs(r) == ["four"] and r.caught_up and r.ok


def test_first_sight_backfill_is_bounded_and_skips_the_cut_line(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"x" * 100 + b"\nold\nnew\n")
    assert _msgs(_ing(store, p, backfill_bytes=5).poll()) == ["new"]
    q = tmp_path / "b.log"
    q.write_bytes(b"x" * 100 + b"\nold\nnew\n")
    assert _msgs(Ingestor(store, "b", q, _raw(None), now=lambda: NOW, backfill_bytes=8).poll()) == [
        "old",
        "new",
    ]  # starting right after a newline loses nothing


def test_multibyte_character_split_across_polls(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()
    data = "héllo ✓\n".encode()
    with p.open("ab") as fh:
        fh.write(data[:2])
    assert _msgs(ing.poll()) == []
    with p.open("ab") as fh:
        fh.write(data[2:])
    assert _msgs(ing.poll()) == ["héllo ✓"]


def test_restart_resumes_without_duplicates(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    _ing(store, p).poll()
    p.write_bytes(b"a\nb\n")
    assert _msgs(_ing(store, p).poll()) == ["a", "b"]
    with p.open("ab") as fh:
        fh.write(b"c\n")
    assert _msgs(_ing(store, p).poll()) == ["c"]  # a new Ingestor, as after a restart


def test_batches_are_bounded(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, batch_bytes=4)
    ing.poll()
    p.write_bytes(b"aa\nbb\ncc\n")
    first = ing.poll()
    assert _msgs(first) == ["aa"] and not first.caught_up
    assert _msgs(ing.poll()) == ["bb"]
    assert _msgs(ing.poll()) == ["cc"]
    assert ing.poll().caught_up


def test_overlong_line_is_truncated_not_buffered_forever(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, max_line=8, batch_bytes=4)
    ing.poll()
    p.write_bytes(b"0123456789abcdef\nok\n")
    out = []
    for _ in range(10):
        out += _msgs(ing.poll())
    assert out == ["01234567…", "ok"]


def test_truncation_restarts_from_zero_and_records_a_gap(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()
    p.write_bytes(b"a\nb\n")
    ing.poll()
    p.write_bytes(b"c\n")  # copy-truncate then a shorter regrow
    r = ing.poll()
    assert _msgs(r) == ["c"] and store.cursor("src")["gap_at"] == NOW


def test_rename_rotation_follows_the_new_file(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()
    p.write_bytes(b"a\n")
    ing.poll()
    p.rename(tmp_path / "a.log.1")
    p.write_bytes(b"b\n")
    assert _msgs(ing.poll()) == ["b"]
    assert store.cursor("src")["gap_at"] == NOW


def test_missing_file_is_not_ok(store, tmp_path):
    r = _ing(store, tmp_path / "missing.log").poll()
    assert not r.ok and r.events == []


def test_replayed_offsets_are_ignored(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()
    p.write_bytes(b"a\n")
    assert len(ing.poll().events) == 1
    store.set_cursor_offset("src", 0)  # simulate a cursor write lost after the events
    assert ing.poll().events == []
    assert len(store.events(kinds=("line",))) == 1


def test_event_and_cursor_commit_together(store, tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()
    p.write_bytes(b"a\n")

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save_cursor", boom)
    with pytest.raises(OSError):
        ing.poll()
    monkeypatch.undo()
    assert store.events(kinds=("line",)) == []  # rolled back with the cursor
    assert _msgs(ing.poll()) == ["a"]


def test_events_older_than_max_age_are_dropped(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = Ingestor(
        store,
        "src",
        p,
        lambda line: {"kind": "line", "message": line, "ts": float(line)},
        now=lambda: NOW,
        max_age_s=60,
    )
    p.write_bytes(f"{NOW - 120}\n{NOW - 30}\n".encode())
    assert [e["ts"] for e in ing.poll().events] == [NOW - 30]  # backfill is age-limited
    with p.open("ab") as fh:
        fh.write(f"{NOW - 999}\n".encode())
    assert [e["ts"] for e in ing.poll().events] == [NOW - 999]  # later lines are not


# --- parsers -------------------------------------------------------------------


def test_parse_mlx_error_lines():
    ev = parse_mlx_line(
        "2026-09-28 22:33:31,308 - WARNING - Request failed: endpoint=/chat/completions"
        " model=/Volumes/Models/models/active stream=True error=stream_closed_before_completion"
        " in_flight=0"
    )
    assert ev["kind"] == "error" and ev["ts"] is not None
    assert "error=stream_closed_before_completion" in ev["message"]
    assert parse_mlx_line("2026-09-28 22:41:58,429 - INFO - Request started: x") is None
    assert parse_mlx_line("2026-09-28 22:41:58,429 - WARNING - slow tokenizer") is None
    assert parse_mlx_line("2026-09-28 22:41:58,429 - ERROR - boom")["kind"] == "error"
    tail = parse_mlx_line("ValueError: Unexpected reasoning effort 'max'")
    assert tail["kind"] == "error" and tail["ts"] is None


def test_parse_mlx_access_5xx_and_shutdown_markers():
    ev = parse_mlx_line(
        'INFO:     192.0.2.10:42078 - "POST /v1/chat/completions HTTP/1.1" 500 Internal'
    )
    assert ev["kind"] == "error" and ev["data"]["client"] == "192.0.2.10"
    assert (
        parse_mlx_line('INFO:     1.2.3.4:1 - "POST /v1/chat/completions HTTP/1.1" 200 OK') is None
    )
    down = parse_mlx_line("INFO:     Finished server process [7540]")
    assert down == {
        "kind": "shutdown",
        "message": "Finished server process",
        "ts": None,
        "data": {"pid": 7540},
    }
    up = parse_mlx_line("INFO:     Started server process [7541]")
    assert up["kind"] == "startup" and up["data"]["pid"] == 7541


def test_normalise_groups_unstable_parts_and_redacts():
    a = normalise_error("Request failed: request=7358719640 took 12.5s at /Users/you/x.py")
    b = normalise_error("Request failed: request=735875ffb0 took 3s at /Users/other/x.py")
    assert a == b and "/Users/<user>/" in a
    keep = normalise_error("Error loading model /Volumes/Models/models/Qwen3.8-27B-4bit")
    assert "Qwen3.8-27B-4bit" in keep
    red = normalise_error('bad key sk-abcdefghijkl Bearer tok123 {"content": "my secret prompt"}')
    assert "abcdefghijkl" not in red and "tok123" not in red and "secret prompt" not in red


def test_error_hints():
    assert "reasoning_effort" in error_hint("ValueError: Unexpected reasoning effort 'max'")
    hint = error_hint("Error loading model gpt-4o")
    assert "gpt-4o" in hint and "openai//Volumes/Models/models/active" in hint
    assert error_hint("something else") is None


def test_parse_download_markers():
    done = parse_download_line("===== 2026-09-28 23:10:00 done fail=0 =====")
    assert done["kind"] == "download_done" and done["data"]["failed"] == 0
    failed = parse_download_line("===== 2026-09-28 23:10:00 done fail=2 =====")
    assert failed["data"]["failed"] == 2
    assert parse_download_line("GET x/y") is None


def test_fixture_log_parses(store, tmp_path):
    fix = os.path.join(os.path.dirname(__file__), "fixtures/mac/mlx-server.log")
    lines = open(fix, encoding="utf-8").read().splitlines()
    kinds = [e["kind"] for e in map(parse_mlx_line, lines) if e]
    assert kinds.count("error") == 1


def _stamped(line):
    return float(line[3:]) if line.startswith("ts=") else None


def test_backfill_dates_unstamped_lines_from_the_previous_stamp(store, tmp_path):
    """uvicorn and traceback lines have no timestamp; old ones must not look current."""
    p = tmp_path / "a.log"
    p.write_bytes(
        b"orphan without any earlier stamp\n"
        + f"ts={NOW - 7200}\nold error\nts={NOW - 60}\nrecent error\n".encode()
    )
    ing = Ingestor(
        store,
        "src",
        p,
        lambda line: {"kind": "line", "message": line, "ts": None} if "error" in line else None,
        line_ts=_stamped,
        max_age_s=3600,
        now=lambda: NOW,
    )
    got = ing.poll().events
    assert [(e["message"], e["ts"]) for e in got] == [("recent error", NOW - 60)]
    with p.open("ab") as fh:
        fh.write(f"ts={NOW - 7200}\nlive error\n".encode())
    (live,) = ing.poll().events
    assert live["ts"] == NOW  # written after the baseline: observed now


def test_mlx_line_timestamp():
    from local_llm_admin.ingestion import mlx_line_ts

    assert mlx_line_ts("2026-09-28 22:33:27,361 - INFO - x") is not None
    assert mlx_line_ts('INFO:     1.2.3.4:1 - "POST / HTTP/1.1" 500 x') is None


# --- M4: sink in the cursor transaction, and draining a renamed log ---------------


def _sinking(store, p, calls, **kw):
    def sink(conn, events, gap):
        calls.append(([e["message"] for e in events], gap))
        conn.execute(
            "INSERT INTO kv (key, value, updated) VALUES ('sunk', '1', 0)"
            " ON CONFLICT(key) DO UPDATE SET value = value + 1"
        )

    return _ing(store, p, sink=sink, store_events=False, **kw)


def test_sink_runs_in_the_cursor_transaction_without_storing_events(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    calls: list = []
    ing = _sinking(store, p, calls)
    ing.poll()
    p.write_bytes(b"a\nb\n")
    r = ing.poll()
    assert _msgs(r) == ["a", "b"] and calls[-1] == (["a", "b"], False)
    assert store.events(kinds=("line",)) == []  # not written to log_events
    assert store.get_kv("sunk") == 2


def test_sink_failure_rolls_back_the_cursor(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    fail = [True]

    def sink(conn, events, gap):
        if not events:
            return
        conn.execute("INSERT INTO kv (key, value, updated) VALUES ('x', '1', 0)")
        if fail[0]:
            raise RuntimeError("sink failed")

    ing = _ing(store, p, sink=sink, store_events=False)
    ing.poll()
    p.write_bytes(b"a\n")
    with pytest.raises(RuntimeError):
        ing.poll()
    assert store.get_kv("x") is None
    fail[0] = False
    assert _msgs(ing.poll()) == ["a"]  # the same line again, nothing skipped


def test_rename_rotation_drains_the_archived_tail_first(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    calls: list = []
    ing = _sinking(store, p, calls, archives=lambda: sorted(tmp_path.glob("a.log.*")))
    ing.poll()
    p.write_bytes(b"a\n")
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"unread\nlast-no-newline")  # written before the writer stopped
    p.rename(tmp_path / "a.log.20261001-030000")
    p.write_bytes(b"b\n")
    first = ing.poll()
    assert _msgs(first) == ["unread", "last-no-newline"] and not first.caught_up
    second = ing.poll()
    assert _msgs(second) == ["b"] and second.caught_up
    assert all(gap is False for _, gap in calls)
    assert store.cursor("src")["gap_at"] is None


def test_drain_is_bounded_by_the_batch_size(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, batch_bytes=4, archives=lambda: sorted(tmp_path.glob("a.log.*")))
    ing.poll()
    p.write_bytes(b"aa\nbb\ncc\n")
    p.rename(tmp_path / "a.log.1")
    p.write_bytes(b"dd\n")
    seen = []
    for _ in range(10):
        seen += _msgs(ing.poll())
    assert seen == ["aa", "bb", "cc", "dd"]


def test_rotation_without_the_archive_is_a_gap_for_the_sink(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    calls: list = []
    ing = _sinking(store, p, calls, archives=lambda: [])
    ing.poll()
    p.write_bytes(b"a\n")
    ing.poll()
    p.rename(tmp_path / "gone.log")
    p.write_bytes(b"b\n")
    ing.poll()
    assert calls[-1] == (["b"], True)
    assert store.cursor("src")["gap_at"] == NOW


def test_truncation_is_a_gap_for_the_sink(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    calls: list = []
    ing = _sinking(store, p, calls)
    ing.poll()
    p.write_bytes(b"a\nb\n")
    ing.poll()
    p.write_bytes(b"c\n")
    ing.poll()
    assert calls[-1] == (["c"], True)


def test_host_logs_mlx_source_drains_a_rotated_log_without_a_gap(tmp_path):
    from local_llm_admin.config import Settings
    from local_llm_admin.ingestion import HostLogs

    settings = Settings(mlx_root=tmp_path / "Models", admin_data_dir=tmp_path / "data")
    settings.mlx_log_dir.mkdir(parents=True)
    store = IngestStore(tmp_path / "data/admin.sqlite")
    log = settings.mlx_log_dir / "mlx-server.log"
    log.write_bytes(b"")
    host = HostLogs(settings, store, now=lambda: NOW)
    host.poll_all()
    with log.open("ab") as fh:
        fh.write(b"INFO:     Finished server process [7]\n")
    log.rename(log.with_name("mlx-server.log.20261001-030000"))
    log.write_bytes(b"")
    host.poll_all()
    host.poll_all()
    assert store.cursor("mlx-server")["gap_at"] is None
    assert [e["data"]["pid"] for e in store.events(kinds=("shutdown",))] == [7]


def test_archive_lookup_skips_unreadable_and_unrelated_files(store, tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    other = tmp_path / "a.log.20260101-000000"
    other.write_bytes(b"unrelated\n")
    missing = tmp_path / "a.log.20260102-000000"
    ing = _ing(store, p, archives=lambda: [missing, other])
    ing.poll()
    p.write_bytes(b"a\n")
    ing.poll()
    p.rename(tmp_path / "moved")
    p.write_bytes(b"b\n")
    assert _msgs(ing.poll()) == ["b"]
    assert store.cursor("src")["gap_at"] == NOW  # the old file was not among the archives


class _Remount:
    """Makes ``os.fstat`` report another device number, as after the model drive is
    unmounted and mounted again (macOS assigns a new st_dev; the inode is kept)."""

    def __init__(self, monkeypatch, shift=5):
        from local_llm_admin import ingestion

        real = os.fstat
        self.shift = shift

        def fstat(fd):
            st = real(fd)
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + self.shift, *tuple(st)[3:]))

        monkeypatch.setattr(ingestion.os, "fstat", fstat)


def test_remount_with_a_new_device_number_is_the_same_file(store, tmp_path, monkeypatch):
    """A drive remount must not replay the log under a new identity (2026-10-01)."""
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: [])
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\ntwo\n")
    first = ing.poll()
    before = store.cursor("src")
    _Remount(monkeypatch)
    with p.open("ab") as fh:
        fh.write(b"three\n")
    r = ing.poll()
    assert _msgs(r) == ["three"]  # nothing replayed
    cur = store.cursor("src")
    assert cur["gap_at"] is None and (cur["dev"], cur["inode"]) == (before["dev"], before["inode"])
    # the identity (and so every dedup key) is unchanged
    assert r.events[0]["dedup"].rsplit(":", 1)[0] == first.events[0]["dedup"].rsplit(":", 1)[0]
    assert _msgs(ing.poll()) == []


def test_remount_of_a_short_file_uses_the_grown_fingerprint(store, tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p)
    ing.poll()  # empty file: no fingerprint yet
    with p.open("ab") as fh:
        fh.write(b"x\n")
    ing.poll()
    assert store.cursor("src")["head"].startswith("2:")
    with p.open("ab") as fh:
        fh.write(b"y" * 5000 + b"\n")
    ing.poll()
    assert store.cursor("src")["head"].startswith("4096:")
    _Remount(monkeypatch)
    with p.open("ab") as fh:
        fh.write(b"z\n")
    assert _msgs(ing.poll()) == ["z"]


def test_new_device_and_different_content_is_a_new_file(store, tmp_path, monkeypatch):
    """Same inode number on another device with other content: not the same file."""
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: [])
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\n")
    ing.poll()
    _Remount(monkeypatch)
    p.write_bytes(b"ONE\nnew\n")  # rewritten in place: same inode, other head
    assert _msgs(ing.poll()) == ["ONE", "new"]
    assert store.cursor("src")["gap_at"] == NOW


def test_new_device_and_shrunk_file_is_a_new_file(store, tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: [])
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\ntwo\n")
    ing.poll()
    _Remount(monkeypatch)
    p.write_bytes(b"one\n")  # same head, but shorter than the cursor
    assert _msgs(ing.poll()) == ["one"]


def test_cursor_without_a_fingerprint_gets_one(store, tmp_path, monkeypatch):
    """Cursors saved before schema 9 have no fingerprint; the next poll records it,
    so a later remount is recognised."""
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: [])
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\n")
    ing.poll()
    with store._conn() as c:
        c.execute("UPDATE log_cursors SET head = NULL")
    ing.poll()
    assert store.cursor("src")["head"].startswith("4:")
    _Remount(monkeypatch)
    with p.open("ab") as fh:
        fh.write(b"two\n")
    assert _msgs(ing.poll()) == ["two"]


def test_remount_without_a_fingerprint_is_still_a_new_file(store, tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: [])
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\n")
    ing.poll()
    with store._conn() as c:
        c.execute("UPDATE log_cursors SET head = NULL")
    _Remount(monkeypatch)
    assert _msgs(ing.poll()) == ["one"]  # no evidence it is the same file: old behavior


def test_remounted_archive_is_still_drained(store, tmp_path, monkeypatch):
    p = tmp_path / "a.log"
    p.write_bytes(b"")
    ing = _ing(store, p, archives=lambda: sorted(tmp_path.glob("a.log.*")))
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"one\n")
    ing.poll()
    with p.open("ab") as fh:
        fh.write(b"tail\n")
    p.rename(tmp_path / "a.log.20261001-010000")
    p.write_bytes(b"fresh\n")
    _Remount(monkeypatch)
    assert _msgs(ing.poll()) == ["tail"]
    assert _msgs(ing.poll()) == ["fresh"]
    assert store.cursor("src")["gap_at"] is None


def test_file_head_helpers(tmp_path):
    from local_llm_admin.ingestion import HEAD_BYTES, file_head, head_matches

    p = tmp_path / "a.log"
    p.write_bytes(b"abc")
    with p.open("rb") as fh:
        fd = fh.fileno()
        assert file_head(fd, 0) is None
        head = file_head(fd, 3)
        assert head is not None and head.startswith("3:")
        assert head_matches(fd, 3, head)
        for bad in (None, "", "x:y", "0:abc", "9:abc", head.replace("3:", "2:")):
            assert not head_matches(fd, 3, bad)
        assert not head_matches(fd, 2, head)  # shorter than the fingerprinted bytes
    assert HEAD_BYTES == 4096
