"""Generation-checked ownership of OCR process groups; standard library only."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def inventory() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat=,lstart=,args="],
        capture_output=True, text=True, timeout=3, check=True, start_new_session=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=8)
        if len(fields) != 9 or fields[2].startswith("Z"):
            continue
        rows.append({"pid": int(fields[0]), "pgid": int(fields[1]),
                     "started": " ".join(fields[3:8]), "command": fields[8]})
    return rows


def identity(pid: int) -> dict[str, Any] | None:
    return next((row for row in inventory() if row["pid"] == pid), None)


def write_record(path: Path, pid: int, scope: str, *, owner_token: str | None = None) -> dict[str, Any]:
    observed = identity(pid)
    if observed is None or observed["pgid"] != pid:
        raise ValueError("process generation cannot be recorded")
    record = {**observed, "scope": scope}
    token = owner_token or os.environ.get("RESIDENT_OWNER_TOKEN")
    if token:
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("invalid start transaction")
        record["owner_token"] = token
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".owner-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream)
        os.replace(temporary, path)
        path.with_suffix(".pid").write_text(str(pid) + "\n")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return record


def stop_record(path: Path, grace_s: float = 3.0) -> bool:
    record = None
    try:
        if not path.exists():
            return not path.with_suffix(".pid").exists()
        if path.is_symlink():
            return False
        record = json.loads(path.read_text())
        pid, pgid = record["pid"], record["pgid"]
        if type(pid) is not int or pid <= 1 or pgid != pid:
            return False

        def members() -> list[dict[str, Any]]:
            rows = inventory()
            current = next((row for row in rows if row["pid"] == pid), None)
            if current is not None and any(current[key] != record[key] for key in ("pgid", "started", "command")):
                raise ValueError("process generation changed")
            group = [row for row in rows if row["pgid"] == pgid]
            if any(row["pid"] != pid and record["scope"] + "/" not in row["command"] for row in group):
                raise ValueError("unverified process group member")
            return group

        for sig, timeout in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 3.0)):
            if not members():
                break
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                if members():
                    return False
                break  # another cleanup already stopped it; final verification still applies
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and members():
                time.sleep(0.02)
        if members():
            return False
        # Never remove a record replaced by another owner during observation.
        if not path.exists():
            return not members() and not path.with_suffix(".pid").exists()
        if json.loads(path.read_text()) != record:
            return False
        path.unlink()
        pid_file = path.with_suffix(".pid")
        if pid_file.exists() and pid_file.read_text().strip() == str(pid):
            pid_file.unlink()
        return True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # A process can exit while ps is reading its argv. Never signal an ambiguous
        # generation; a fresh, empty inventory can still prove that cleanup finished.
        try:
            if not (isinstance(record, dict) and type(record.get("pid")) is int
                    and record["pid"] > 1 and record.get("pgid") == record["pid"]):
                return False
            deadline = time.monotonic() + min(grace_s, 3.0)
            while any(row["pid"] == record["pid"] or row["pgid"] == record["pgid"]
                      for row in inventory()):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
            if (not path.is_symlink() and path.exists()
                    and json.loads(path.read_text()) == record):
                path.unlink()
                pid_file = path.with_suffix(".pid")
                if pid_file.exists() and pid_file.read_text().strip() == str(record["pid"]):
                    pid_file.unlink()
                return True
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            pass
        return False


def log_event(root: Path, role: str, event: str, **counts: int | float) -> None:
    with contextlib.suppress(OSError):
        _log_event(root, role, event, **counts)


def _log_event(root: Path, role: str, event: str, **counts: int | float) -> None:
    """Only supervisor-owned event names and numeric counts enter routine logs."""
    path = root / "logs" / ("vlm.log" if role == "vlm" else "ocr.log")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists() and path.stat().st_size >= 50 * 1024 * 1024:
        archived = path.with_name(path.name + "." + str(time.time_ns()))
        path.rename(archived)
        for previous in sorted(path.parent.glob(path.name + ".*"), key=lambda p: p.name, reverse=True)[8:]:
            previous.unlink()
    row = {"ts": time.time(), "event": event, **{
        key: value for key, value in counts.items()
        if key in ("code", "pages", "bytes", "latency_ms") and type(value) in (int, float)
    }}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        stream.write(json.dumps(row) + "\n")
