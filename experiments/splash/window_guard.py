#!/usr/bin/env python3
"""Temporary Mac experiment supervisor. Requires explicit maintenance approval.

Only configured experiment children are signalled, after checking PID, creation
 time and complete command. Configuration and state stay private in the spike.
The deployed admin Python supplies psutil; no production environment is modified.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psutil
from preflight import database_state, file_sha, semantic_intent

Record = dict[str, Any]
_STOP = False
_CHILDREN: list[subprocess.Popen[bytes]] = []


def identity(pid: int) -> Record | None:
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return {"pid": pid, "created": proc.create_time(), "command": proc.cmdline()}
    except (psutil.Error, OSError):
        return None


def same_generation(expected: Record, current: Record | None) -> bool:
    return bool(
        current
        and expected.get("pid")
        and expected.get("created")
        and expected.get("command")
        and all(expected.get(k) == current.get(k) for k in ("pid", "created", "command"))
    )


def signal_process(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def stop_records(records: list[Record], *, grace_s: float = 20) -> bool:
    """Recheck before TERM and again before any escalation; reused PIDs are left alone."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for record in records:
            if same_generation(record, identity(record["pid"])):
                signal_process(record["pid"], sig)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not any(same_generation(r, identity(r["pid"])) for r in records):
                return True
            time.sleep(0.2)
    return not any(same_generation(r, identity(r["pid"])) for r in records)


def descendants(records: list[Record]) -> list[Record]:
    found = {(r["pid"], r["created"]): r for r in records}
    for record in records:
        if not same_generation(record, identity(record["pid"])):
            continue
        try:
            for child in psutil.Process(record["pid"]).children(recursive=True):
                value = identity(child.pid)
                if value:
                    found[(value["pid"], value["created"])] = value
        except psutil.Error:
            continue
    return list(found.values())


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run_command(argv: list[str], *, timeout: float = 180) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout + result.stderr


