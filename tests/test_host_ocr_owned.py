"""Owned process groups are stopped only when generation identity still matches."""

import json
import subprocess
import sys

from .test_host_ocr_pipeline import load


def test_stale_generation_never_signals_an_unrelated_process(tmp_path, monkeypatch):
    module = load("owned")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    record = tmp_path / "role.json"
    try:
        identity = module.identity(process.pid)
        identity["started"] = "stale-generation"
        identity["scope"] = str(tmp_path)
        record.write_text(json.dumps(identity))
        def forbidden(*args):
            raise AssertionError("must not signal a different generation")
        monkeypatch.setattr(module.os, "killpg", forbidden)
        assert not module.stop_record(record, grace_s=0.01)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_owned_process_group_stop_is_verified(tmp_path):
    module = load("owned")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(tmp_path)],
                               start_new_session=True)
    record = tmp_path / "role.json"
    try:
        module.write_record(record, process.pid, str(tmp_path))
        assert module.stop_record(record, grace_s=0.1)
        process.wait(timeout=3)
        assert process.returncode is not None and not record.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def test_group_exit_between_observation_and_signal_is_confirmed_down(tmp_path, monkeypatch):
    module = load("owned")
    record = tmp_path / "role.json"
    data = {"pid": 123, "pgid": 123, "started": "same-generation", "command": str(tmp_path / "python"),
            "scope": str(tmp_path)}
    record.write_text(json.dumps(data))
    record.with_suffix(".pid").write_text("123")
    alive = True
    monkeypatch.setattr(module, "inventory", lambda: [data] if alive else [])
    def already_exited(*args):
        nonlocal alive
        alive = False
        raise ProcessLookupError("group exited before signal")
    monkeypatch.setattr(module.os, "killpg", already_exited)
    assert module.stop_record(record)
    assert not record.exists() and not record.with_suffix(".pid").exists()


def test_process_exiting_during_identity_check_is_confirmed_down(tmp_path, monkeypatch):
    module = load("owned")
    record = tmp_path / "role.json"
    data = {"pid": 123, "pgid": 123, "started": "same-generation",
            "command": str(tmp_path / "python"), "scope": str(tmp_path)}
    record.write_text(json.dumps(data))
    record.with_suffix(".pid").write_text("123")
    snapshots = iter([[{**data, "command": "(python)"}], [{**data, "command": "(python)"}], []])
    monkeypatch.setattr(module, "inventory", lambda: next(snapshots, []))
    def forbidden(*args):
        raise AssertionError("an ambiguous generation must never be signaled")
    monkeypatch.setattr(module.os, "killpg", forbidden)
    assert module.stop_record(record)
    assert not record.exists() and not record.with_suffix(".pid").exists()


def test_inventory_observer_runs_outside_the_owned_service_process_group(tmp_path, monkeypatch):
    import os
    module = load("owned")
    observer = tmp_path / "ps"
    observer.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "print(f'{os.getpid()} {os.getpgrp()} S Thu Jan 01 00:00:00 1970 ps-observer')\n"
    )
    observer.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    rows = module.inventory()
    assert len(rows) == 1
    assert rows[0]["pgid"] == rows[0]["pid"]
    assert rows[0]["pgid"] != os.getpgrp()
