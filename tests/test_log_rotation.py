"""``mlx-model rotate-logs`` with real zsh (M4 §3): rename-only, writer-aware, bounded."""

from __future__ import annotations

import os
import re
import stat

import pytest

from .host_harness import make_host

MIB = 1024 * 1024
STAMP = re.compile(r"^mlx-server\.log\.\d{8}-\d{6}$")


@pytest.fixture
def host(tmp_path):
    h = make_host(tmp_path)
    yield h
    h.cleanup()


def _logs(host):
    return host.root / "logs"


def _write(host, name, size, mode=0o640):
    p = _logs(host) / name
    p.write_bytes(b"x" * (size - 1) + b"\n")
    p.chmod(mode)
    return p


def _rotate(host, *args):
    return host.run("rotate-logs", "--max-bytes", str(MIB), "--keep", "3", *args)


def _lines(r):
    return [ln.split() for ln in r.stdout.splitlines() if ln.strip()]


def _archives(host, name="mlx-server.log"):
    return sorted(p.name for p in _logs(host).glob(f"{name}.*"))


def test_below_threshold_is_skipped_untouched(host):
    p = _write(host, "mlx-server.log", 100)
    r = _rotate(host)
    assert r.returncode == 0, r.stderr
    assert ["skipped", "mlx-server.log", "below-threshold", "100"] in _lines(r)
    assert p.stat().st_size == 100 and _archives(host) == []


def test_stopped_writer_is_rotated_by_rename_with_mode_kept(host):
    p = _write(host, "mlx-server.log", 2 * MIB)
    inode = p.stat().st_ino
    r = _rotate(host)
    assert r.returncode == 0, r.stderr
    (archive,) = _archives(host)
    assert STAMP.match(archive)
    assert ["rotated", "mlx-server.log", archive, str(2 * MIB)] in _lines(r)
    old = _logs(host) / archive
    assert old.stat().st_ino == inode and old.stat().st_size == 2 * MIB  # renamed, not copied
    assert p.stat().st_size == 0 and stat.S_IMODE(p.stat().st_mode) == 0o640
    assert not host.lock.exists() and not host.gate.exists()


def test_running_mlx_server_defers_its_logs(host):
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0
    p = _write(host, "mlx-server.log", 2 * MIB)
    r = _rotate(host)
    assert r.returncode == 0, r.stderr
    assert ["deferred", "mlx-server.log", "active-writer", str(2 * MIB)] in _lines(r)
    assert p.stat().st_size == 2 * MIB and _archives(host) == []


def test_open_download_log_is_deferred_and_others_still_rotate(host):
    _write(host, "download-models.log", 2 * MIB)
    _write(host, "chat-qwen.log", 2 * MIB)
    s = host.sleeper()
    (host.state / "open_download-models.log").write_text(str(s.pid))
    r = _rotate(host)
    assert r.returncode == 0, r.stderr
    got = _lines(r)
    assert ["deferred", "download-models.log", "active-writer", str(2 * MIB)] in got
    assert any(ln[:2] == ["rotated", "chat-qwen.log"] for ln in got)


def test_unknown_writer_is_deferred(host):
    _write(host, "serve-qwen.log", 2 * MIB)
    (host.state / "lsof_broken").write_text("")
    r = _rotate(host)
    assert ["deferred", "serve-qwen.log", "writer-unknown", str(2 * MIB)] in _lines(r)
    assert _archives(host, "serve-qwen.log") == []


def test_archives_beyond_keep_are_pruned_oldest_first(host):
    for day in range(1, 5):
        (_logs(host) / f"mlx-server.log.2026090{day}-030000").write_text("old\n")
    _write(host, "mlx-server.log", 2 * MIB)
    r = _rotate(host)
    assert r.returncode == 0, r.stderr
    kept = _archives(host)
    assert len(kept) == 3 and "mlx-server.log.20260904-030000" in kept
    pruned = [ln[1] for ln in _lines(r) if ln[0] == "pruned"]
    assert pruned == ["mlx-server.log.20260902-030000", "mlx-server.log.20260901-030000"]


def test_permission_failure_leaves_the_log_in_place(host):
    p = _write(host, "mlx-server.log", 2 * MIB)
    _logs(host).chmod(0o555)
    try:
        r = _rotate(host)
    finally:
        _logs(host).chmod(0o755)
    assert r.returncode == 1
    assert ["error", "mlx-server.log", "rename-failed"] in _lines(r)
    assert p.stat().st_size == 2 * MIB and _archives(host) == []
    assert not host.lock.exists()