def loaded(cfg: Record) -> bool:
    return (
        subprocess.run(
            ["launchctl", "print", f"gui/{cfg['uid']}/{cfg['admin_label']}"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def preserved(cfg: Record) -> bool:
    db = database_state(Path(cfg["admin_db"]))
    if not db["ok"]:
        return False
    current = db["state"]
    normalized = {
        "intent": semantic_intent(current.get("intent")),
        "sidecar_intent": semantic_intent(current.get("sidecar_intent")),
        "embed_intent": semantic_intent(current.get("embed_intent")),
        "recovery": current.get("recovery_suspended"),
    }
    try:
        return (
            normalized == cfg["expected_intent"]
            and all(file_sha(Path(path)) == sha for path, sha in cfg["config_hashes"].items())
            and str(Path(cfg.get("selected_link", "/Volumes/Models/models/active")).resolve())
            == cfg["selected_path"]
        )
    except OSError:
        return False


def experiment_present(spike: str) -> bool:
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or [])
            if spike in command and any(
                marker in command for marker in ("PHASE0_MLX", "server.server", "/engine/splash")
            ):
                return True
        except (psutil.Error, TypeError):
            continue
    return False


def strata_running(cfg: Record) -> bool:
    pidfile = Path("/Volumes/Models/strata/run/strata.pid")
    packfile = Path("/Volumes/Models/strata/run/strata.pack")
    try:
        pid = int(pidfile.read_text().strip())
        record = identity(pid)
        return bool(
            record
            and "serve/server.py --engine strata" in " ".join(record["command"])
            and packfile.read_text().strip() == cfg["original_pack"]
        )
    except (OSError, ValueError):
        return False


def restore(cfg: Record, state: Record) -> str:
    if not stop_records(state["records"]):
        return "experiment_processes_remain"
    if cfg.get("spike") and experiment_present(cfg["spike"]):
        return "experiment_processes_remain"
    if port_open(8086):
        return "experiment_port_remains"
    if not state.get("activated"):
        return "restored"
    if not preserved(cfg):
        return "external_state_changed"
    if state.get("strata_stopped"):
        if not strata_running(cfg):
            run_command(["/Volumes/Models/strata/bin/strata", "start", cfg["original_pack"]])
        state["strata_stopped"] = False
    if state.get("admin_unloaded") and cfg["original_loaded"] and not loaded(cfg):
        run_command(["launchctl", "bootstrap", f"gui/{cfg['uid']}", cfg["admin_plist"]])
        state["admin_unloaded"] = False
    return "restored"


def resource_reason(sample: Record, baseline_swap: int, previous_swap_warnings: int) -> str | None:
    if sample["available"] < 6 * 1024**3:
        return "low_headroom"
    if sample["pressure"] is not None and sample["pressure"] >= 4:
        return "critical_pressure"
    if sample["swap"] > baseline_swap + 16 * 1024**2 and previous_swap_warnings >= 1:
        return "sustained_swap_growth"
    return None


def sample_resources(records: list[Record]) -> Record:
    vm, swap = psutil.virtual_memory(), psutil.swap_memory()
    pressure = subprocess.run(
        ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    ).stdout.strip()
    rss = 0
    for record in records:
        if same_generation(record, identity(record["pid"])):
            try:
                rss += psutil.Process(record["pid"]).memory_info().rss
            except psutil.Error:
                pass
    return {
        "at": time.time(),
        "available": vm.available,
        "swap": swap.used,
        "pressure": int(pressure) if pressure.isdigit() else None,
        "owned_rss": rss,
    }


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(value, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def perform(cfg: Record, state: Record, request: Record) -> None:
    action = request["action"]
    if action == "activate":
        if state["phase"] != "armed" or not preserved(cfg):
            raise RuntimeError("state_changed_before_activation")
        db = database_state(Path(cfg["admin_db"]))
        if db["active_jobs"] != [] or port_open(8086):
            raise RuntimeError("busy_before_activation")
        expected = cfg["original_generation"]
        if not same_generation(expected, identity(expected["pid"])):
            raise RuntimeError("original_generation_changed")
        state["activated"] = True
        state["admin_unloaded"] = True
        write_json(Path(cfg["spike"]) / "guard-state.json", state)
        run_command(["launchctl", "bootout", f"gui/{cfg['uid']}/{cfg['admin_label']}"])
        write_json(Path(cfg["spike"]) / "guard-state.json", state)
        state["strata_stopped"] = True
        write_json(Path(cfg["spike"]) / "guard-state.json", state)
        run_command(["/Volumes/Models/strata/bin/strata", "stop"])
        if port_open(8080):
            raise RuntimeError("original_listener_remains")
        state["phase"] = "idle"
    elif action == "launch":
        if state["phase"] != "idle" or port_open(8086):
            raise RuntimeError("experiment_not_idle")
        name = request["name"]
        argv = cfg["commands"][name]
        with (Path(cfg["spike"]) / "logs" / f"{name}-{int(time.time())}.log").open("ab") as output:
            child = subprocess.Popen(
                argv, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
        _CHILDREN.append(child)
        record = None
        for _ in range(40):
            record = identity(child.pid)
            if (
                record
                and cfg["spike"] in " ".join(record["command"])
                and len(record["command"]) > 2
            ):
                if (
                    "bash" not in Path(record["command"][0]).name
                    and Path(record["command"][0]).name != "env"
                ):
                    break
            if child.poll() is not None:
                raise RuntimeError("experiment_exited_at_launch")
            time.sleep(0.05)
        else:
            child.terminate()
            raise RuntimeError("experiment_identity_not_established")
        assert record is not None
        state.update(records=[record], active=name, phase="serving")
    elif action == "stop":
        state["records"] = descendants(state["records"])
        if (
            not stop_records(state["records"])
            or port_open(8086)
            or experiment_present(cfg["spike"])
        ):
            raise RuntimeError("experiment_did_not_stop")
        state.update(records=[], active=None, phase="idle")
    else:
        raise RuntimeError("unknown_control_action")


def daemon(cfg: Record) -> int:
    global _STOP
    spike = Path(cfg["spike"])
    state: Record = {
        "phase": "armed",
        "activated": False,
        "records": [],
        "active": None,
        "admin_unloaded": False,
        "strata_stopped": False,
        "last_command": None,
        "guard_pid": os.getpid(),
        "heartbeat": time.time(),
        "reason": None,
    }

    def stop_signal(_signum: int, _frame: Any) -> None:
        global _STOP
        _STOP = True

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    baseline_swap = psutil.swap_memory().used
    swap_warnings = 0
    last_check = 0.0
    code = 0
    try:
        while not _STOP:
            for child in _CHILDREN:
                child.poll()
            state["records"] = descendants(state["records"])
            sample = sample_resources(state["records"])
            with (spike / "resources.jsonl").open("a") as output:
                output.write(json.dumps(sample) + "\n")
            state["resources"] = sample
            reason = resource_reason(sample, baseline_swap, swap_warnings)
            swap_warnings = (
                swap_warnings + 1 if sample["swap"] > baseline_swap + 16 * 1024**2 else 0
            )
            if time.time() >= cfg["collect_until"]:
                reason = "collection_deadline"
            if state["activated"] and time.monotonic() - last_check > 10:
                last_check = time.monotonic()
                if not preserved(cfg):
                    reason = "external_state_changed"
            if reason:
                state["reason"] = reason
                break
            command_file = spike / "guard-command.json"
            if command_file.exists():
                request = json.loads(command_file.read_text())
                if request["id"] != state["last_command"]:
                    if request["action"] == "restore":
                        state["last_command"] = request["id"]
                        state["reason"] = "requested_restore"
                        break
                    perform(cfg, state, request)
                    state["last_command"] = request["id"]
            state["heartbeat"] = time.time()
            write_json(spike / "guard-state.json", state)
            time.sleep(2)
    except BaseException as exc:
        state["reason"] = f"guard_error:{type(exc).__name__}:{exc}"
        code = 1
    finally:
        state["phase"] = "restoring"
        write_json(spike / "guard-state.json", state)
        try:
            state["records"] = descendants(state["records"])
            outcome = restore(cfg, state)
            state["phase"] = outcome
            if outcome != "restored":
                code = 1
        except BaseException as exc:
            state.update(phase="restore_failed", error=f"{type(exc).__name__}:{exc}")
            code = 1
        state["heartbeat"] = time.time()
        write_json(spike / "guard-state.json", state)
    return code


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) < 3:
        return 2
    action, path = sys.argv[1:3]
    cfg = json.loads(Path(path).read_text())
    if action == "daemon":
        return daemon(cfg)
    if action == "request":
        request = {"id": uuid.uuid4().hex, "action": sys.argv[3]}
        if len(sys.argv) > 4:
            request["name"] = sys.argv[4]
        write_json(Path(cfg["spike"]) / "guard-command.json", request)
        print(request["id"])
        return 0
    if action == "status":
        state = json.loads((Path(cfg["spike"]) / "guard-state.json").read_text())
        print(
            json.dumps(
                {
                    k: state.get(k)
                    for k in (
                        "phase",
                        "active",
                        "last_command",
                        "heartbeat",
                        "reason",
                        "resources",
                        "error",
                    )
                }
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
