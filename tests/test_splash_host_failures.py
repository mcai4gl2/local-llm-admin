"""Failure boundaries of the standalone owner; no live processes or sockets."""

from __future__ import annotations

import contextlib
import itertools
import signal
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from .test_host_splash import host_control as host_control


@pytest.mark.parametrize("failure", [OSError(), subprocess.TimeoutExpired("ps", 3)])
def test_process_command_failure_refuses_ownership(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(host_control.subprocess, "run", fail)
    with pytest.raises(host_control.HostError):
        host_control._run(["ps", "-axo", "args="])


@pytest.mark.parametrize(
    "mode", ["live-hidden", "denied", "malformed", "zombie", "bad-date", "mac"]
)
def test_process_inspection_never_guesses_identity(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    text = {
        "live-hidden": "",
        "denied": "",
        "malformed": "bad",
        "zombie": "Mon Jan 01 00:00:00 2024 Z 1 python python",
        "bad-date": "Bad Jan 01 00:00:00 2024 S 1 python python",
        "mac": "Mon Jan 01 00:00:00 2024 S 1 python python",
    }[mode]
    monkeypatch.setattr(host_control, "_run", lambda argv: text)

    def hidden(*args: Any) -> None:
        if mode == "denied":
            raise PermissionError()

    monkeypatch.setattr(host_control.os, "kill", hidden)
    if mode == "zombie":
        assert host_control.process(999999999) is None
    elif mode == "mac":
        assert host_control.process(999999999)["executable"] == "python"
    else:
        with pytest.raises(host_control.HostError):
            host_control.process(999999999)


@pytest.mark.parametrize("match", [True, False])
def test_macos_executable_uses_text_mapping(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, match: bool
) -> None:
    expected = tmp_path / "runtime/python"
    monkeypatch.setattr(
        host_control,
        "_run",
        lambda argv: "p100\nn" + str(expected if match else tmp_path / "other"),
    )
    assert host_control._exe_matches({"pid": 100, "executable": "python"}, expected) is match


@pytest.mark.parametrize("mode", ["executable", "controller-marker", "native-marker", "prepared"])
def test_member_needs_executable_and_launch_identity(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    role = "native" if mode == "native-marker" else "controller"
    proc = {"pid": 100, "started": 1.0, "args": ["-m", "server.server", "prepared"]}
    if mode == "controller-marker":
        proc["args"] = ["prepared"]
    if mode == "prepared":
        proc["args"] = ["-m", "server.server"]
    monkeypatch.setattr(host_control, "process", lambda pid: proc)
    monkeypatch.setattr(host_control, "_exe_matches", lambda *args: mode != "executable")
    with pytest.raises(host_control.HostError):
        host_control._member(tmp_path, 100, role, "prepared")


def test_unrelated_runtime_command_and_invalid_listener_are_refused(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        host_control,
        "_run",
        lambda argv: "100 " + str(tmp_path / "runtime/python") + " unrelated\ninvalid\n",
    )
    assert host_control.possible_pids(tmp_path) == set()
    monkeypatch.setattr(host_control, "_run", lambda argv: "unknown-pid")
    with pytest.raises(host_control.HostError):
        host_control.listeners(8080)


@pytest.mark.parametrize("unrecorded", [True, False])
def test_foreign_resources_are_unknown(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unrecorded: bool
) -> None:
    record = {"profile": "stock-8bit-32k", "fingerprint": "a" * 20, "model": "example/model"}
    monkeypatch.setattr(host_control, "_record", lambda root: None if unrecorded else record)
    monkeypatch.setattr(host_control, "possible_pids", lambda root: {100})
    monkeypatch.setattr(host_control, "members", lambda *args: [{"pid": 100}])
    monkeypatch.setattr(host_control, "listeners", lambda port: {200})
    assert host_control.observe(tmp_path)["state"] == "unknown"


def test_signal_tolerates_exit_after_revalidation(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(host_control, "_same", lambda *args: {})

    def exited(*args: Any) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(host_control.os, "kill", exited)
    host_control._signal(tmp_path, {}, {"pid": 100}, signal.SIGTERM)
    monkeypatch.setattr(host_control, "_same", lambda *args: None)
    host_control._signal(tmp_path, {}, {"pid": 100}, signal.SIGTERM)


@pytest.mark.parametrize("released", [True, False])
def test_stop_escalation_waits_for_resources(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, released: bool
) -> None:
    member = {"pid": 100, "role": "native"}
    record = {"native": [member]}
    monkeypatch.setattr(
        host_control, "observe", lambda root: {"state": "starting", "processes": [member]}
    )
    monkeypatch.setattr(host_control, "_record", lambda root: record)
    monkeypatch.setattr(host_control, "_write", lambda *args: None)
    signals = []
    monkeypatch.setattr(host_control, "_signal", lambda *args: signals.append(args[-1]))
    monkeypatch.setattr(
        host_control,
        "members",
        lambda *args: [] if released and signal.SIGKILL in signals else [member],
    )
    monkeypatch.setattr(host_control, "listeners", lambda port: set() if released else {200})
    clock = itertools.count()
    monkeypatch.setattr(host_control.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(host_control.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host_control, "STOP_TIMEOUT", 2)
    if released:
        host_control.stop(tmp_path)
    else:
        with pytest.raises(host_control.HostError, match="not been released"):
            host_control.stop(tmp_path)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_idempotent_stop_with_no_record(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(host_control, "observe", lambda root: {"state": "absent"})
    monkeypatch.setattr(host_control, "_record", lambda root: None)
    host_control.stop(tmp_path)


@pytest.mark.parametrize("mode", ["non200", "oversize", "json"])
def test_host_status_http_is_bounded(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    body = b"x" * 1048577 if mode == "oversize" else b"invalid-json"
    response = SimpleNamespace(
        status=403 if mode == "non200" else 200, read=lambda limit: body[:limit]
    )
    monkeypatch.setattr(
        host_control.profiles, "read_env", lambda path: SimpleNamespace(api_key="k" * 40)
    )
    monkeypatch.setattr(
        host_control.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=lambda *a, **kw: contextlib.nullcontext(response)),
    )
    assert host_control._get(tmp_path, "/status") is None


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["invalid"],
        ["start", "stock-8bit-32k"],
        ["stop"],
        ["status"],
        ["status", "--json"],
        ["guard"],
    ],
)
def test_cli_dispatch_redacts_executable(
    host_control: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
    args: list[str],
) -> None:
    monkeypatch.setattr(
        host_control.sys, "argv", ["control", str(tmp_path), *args] if args else ["control"]
    )
    calls = []
    monkeypatch.setattr(host_control, "start", lambda *args: calls.append("start"))
    monkeypatch.setattr(host_control, "stop", lambda *args: calls.append("stop"))
    monkeypatch.setattr(host_control, "observe", lambda root: {"state": "absent"})
    monkeypatch.setattr(
        host_control,
        "details",
        lambda root: {
            "state": "ready",
            "processes": [{"pid": 100, "executable": "/private/runtime"}],
        },
    )
    assert host_control.main() == (2 if not args or args[0] == "invalid" else 0)
    assert "/private/runtime" not in capsys.readouterr().out


def test_cli_failure_is_redacted(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    monkeypatch.setattr(host_control.sys, "argv", ["control", str(tmp_path), "stop"])

    def fail(root: Path) -> None:
        raise host_control.HostError("private input")

    monkeypatch.setattr(host_control, "stop", fail)
    assert host_control.main() == 1
    assert "private input" not in capsys.readouterr().err


def test_missing_profile_helper_refuses_import(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(host_control.importlib.util, "spec_from_file_location", lambda *args: None)
    with pytest.raises(host_control.HostError):
        host_control._profiles()


@pytest.mark.parametrize(
    "mode",
    [
        "running",
        "profile",
        "pin",
        "executable",
        "port",
        "other-pid",
        "bad-pid",
        "stale-pid",
        "capture-live",
        "capture-dead",
        "crash",
        "timeout",
        "ready",
    ],
)
def test_start_validates_before_fork_and_bounds_owned_cleanup(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    from .test_splash import catalog, write

    root = tmp_path / "splash"
    catalog(root)
    env = root / "etc/splash.env"
    env.write_text("SPLASH_API_KEY=" + "k" * 40 + "\nSPLASH_ALLOWED_HOSTS=mac-mini.local\n")
    env.chmod(0o600)
    write(
        root / "etc/runtime.json",
        {
            "runtime_version": "wrong" if mode == "pin" else "1.4.0",
            "runtime_sha256": host_control.profiles.RUNTIME_SHA256,
        },
    )
    for rel in ("python/bin/python3", "engine/splash"):
        path = root / "runtime" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder")
        path.chmod(0o700)
    if mode == "executable":
        (root / "runtime/engine/splash").unlink()
    if mode in ("other-pid", "bad-pid", "stale-pid"):
        path = root.parent / "run/mlx-server.pid"
        path.parent.mkdir()
        path.write_text("invalid" if mode == "bad-pid" else "200")
    monkeypatch.setattr(
        host_control,
        "observe",
        lambda root: {"state": "starting" if mode == "running" else "absent"},
    )
    monkeypatch.setattr(host_control, "listeners", lambda port: {200} if mode == "port" else set())
    monkeypatch.setattr(host_control, "process", lambda pid: None if mode == "stale-pid" else {})
    child = SimpleNamespace(pid=100, poll=lambda: 3 if mode in ("capture-dead", "crash") else None)
    launches = []

    def fork(*args: Any, **kwargs: Any) -> Any:
        launches.append((args, kwargs))
        return child

    monkeypatch.setattr(host_control.subprocess, "Popen", fork)
    member = {
        "pid": 100,
        "role": "controller",
        "started": 1.0,
        "executable": str(root / "runtime/python/bin/python3"),
    }
    monkeypatch.setattr(
        host_control, "_member", lambda *args: None if mode.startswith("capture-") else member
    )
    monkeypatch.setattr(host_control, "members", lambda *args: [member])
    monkeypatch.setattr(host_control, "_write", lambda *args: None)
    monkeypatch.setattr(
        host_control,
        "details",
        lambda root: {"state": "starting" if mode in ("crash", "timeout") else "ready"},
    )
    cleanups = []
    monkeypatch.setattr(host_control, "stop", lambda root: cleanups.append(root))
    clock = itertools.count()
    monkeypatch.setattr(host_control.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(host_control.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(host_control, "READY_TIMEOUT", 2)
    if mode in ("ready", "stale-pid"):
        host_control.start(root, "stock-8bit-32k")
        argv = launches[0][0][0]
        assert argv[argv.index("--allowed-host") + 1] == "mac-mini.local"
        assert cleanups == []
    else:
        with pytest.raises(host_control.HostError):
            host_control.start(root, "unknown" if mode == "profile" else "stock-8bit-32k")
        assert bool(launches) == (mode in ("capture-live", "capture-dead", "crash", "timeout"))
        assert bool(cleanups) == (mode in ("crash", "timeout"))


def test_guard_cli_refuses_known_owner(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(host_control.sys, "argv", ["control", str(tmp_path), "guard"])
    monkeypatch.setattr(host_control, "observe", lambda root: {"state": "starting"})
    assert host_control.main() == 1


def test_child_exiting_during_discovery_is_not_retained(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = {"pid": 100, "role": "controller"}
    record = {"controller": parent, "native": [], "prepared": "prepared"}
    monkeypatch.setattr(host_control, "_same", lambda *args: parent)
    monkeypatch.setattr(host_control, "_run", lambda argv: "101 100")
    monkeypatch.setattr(host_control, "_member", lambda *args: None)
    monkeypatch.setattr(host_control, "possible_pids", lambda root: {100})
    assert host_control.members(tmp_path, record) == [parent]


def test_host_status_absence_needs_no_http(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(host_control, "observe", lambda root: {"state": "absent", "processes": []})

    def forbidden(*args: Any) -> None:
        pytest.fail("HTTP called for absent runtime")

    monkeypatch.setattr(host_control, "_get", forbidden)
    assert host_control.details(tmp_path)["state"] == "absent"


def test_standalone_entrypoint_reports_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["control"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "host/splash/lib/control.py"),
            run_name="__main__",
        )
    assert exc.value.code == 2