def test_failure_after_the_rename_restores_the_original(host):
    # stands in for a full disk: the replacement file cannot be finished
    p = _write(host, "mlx-server.log", 2 * MIB)
    fakebin = host.root.parent / "fakebin"
    (fakebin / "chmod").write_text("#!/bin/bash\nexit 1\n")
    (fakebin / "chmod").chmod(0o755)
    r = _rotate(host)
    assert r.returncode == 1
    assert ["error", "mlx-server.log", "create-failed", "restored"] in _lines(r)
    assert p.stat().st_size == 2 * MIB and _archives(host) == []


def test_symlinked_logs_are_refused(host, tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_bytes(b"y" * 2 * MIB)
    os.symlink(target, _logs(host) / "mlx-server.log")
    os.symlink(target, _logs(host) / "download-evil.log")
    r = _rotate(host)
    assert r.returncode == 1
    got = _lines(r)
    assert ["error", "mlx-server.log", "symlink"] in got
    assert ["error", "download-evil.log", "symlink"] in got
    assert target.stat().st_size == 2 * MIB and _archives(host) == []


def test_only_allow_listed_names_are_considered(host):
    _write(host, "notes.log", 2 * MIB)
    _write(host, "download-bad name.log", 2 * MIB)
    r = _rotate(host)
    assert r.returncode == 0
    assert not any("notes.log" in ln or "bad" in ln for ln in r.stdout.splitlines())


@pytest.mark.parametrize(
    "args",
    [
        ["--max-bytes", "abc"],
        ["--max-bytes", "0"],
        ["--max-bytes", "99999999999999"],
        ["--keep", "0"],
        ["--keep", "51"],
        ["--keep"],
        ["../etc/models.conf"],
        ["--path", "/etc/passwd"],
    ],
)
def test_bad_arguments_are_rejected_before_anything_runs(host, args):
    p = _write(host, "mlx-server.log", 2 * MIB)
    r = host.run("rotate-logs", *args)
    assert r.returncode == 1 and r.stderr
    assert p.stat().st_size == 2 * MIB and _archives(host) == []


def test_rotation_is_serialized_by_the_lifecycle_lock(host):
    _write(host, "mlx-server.log", 2 * MIB)
    host.lock.mkdir()
    r = _rotate(host)
    assert r.returncode == 1 and "Another mlx-model command is running" in r.stderr
    assert _archives(host) == []


def test_second_run_does_not_create_another_archive(host):
    _write(host, "mlx-server.log", 2 * MIB)
    assert _rotate(host).returncode == 0
    r = _rotate(host)
    assert r.returncode == 0 and len(_archives(host)) == 1
    assert ["skipped", "mlx-server.log", "below-threshold", "0"] in _lines(r)


def test_missing_logs_are_reported_not_created(host):
    r = _rotate(host)
    assert ["missing", "mlx-server.log"] in _lines(r)
    assert not (_logs(host) / "mlx-server.log").exists()


def test_start_rotates_an_oversized_log_before_its_marker(host):
    _write(host, "mlx-server.log", 2 * MIB)
    (host.state / "healthy").write_text("")
    host.env["MLX_LOG_ROTATE_MAX_BYTES"] = str(MIB)
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    (archive,) = _archives(host)
    assert (_logs(host) / archive).stat().st_size == 2 * MIB
    text = (_logs(host) / "mlx-server.log").read_text()
    assert "start a =====" in text and "x" * 10 not in text


def test_start_still_starts_when_rotation_fails(host):
    _write(host, "mlx-server.log", 2 * MIB)
    (host.state / "healthy").write_text("")
    fakebin = host.root.parent / "fakebin"
    (fakebin / "chmod").write_text("#!/bin/bash\nexit 1\n")
    (fakebin / "chmod").chmod(0o755)
    host.env["MLX_LOG_ROTATE_MAX_BYTES"] = str(MIB)
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    assert _archives(host) == []


def test_start_leaves_a_small_log_alone(host):
    p = _write(host, "mlx-server.log", 100)
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0
    assert _archives(host) == [] and p.read_bytes().startswith(b"x" * 99)
