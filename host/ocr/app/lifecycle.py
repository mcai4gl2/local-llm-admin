#!/usr/bin/python3
"""OCR dependency lifecycle under the shared host wrapper lock."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_local(name: str) -> Any:
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("ocr_lifecycle_" + name, path)
    if spec is None or spec.loader is None:
        raise ValueError("missing OCR lifecycle module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


owned = load_local("owned")
configuration = load_local("config")
ROLES = ("paddleocr-vl", "pipeline", "vlm")


class LifecycleError(ValueError):
    pass


_ENVIRONMENT = """
import importlib.metadata, json, platform, sys
print(json.dumps({"python":list(sys.version_info[:2]),"system":platform.system(),
"machine":platform.machine(),"packages":{d.metadata["Name"]:d.version
for d in importlib.metadata.distributions() if d.metadata["Name"]}}))
"""


def validate_environments(root: Path) -> None:
    for name, lock in (("venv", "ocr-app.txt"), ("venv-vlm", "ocr-vlm.txt")):
        path = root.parent / "environment" / lock
        try:
            pins = dict(re.findall(r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", path.read_text(), re.M))
            if not pins:
                raise ValueError("missing lock")
            result = subprocess.run(
                [str(root / name / "bin/python"), "-I", "-c", _ENVIRONMENT],
                capture_output=True, text=True, timeout=10, check=True,
            )
            actual = json.loads(result.stdout)
            if actual["python"] != [3, 12] or actual["system"] != "Darwin" or actual["machine"] != "arm64":
                raise ValueError("unsupported platform")
            packages = {re.sub(r"[-_.]+", "-", name.lower()): version
                        for name, version in actual["packages"].items()}
            if any(packages.get(re.sub(r"[-_.]+", "-", package.lower())) != version
                   for package, version in pins.items()):
                raise ValueError("environment drift")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise LifecycleError("OCR isolated environment differs from its locked candidate") from None


def launch(root: Path, role: str, argv: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True,
    )
    try:
        owned.write_record(root / "run" / (role + ".json"), process.pid, str(root),
                           owner_token=env.get("RESIDENT_OWNER_TOKEN"))
    except Exception:
        # This is still our unreaped direct child; do not leave an unrecorded launch alive.
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        raise
    owned.log_event(root, "vlm" if role == "vlm" else "ocr", "process_started")
    return process


def wait_vlm(port: int, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise LifecycleError("OCR VLM did not become live before its deadline")


def _has_unrecorded_process(root: Path) -> bool:
    markers = (str(root / "app/server.py"), str(root / "app/worker.py"), "mlx_vlm.server")
    return any(str(root) + "/" in process["command"] and any(
        marker in process["command"] for marker in markers
    ) for process in owned.inventory())


def start(root: Path, *, owner_token: str | None = None) -> None:
    config = configuration.load_config(root)
    validate_environments(root)
    for role in ROLES:
        record = root / "run" / (role + ".json")
        if record.exists():
            data = json.loads(record.read_text())
            if owned.identity(data["pid"]) is not None or not owned.stop_record(record):
                raise LifecycleError("OCR process ownership already exists or is unknown")
        elif record.with_suffix(".pid").exists():
            raise LifecycleError("OCR process generation record is missing")
    if _has_unrecorded_process(root):
        raise LifecycleError("An orphaned OCR process still owns resources")
    for name in ("logs", "run", "tmp", "cache/hf", "cache/paddlex"):
        (root / name).mkdir(parents=True, mode=0o700, exist_ok=True)
    env = {
        **os.environ, "HF_HOME": str(root / "cache/hf"), "HF_HUB_OFFLINE": "1",
        "PADDLE_PDX_CACHE_HOME": str(root / "cache/paddlex"),
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True", "PYTHONUNBUFFERED": "1",
        "TMPDIR": str(root / "tmp"), "PYTHONNOUSERSITE": "1",
    }
    env.pop("RESIDENT_OWNER_TOKEN", None)
    if owner_token:
        if not re.fullmatch(r"[a-f0-9]{32}", owner_token):
            raise LifecycleError("invalid start transaction")
        env["RESIDENT_OWNER_TOKEN"] = owner_token
    created = []
    try:
        launch(root, "vlm", [
            str(root / "venv-vlm/bin/python"), "-m", "mlx_vlm.server",
            "--host", "127.0.0.1", "--port", str(config["vlm_port"]),
            "--model", config["vlm_model_path"], "--max-num-seqs", "1", "--log-level", "ERROR",
        ], env)
        created.append("vlm")
        wait_vlm(config["vlm_port"])
        launch(root, "paddleocr-vl", [
            str(root / "venv/bin/python"), "-I", str(root / "app/server.py"), str(root),
        ], env)
        created.append("paddleocr-vl")
    except Exception:
        clean = all([owned.stop_record(root / "run" / (role + ".json")) for role in reversed(created)])
        raise LifecycleError("OCR start failed; " + ("created processes stopped" if clean else "cleanup incomplete")) from None


def stop(root: Path, *, owner_token: str | None = None) -> bool:
    if owner_token:
        for role in ROLES:
            path = root / "run" / (role + ".json")
            if path.exists():
                if path.is_symlink():
                    return False
                record = json.loads(path.read_text())
                if not isinstance(record, dict) or record.get("owner_token") != owner_token:
                    return False  # another transaction owns this service; never signal it
    stopped = [owned.stop_record(root / "run" / (role + ".json")) for role in ROLES]
    released = all(stopped) and not _has_unrecorded_process(root)
    owned.log_event(root, "ocr", "processes_stopped" if released else "cleanup_incomplete")
    owned.log_event(root, "vlm", "processes_stopped" if released else "cleanup_incomplete")
    return released


def status(root: Path) -> None:
    for role in ROLES:
        pid_file = root / "run" / (role + ".pid")
        try:
            pid = int(pid_file.read_text().strip())
            running = owned.identity(pid) is not None
        except (OSError, ValueError):
            pid, running = None, False
        print(f"{role}: " + (f"running pid {pid}" if running else "stopped"))
    try:
        configuration.load_config(root)
        print("weights: complete")
    except ValueError:
        print("weights: incomplete")


def main() -> int:
    try:
        if len(sys.argv) not in (3, 5) or sys.argv[2] not in ("start", "stop", "status"):
            raise LifecycleError("Usage: lifecycle.py ROOT start|stop|status")
        root, action = Path(sys.argv[1]), sys.argv[2]
        token = None
        if len(sys.argv) == 5:
            expected_flag = "--owner-token" if action == "start" else "--if-owner-token"
            if action == "status" or sys.argv[3] != expected_flag or not re.fullmatch(r"[a-f0-9]{32}", sys.argv[4]):
                raise LifecycleError("invalid start transaction arguments")
            token = sys.argv[4]
        if action == "start":
            start(root, owner_token=token)
            print("OCR API and VLM started; waiting for recognition warmup.")
        elif action == "stop":
            if not stop(root, owner_token=token):
                raise LifecycleError("OCR resources remain or ownership is unknown")
        else:
            status(root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("OCR lifecycle refused or failed; check configuration, locked environments and ownership.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
