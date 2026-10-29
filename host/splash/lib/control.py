#!/usr/bin/python3
"""Standalone Splash ownership/lifecycle helper. Standard library only."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

READY_TIMEOUT = 300.0
STOP_TIMEOUT = 30.0
PORT = 8080
LOG_ROTATE_MAX = 50 * 1024 * 1024
LOG_ROTATE_KEEP = 8


class HostError(ValueError):
    pass


def _profiles() -> Any:
    path = Path(__file__).with_name("profiles.py")
    spec = importlib.util.spec_from_file_location("splash_private_profiles", path)
    if spec is None or spec.loader is None:
        raise HostError("Missing profile contract.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Avoid bytecode under a versioned/deployed host tree.
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


profiles = _profiles()


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HostError("Cannot verify process ownership.") from None
    if result.returncode != 0 and not (
        result.returncode == 1 and (argv[0] == "lsof" or "-p" in argv)
    ):
        raise HostError("Cannot verify process ownership.")
    return result.stdout.strip()


def process(pid: int) -> dict[str, Any] | None:
    text = _run(
        [
            "ps",
            "-ww",
            "-p",
            str(pid),
            "-o",
            "lstart=",
            "-o",
            "stat=",
            "-o",
            "ppid=",
            "-o",
            "comm=",
            "-o",
            "args=",
        ]
    )
    if not text:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        raise HostError("Process inspection is unavailable.")
    fields = text.split(None, 8)
    if len(fields) < 9:
        raise HostError("Unreadable process identity.")
    if fields[5].startswith("Z"):
        return None
    if sys.platform == "darwin" and "E" in fields[5]:
        # macOS tears down executable/argv mappings while P_WEXIT is still visible.
        # Wait for independent absence; an exiting or reused live PID never authorizes signals.
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return None
            except PermissionError:
                raise HostError("Process inspection is unavailable.") from None
            time.sleep(0.05)
        raise HostError("Exiting process is still present.")
    try:
        started = time.mktime(time.strptime(" ".join(fields[:5]), "%a %b %d %H:%M:%S %Y"))
        executable = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
    except FileNotFoundError:
        executable = fields[7]
    except ValueError:
        raise HostError("Unreadable process creation time.") from None
    return {
        "pid": pid,
        "started": started,
        "executable": executable,
        "ppid": int(fields[6]),
        "args": fields[8].split(),
    }


def _exe_matches(proc: dict[str, Any], expected: Path) -> bool:
    if Path(proc["executable"]).is_absolute() and Path(proc["executable"]).resolve() == expected:
        return True
    # macOS does not have /proc. The executable is a txt entry, never a later argv token.
    text = _run(["lsof", "-a", "-p", str(proc["pid"]), "-d", "txt", "-Fn"])
    return any(
        line.startswith("n") and Path(line[1:]).resolve() == expected for line in text.splitlines()
    )


def _member(root: Path, pid: int, role: str, prepared: str) -> dict[str, Any] | None:
    proc = process(pid)
    if proc is None:
        return None
    rel = "python/bin/python3" if role == "controller" else "engine/splash"
    expected = (root / "runtime" / rel).resolve()
    if not expected.is_relative_to((root / "runtime").resolve()):
        raise HostError("Process executable identity is unknown.")
    if not _exe_matches(proc, expected):
        # A macOS member can exit between ps and lsof while its owner stops it.
        # Only proven absence permits ignoring the mismatch; live/reused PIDs refuse.
        if process(pid) is None:
            return None
        raise HostError("Process executable identity is unknown.")
    args = proc["args"]
    marker = any(args[i : i + 2] == ["-m", "server.server"] for i in range(len(args) - 1))
    if prepared not in args or (not marker if role == "controller" else "serve-native" not in args):
        raise HostError("Process launch identity is unknown.")
    return {"pid": pid, "started": proc["started"], "executable": str(expected), "role": role}


def possible_pids(root: Path) -> set[int]:
    prefix = str(root / "runtime") + "/"
    found = set()
    for line in _run(["ps", "-ww", "-axo", "pid=,args="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].startswith(prefix):
            if " -m server.server " in parts[1] or parts[1].startswith(
                str(root / "runtime/engine/splash") + " "
            ):
                found.add(int(parts[0]))
    return found


def listeners(port: int) -> set[int]:
    text = _run(["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"])
    if text and not all(line.isdigit() for line in text.splitlines()):
        raise HostError("Listener ownership is unknown.")
    return {int(line) for line in text.splitlines()}


def _write(root: Path, record: dict[str, Any]) -> None:
    run = root / "run"
    run.mkdir(mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="splash-", suffix=".tmp", dir=run)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(record, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, run / "splash.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _record(root: Path) -> dict[str, Any] | None:
    try:
        value: dict[str, Any] | None = profiles.read_ownership(root)
        return value
    except ValueError:
        raise HostError("Splash ownership record cannot be verified.") from None


def _same(root: Path, record: dict[str, Any], old: dict[str, Any]) -> dict[str, Any] | None:
    current = _member(root, old["pid"], old["role"], record["prepared"])
    if current is not None and current != old:
        raise HostError("Splash process generation changed.")
    return current


def members(root: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    # Snapshot candidates before child discovery so a newly born child waits for the
    # next observation instead of becoming an apparently unrecorded orphan.
    candidates = possible_pids(root)
    controller = _same(root, record, record["controller"])
    native = {p["pid"]: p for p in record["native"] if _same(root, record, p) is not None}
    if controller is not None:
        for line in _run(["ps", "-ww", "-axo", "pid=,ppid="]).splitlines():
            fields = line.split()
            if len(fields) == 2 and int(fields[1]) == controller["pid"]:
                item = _member(root, int(fields[0]), "native", record["prepared"])
                if item:
                    native[item["pid"]] = item
    found = ([controller] if controller else []) + list(native.values())
    # Each recorded generation was revalidated above, including proven exits.
    recorded = {p["pid"] for p in [record["controller"], *record["native"]]}
    if candidates - {p["pid"] for p in found} - recorded:
        raise HostError("An unrecorded Splash process owns resources.")
    return found


def observe(root: Path) -> dict[str, Any]:
    try:
        record = _record(root)
        if record is None:
            if possible_pids(root):
                raise HostError("Unrecorded Splash process.")
            return {"state": "absent", "processes": [], "profile": None}
        found = members(root, record)
        owners = listeners(record.get("port", PORT))
        if owners - {p["pid"] for p in found}:
            raise HostError("An unrelated listener owns the serving port.")
        return {
            "state": "starting" if found else "absent",
            "processes": found,
            "profile": record["profile"],
            "fingerprint": record["fingerprint"],
            "model": record["model"],
        }
    except (HostError, OSError):
        return {"state": "unknown", "processes": [], "profile": None}


def _get(root: Path, path: str) -> Any:
    try:
        env = profiles.read_env(root / "etc/splash.env")
        record = _record(root)
        port = record.get("port", PORT) if record is not None else PORT
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            headers={"Authorization": "Bearer " + env.api_key},
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=1
        ) as response:
            body = response.read(1_048_577)
            if response.status != 200 or len(body) > 1_048_576:
                return None
            return json.loads(body)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def details(root: Path) -> dict[str, Any]:
    result = observe(root)
    if result["state"] != "starting":
        return result
    record = _record(root)
    assert record is not None
    ready, status, models = (_get(root, path) for path in ("/ready", "/status", "/v1/models"))
    if not isinstance(status, dict) or not isinstance(models, dict):
        return result
    instance = status.get("instance")
    if (
        not isinstance(instance, dict)
        or instance.get("pid") != record["controller"]["pid"]
        or instance.get("model") != record["model"]
    ):
        result["state"] = "unknown"
        return result
    try:
        configured = profiles.load_profiles(root / "etc/profiles.json", root, require_assets=False)
    except ValueError:
        result["state"] = "unknown"
        return result
    profile = configured.get(record["profile"])
    rows = models.get("data")
    rows = rows if isinstance(rows, list) else []
    by_id = {
        row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    expected = {profile.model, *profile.aliases} if profile is not None else set()
    weights = status.get("weights", {})
    if (
        profile is None
        or profile.fingerprint != record["fingerprint"]
        or type(instance.get("pid")) is not int
        or type(status.get("maximum_context_tokens")) is not int
        or status.get("maximum_context_tokens") != profile.max_context
        or not expected <= by_id.keys()
        or any(
            type(by_id[name].get("max_model_len")) is not int
            or by_id[name]["max_model_len"] != profile.max_context
            for name in expected
        )
        or not profiles.runtime_precision_matches(
            status, kv_format=profile.kv_format, state_format=profile.state_format
        )
        or not isinstance(weights, dict)
    ):
        result["state"] = "unknown"
        return result
    fresh = observe(root)
    if fresh["processes"] != result["processes"] or fresh["state"] != "starting":
        result["state"] = "unknown"
        return result
    if weights.get("restoring") is True:
        result["state"] = "recovering"
    elif weights.get("released") is True:
        result["state"] = "released"
    elif (
        ready == {"status": "ready"}
        and status.get("ready") is True
        and any(p["role"] == "native" for p in result["processes"])
    ):
        result.update(state="ready", max_context=profile.max_context)
    return result


def _signal(root: Path, record: dict[str, Any], member: dict[str, Any], sig: int) -> None:
    if _same(root, record, member) is not None:
        try:
            os.kill(member["pid"], sig)
        except ProcessLookupError:
            pass


def stop(root: Path) -> None:
    snapshot = observe(root)
    if snapshot["state"] == "unknown":
        raise HostError("Refusing to stop unknown ownership.")
    record = _record(root)
    if record is None:
        return
    found = snapshot["processes"]
    # Persist verified descendants before the controller can exit and reparent them.
    record["native"] = [p for p in found if p["role"] == "native"]
    _write(root, record)
    for item in found:
        _signal(root, record, item, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        remaining = members(root, record)
        if not remaining:
            break
        time.sleep(0.05)
    for item in members(root, record):
        _signal(root, record, item, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not members(root, record) and not listeners(record.get("port", PORT)):
            (root / "run/splash.json").unlink(missing_ok=True)
            return
        time.sleep(0.05)
    raise HostError("Splash resources have not been released.")


def rotate_log(root: Path) -> None:
    log = root / "logs/splash.log"
    if not log.is_file() or log.stat().st_size <= LOG_ROTATE_MAX:
        return
    log.rename(log.with_name(log.name + "." + str(time.time_ns())))
    archives = sorted(log.parent.glob("splash.log.*"), key=lambda p: p.name, reverse=True)
    for old in archives[LOG_ROTATE_KEEP:]:
        old.unlink()


def native_ports(root: Path) -> dict[str, int]:
    config = root.parent / "etc/engine-ports.json"
    if not config.exists() and not config.is_symlink():
        return {"mlx": PORT, "strata": PORT}
    helper = root.parent / "bin/engine-ports"
    ports: dict[str, int] = json.loads(_run([sys.executable, "-I", str(helper), str(config)]))
    return ports


def start(root: Path, name: str) -> None:
    if observe(root)["state"] != "absent":
        raise HostError("Splash already owns resources or ownership is unknown.")
    profile = profiles.load_profiles(root / "etc/profiles.json", root).get(name)
    if profile is None:
        raise HostError("Unknown Splash profile.")
    env = profiles.read_env(root / "etc/splash.env")
    receipt = json.loads(profiles.private_text(root / "etc/runtime.json"))
    if receipt != {
        "runtime_version": profiles.RUNTIME_VERSION,
        "runtime_sha256": profiles.RUNTIME_SHA256,
    }:
        raise HostError("Runtime inventory pin does not match.")
    python, binary = root / "runtime/python/bin/python3", root / "runtime/engine/splash"
    for path in (python, binary):
        if (
            not path.is_file()
            or not os.access(path, os.X_OK)
            or not path.resolve().is_relative_to((root / "runtime").resolve())
        ):
            raise HostError("Missing or untrusted runtime executable.")
    ports = native_ports(root)
    if "splash" in ports and ports["splash"] != env.port:
        raise HostError("Splash native port configuration differs from its environment.")
    for port in (ports["mlx"], ports["strata"], env.port, 7860, 8188, 8081, 8083, 8084):
        if listeners(port):
            raise HostError("An engine or GPU service still owns resources.")
    for path in [
        root.parent / "run/mlx-server.pid",
        root.parent / "comfyui/run/comfyui.pid",
        root.parent / "strata/run/strata.pid",
        root.parent / "decision/run/openjev.pid",
        root.parent / "tts/run/qwen-tts.pid",
        root.parent / "embed/run/qwen-embed.pid",
    ]:
        if path.exists():
            try:
                if process(int(path.read_text().strip())) is not None:
                    raise HostError("An engine or GPU service still owns a process.")
            except ValueError:
                raise HostError("Another owner record is unreadable.") from None
    for rel in ("home", "tmp", "cache/huggingface/hub", "cache/uv", "logs", "run"):
        (root / rel).mkdir(parents=True, mode=0o700, exist_ok=True)
    launch_env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(root / "tmp") + "/",
        "XDG_CONFIG_HOME": str(root / "home/.config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_DATA_HOME": str(root / "home/.local/share"),
        "HF_HOME": str(root / "cache/huggingface"),
        "HF_HUB_CACHE": str(root / "cache/huggingface/hub"),
        "UV_CACHE_DIR": str(root / "cache/uv"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(root / "runtime"),
        "SPLASH_API_KEY": env.api_key,
    }
    argv = [
        str(python),
        "-P",
        "-m",
        "server.server",
        str(profile.prepared),
        "--model",
        profile.model,
        "--binary",
        str(binary),
        "--host",
        env.host,
        "--port",
        str(env.port),
        "--max-context",
        str(profile.max_context),
        "--max-memory",
        str(profile.memory_gib) + "G",
        "--kv-format",
        "bf16",
        "--state-format",
        "fp32",
        "--max-cache-disk",
        "0",
        "--idle-release",
        "off",
        "--default-reasoning-effort",
        "high",
        "--no-webui",
    ]
    for alias in profile.aliases:
        argv.extend(["--served-model-name", alias])
    for host in env.allowed_hosts:
        argv.extend(["--allowed-host", host])
    rotate_log(root)
    with (root / "logs/splash.log").open("ab") as log:
        child = subprocess.Popen(
            argv,
            cwd=root,
            env=launch_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    record = {
        "version": 1,
        "profile": name,
        "fingerprint": profile.fingerprint,
        "model": profile.model,
        "prepared": str(profile.prepared),
        "port": env.port,
        "controller": None,
        "native": [],
    }
    try:
        controller = _member(root, child.pid, "controller", str(profile.prepared))
        if controller is None:
            raise HostError("Splash exited before ownership capture.")
        record["controller"] = controller
        _write(root, record)
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            found = members(root, record)
            record["native"] = [p for p in found if p["role"] == "native"]
            _write(root, record)
            ready = details(root)
            if ready["state"] == "ready":
                # Readiness can discover a child born after the preceding member scan.
                record["native"] = [p for p in ready["processes"] if p["role"] == "native"]
                _write(root, record)
                return
            if child.poll() is not None:
                raise HostError("Splash exited during startup.")
            time.sleep(0.05)
        raise HostError("Splash loaded readiness timed out.")
    except (HostError, OSError, ValueError):
        if record["controller"] is not None:
            stop(root)
        else:
            # A failed unrecorded launch cannot authorize signals to an unknown PID.
            if child.poll() is None:
                raise HostError(
                    "Startup ownership was not captured; intervention is required."
                ) from None
        raise HostError("Splash startup failed; owned resources were stopped.") from None


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    root, action = Path(sys.argv[1]).resolve(), sys.argv[2]
    try:
        if action == "start" and len(sys.argv) == 4:
            start(root, sys.argv[3])
            print("Splash is ready.")
        elif action == "stop" and len(sys.argv) == 3:
            stop(root)
            print("Splash stopped.")
        elif action == "guard" and len(sys.argv) == 3:
            return 0 if observe(root)["state"] == "absent" else 1
        elif action == "status" and sys.argv[3:] in ([], ["--json"]):
            value = details(root)
            value["processes"] = [
                {k: v for k, v in p.items() if k != "executable"} for p in value["processes"]
            ]
            print(json.dumps(value) if sys.argv[3:] else "Splash: " + value["state"])
        else:
            return 2
        return 0
    except (HostError, ValueError, OSError):
        print(
            "Splash operation refused or failed; verify private configuration, ownership and logs.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
