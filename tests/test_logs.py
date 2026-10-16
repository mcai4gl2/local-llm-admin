import os
from pathlib import Path

import pytest

from local_llm_admin import logs
from local_llm_admin.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(mlx_root=tmp_path / "Models", admin_log_dir=tmp_path / "adminlogs")


def test_resolve_source_maps_ids_to_fixed_paths(tmp_path):
    s = _settings(tmp_path)
    assert logs.resolve_source(s, "mlx-server") == tmp_path / "Models/logs/mlx-server.log"
    assert logs.resolve_source(s, "chat-ui") == tmp_path / "Models/logs/chat-qwen.log"
    assert logs.resolve_source(s, "serve") == tmp_path / "Models/logs/serve-qwen.log"
    assert logs.resolve_source(s, "admin") == tmp_path / "adminlogs/admin.log"


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "mlx-server.log", "", "ADMIN"])
def test_resolve_source_rejects_anything_not_allow_listed(tmp_path, bad):
    with pytest.raises(KeyError):
        logs.resolve_source(_settings(tmp_path), bad)


def test_normalise_line_keeps_last_curl_segment():
    assert logs.normalise_line("  1 100M\r 50 100M\r100 100M\n") == "100 100M"
    assert logs.normalise_line("plain\n") == "plain"
    assert logs.normalise_line("a\r\r") == "a"


def test_tail_lines_reads_only_the_end_in_small_blocks(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("".join(f"line {i}\n" for i in range(10_000)))
    assert logs.tail_lines(p, 3, block_size=16) == ["line 9997", "line 9998", "line 9999"]


def test_tail_lines_without_trailing_newline_and_short_file(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("a\nb\nc")
    assert logs.tail_lines(p, 10, block_size=2) == ["a", "b", "c"]
    assert logs.tail_lines(p, 0) == []


def test_tail_lines_missing_or_empty(tmp_path):
    assert logs.tail_lines(tmp_path / "nope.log", 5) == []
    (tmp_path / "e.log").write_text("")
    assert logs.tail_lines(tmp_path / "e.log", 5) == []


def test_tail_lines_normalises_carriage_returns(tmp_path):
    p = tmp_path / "d.log"
    p.write_bytes(b"start\n 10%\r 50%\r100%\n")
    assert logs.tail_lines(p, 5) == ["start", "100%"]


def test_filter_lines_by_grep_and_min_level():
    lines = [
        "2026 INFO Request started",
        "2026 WARNING slow",
        "2026 ERROR boom",
        "no level here",
        "2026 WARN legacy",
    ]
    assert logs.filter_lines(lines, grep="REQUEST", level=None) == ["2026 INFO Request started"]
    assert logs.filter_lines(lines, grep=None, level="WARNING") == [
        "2026 WARNING slow",
        "2026 ERROR boom",
        "2026 WARN legacy",
    ]
    assert logs.filter_lines(lines, grep=None, level="ERROR") == ["2026 ERROR boom"]
    assert logs.filter_lines(lines, grep="", level="INFO") == lines


def test_filter_lines_rejects_unknown_level():
    with pytest.raises(ValueError):
        logs.filter_lines([], grep=None, level="TRACE")


def test_line_level():
    assert logs.line_level("x ERROR y") == "ERROR"
    assert logs.line_level("x Warn y") is None
    assert logs.line_level("nothing") is None


def test_follower_reads_appended_lines_and_buffers_partials(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("old\n")
    f = logs.Follower(p)
    assert f.poll() == []
    with p.open("a") as fh:
        fh.write("new1\nnew")
    assert f.poll() == ["new1"]
    with p.open("a") as fh:
        fh.write("2\n")
    assert f.poll() == ["new2"]


def test_follower_detects_truncation(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("aaaaaaaaaa\n")
    f = logs.Follower(p)
    p.write_text("b\n")
    assert f.poll() == ["b"]


def test_follower_detects_rotation_by_inode(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("a\n")
    f = logs.Follower(p)
    os.rename(p, tmp_path / "f.log.1")
    p.write_text("fresh line that is longer\n")
    assert f.poll() == ["fresh line that is longer"]


def test_follower_handles_missing_file(tmp_path):
    p = tmp_path / "later.log"
    f = logs.Follower(p)
    assert f.poll() == []
    p.write_text("hello\n")
    assert f.poll() == ["hello"]
    p.unlink()
    assert f.poll() == []


def test_file_info(tmp_path):
    p = tmp_path / "i.log"
    assert logs.file_info(p) == {"exists": False, "size": 0}
    p.write_text("abc")
    assert logs.file_info(p) == {"exists": True, "size": 3}


# --- download summary (recorded format) -------------------------------------

MAC = Path(__file__).parent / "fixtures/mac"


def test_download_summary_recorded():
    lines = logs.tail_lines(MAC / "download-models.log", 5000)
    s = logs.download_summary(lines)
    (run,) = s["runs"]
    assert (run["started"], run["finished"], run["failed"]) == (
        "2026-09-28 22:15:01",
        "2026-09-29 00:00:05",
        0,
    )
    assert "mlx-community/Qwen3.8-27B-8bit/config.json" in run["files"]
    assert s["active"] is False
    assert s["last_progress"] == {
        "percent": 100,
        "total": "2713M",
        "received": "2713M",
        "speed": "10.2M",
        "left": "--:--:--",
    }


def test_download_summary_active_run_and_empty():
    lines = [
        "===== 2026-09-30 10:00:00 start =====",
        "GET org/model/model-00001-of-00002.safetensors",
        " 42 4000M   42 1680M    0     0  9000k      0  0:07:35  0:03:11  0:04:24 9.1M",
    ]
    s = logs.download_summary(lines)
    assert s["active"] is True
    assert s["runs"][0]["finished"] is None and s["runs"][0]["files"] == [
        "org/model/model-00001-of-00002.safetensors"
    ]
    assert s["last_progress"]["percent"] == 42 and s["last_progress"]["left"] == "0:04:24"
    assert logs.download_summary([]) == {"runs": [], "active": False, "last_progress": None}


def test_download_logs_are_discovered_by_pattern(tmp_path):
    s = _settings(tmp_path)
    logs_dir = tmp_path / "Models/logs"
    logs_dir.mkdir(parents=True)
    for n in (
        "download-models.log",
        "download-variant.log",
        "download-bad name.log",
        "other.log",
    ):
        (logs_dir / n).write_text("x\n")
    assert logs.source_ids(s) == [
        "mlx-server",
        "chat-ui",
        "serve",
        "admin",
        "comfyui",
        "download-models",
        "download-variant",
    ]
    assert logs.resolve_source(s, "download-variant") == logs_dir / "download-variant.log"
    for bad in ("download-../../etc/passwd", "download-nothere", "download-bad name", "download-"):
        with pytest.raises(KeyError):
            logs.resolve_source(s, bad)


def test_unreadable_files_behave_like_missing(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    (d / "x.log").write_text("a\n")
    os.chmod(d, 0)
    try:
        assert logs.tail_lines(d / "x.log", 5) == []
        assert logs.file_info(d / "x.log") == {"exists": False, "size": 0}
        f = logs.Follower(d / "x.log")
        assert f.poll() == []
    finally:
        os.chmod(d, 0o755)


def test_comfyui_log_is_a_fixed_source(tmp_path):
    s = Settings(mlx_root=tmp_path / "Models", comfyui_root=tmp_path / "comfyui")
    assert logs.resolve_source(s, "comfyui") == tmp_path / "comfyui/logs/comfyui.log"
    assert "comfyui" in logs.source_ids(s)


def test_decision_log_is_a_source_only_when_enabled(tmp_path):
    s = Settings(mlx_root=tmp_path / "Models", decision_root=tmp_path / "decision")
    assert "decision" not in logs.source_ids(s)
    with pytest.raises(KeyError):
        logs.resolve_source(s, "decision")
    s.decision_enabled = True
    assert "decision" in logs.source_ids(s)
    assert logs.resolve_source(s, "decision") == tmp_path / "decision/logs/openjev.log"


def test_strata_log_is_a_source_only_when_enabled(tmp_path):
    s = Settings(mlx_root=tmp_path / "Models", strata_root=tmp_path / "strata")
    assert "strata" not in logs.source_ids(s)
    with pytest.raises(KeyError):
        logs.resolve_source(s, "strata")
    s.strata_enabled = True
    assert "strata" in logs.source_ids(s)
    assert logs.resolve_source(s, "strata") == tmp_path / "strata/logs/strata.log"


def test_strata_engine_logs_are_sources_per_installed_pack(tmp_path):
    s = Settings(mlx_root=tmp_path / "Models", strata_root=tmp_path / "strata")
    app = tmp_path / "strata/app"
    app.mkdir(parents=True)
    for name in ("strata-q2_0.json", "strata-iq3_xxs.json", "strata-q2_0.log"):
        (app / name).write_text("{}")
    assert not [i for i in logs.source_ids(s) if i.startswith("strata")]
    s.strata_enabled = True
    ids = logs.source_ids(s)
    # in STRATA_PACKS order; a pack without a run config is not installed
    assert [i for i in ids if i.startswith("strata-engine")] == [
        "strata-engine-q2_0",
        "strata-engine-iq3_xxs",
    ]
    # listed before setup or a first start wrote the file: it shows as missing
    assert logs.resolve_source(s, "strata-engine-iq3_xxs") == app / "strata-iq3_xxs.log"
    assert logs.resolve_source(s, "strata-engine-q2_0") == app / "strata-q2_0.log"
    for bad in ("strata-engine-iq3_s", "strata-engine-../x", "strata-engine-Q2_0"):
        with pytest.raises(KeyError):
            logs.resolve_source(s, bad)


def test_strata_engine_logs_without_an_app_folder(tmp_path):
    s = Settings(mlx_root=tmp_path / "Models", strata_root=tmp_path / "absent")
    s.strata_enabled = True
    assert "strata" in logs.source_ids(s)
    assert not [i for i in logs.source_ids(s) if i.startswith("strata-engine")]


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("[strata] LOW: requests may stall (free memory 2.1 GB)", "WARNING"),
        ("[strata] non-finite logits at step 812", "ERROR"),
        ("[strata] ready on http://127.0.0.1:8086", None),
    ],
)
def test_strata_warning_lines_have_a_level(line, level):
    assert logs.line_level(line) == level
