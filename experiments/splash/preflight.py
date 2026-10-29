#!/usr/bin/env python3
"""Stdlib-only Mac inspection sent on SSH stdin; never invokes lifecycle commands.

Private output includes deployment paths and process arguments. Keep outside git.
The selected MLX asset may be inactive: inventory is not proof of listener identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Environment files are hashed, never sourced or copied to output.
CONFIG_FILES = (
    "etc/models.conf",
    "etc/mlx.env",
    "run/active.env",
    "strata/etc/strata.env",
    "strata/run/strata.pack",
    "decision/etc/decision.env",
    "tts/etc/tts.env",
    "embed/etc/embed.env",
)


def command(*args: str) -> dict[str, Any]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
        }
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "returncode": None, "stdout": ""}


def probe(port: int, path: str) -> dict[str, Any]:
    try:
        # Ignore ambient proxies; these endpoints belong to the inspected Mac.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            raw = response.read(4 * 1024 * 1024)
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            return {"ok": response.status == 200, "status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": None}
    except (OSError, urllib.error.URLError):
        return {"ok": False, "status": None, "body": None}


def file_sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def group_sha(paths: list[Path], root: Path) -> str | None:
    if not paths:
        return None
    # Physical paths are deliberately excluded; the same files can live elsewhere.
    listing = [(str(p.relative_to(root)), file_sha(p)) for p in sorted(paths)]
    return hashlib.sha256(json.dumps(listing, separators=(",", ":")).encode()).hexdigest()


def database_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "state": {}, "active_jobs": None}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            state = {
                key: json.loads(value)
                for key, value in db.execute(
                    "SELECT key,value FROM kv WHERE key IN ('intent','recovery_suspended','sidecar_intent','embed_intent')"
                )
            }
            jobs = list(
                db.execute("SELECT id,status FROM jobs WHERE status IN ('pending','running')")
            )
        return {"ok": True, "state": state, "active_jobs": jobs}
    except (sqlite3.Error, ValueError):
        return {"ok": False, "state": {}, "active_jobs": None}


def semantic_intent(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ("name", "value") if key in value}


def collect(
    root: Path, admin_db: Path, *, hash_weights: bool, model_name: str | None = None
) -> dict[str, Any]:
    issues: list[str] = []
    commands = {
        "os": ("sw_vers",),
        "hardware": ("system_profiler", "SPHardwareDataType"),
        "memory": ("vm_stat",),
        "pressure": ("memory_pressure", "-Q"),
        "swap": ("sysctl", "vm.swapusage"),
        "pressure_level": ("sysctl", "kern.memorystatus_vm_pressure_level"),
        "thermal": ("pmset", "-g", "therm"),
        "disk": ("df", "-k", str(root)),
        "processes": ("ps", "-axo", "pid,ppid,lstart,rss,command"),
        "listeners": ("lsof", "-nP", "-iTCP:8080", "-iTCP:8086", "-sTCP:LISTEN"),
    }
    observations = {name: command(*args) for name, args in commands.items()}
    for name in ("os", "hardware", "memory", "pressure", "swap", "disk", "processes"):
        if not observations[name]["ok"]:
            issues.append(f"unavailable_{name}")
    active: dict[str, str] = {}
    active_file = root / "run/active.env"
    if active_file.is_file():
        for line in active_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"MLX_ACTIVE_NAME", "MLX_ACTIVE_PATH"}:
                active[key] = value.strip().strip("\"'")
    assets: dict[str, Any] = {
        "selected": active,
        "path": None,
        "config": None,
        "metadata_files": {},
        "weights_sha256": None,
        "tokenizer_sha256": None,
        "template_sha256": None,
    }
    registry: dict[str, str] = {}
    if (root / "etc/models.conf").is_file():
        for line in (root / "etc/models.conf").read_text().splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2 and not line.lstrip().startswith("#"):
                registry[fields[0]] = fields[1]
    model_path = root / "models/active"
    if model_name is not None:
        assets["benchmark_target"] = model_name
        if model_name in registry:
            model_path = Path(registry[model_name])
        else:
            issues.append("benchmark_target_not_registered")
            model_path = root / "missing-benchmark-target"
    if model_name is None and not model_path.is_dir() and active.get("MLX_ACTIVE_PATH"):
        model_path = Path(active["MLX_ACTIVE_PATH"])
    if model_path.is_dir():
        model_path = model_path.resolve()
        assets["path"] = str(model_path)
        config = model_path / "config.json"
        if config.is_file():
            assets["config"] = json.loads(config.read_text())
        metadata = sorted(set(model_path.glob("*.json")) | set(model_path.glob("*.jinja")))
        assets["metadata_files"] = {p.name: file_sha(p) for p in metadata}
        assets["tokenizer_sha256"] = group_sha(
            sorted(
                set(model_path.glob("tokenizer*"))
                | set(model_path.glob("vocab*"))
                | set(model_path.glob("merges*"))
            ),
            model_path,
        )
        assets["template_sha256"] = group_sha(
            [p for p in metadata if "template" in p.name or p.name == "tokenizer_config.json"],
            model_path,
        )
        weights = sorted(model_path.glob("*.safetensors")) + sorted(model_path.glob("*.gguf"))
        assets["weight_files"] = {p.name: p.stat().st_size for p in weights}
        if hash_weights:
            assets["weights_sha256"] = group_sha(weights, model_path)
        if not assets["weights_sha256"]:
            issues.append("weights_not_hashed")
    else:
        issues.append("selected_mlx_assets_missing")
    db = database_state(admin_db)
    if not db["ok"]:
        issues.append("admin_state_unavailable")
    if db["active_jobs"] != []:
        issues.append("admin_jobs_active_or_unknown")
    state = db["state"]
    if "intent" not in state:
        issues.append("intent_unavailable")
    config_hashes = {rel: file_sha(root / rel) for rel in CONFIG_FILES if (root / rel).is_file()}
    admin_env = Path.home() / ".config/local-llm-admin/env"
    if admin_env.is_file():
        config_hashes["admin_env"] = file_sha(admin_env)
    if active.get("MLX_ACTIVE_NAME"):
        profile = root / "etc/profiles" / (active["MLX_ACTIVE_NAME"] + ".json")
        if profile.is_file():
            config_hashes["selected_mlx_profile"] = file_sha(profile)
    endpoints = {path: probe(8080, path) for path in ("/health", "/ready", "/v1/models")}
    # These inspect ownership, not just HTTP liveness. The auditor must resolve this
    # inventory against wrapper PID files/start times before marking a snapshot complete.
    issues.append("listener_identity_requires_review")
    process_text = observations["processes"]["stdout"]
    experiment_processes = [
        line
        for line in process_text.splitlines()
        if re.search(r"(?:splash-spike|[/ ]splash(?: |$)|server\.server)", line)
    ]
    with socket.socket() as sock:
        sock.settimeout(1)
        port_open = sock.connect_ex(("127.0.0.1", 8086)) == 0
    return {
        "schema_version": 1,
        "captured_at": time.time(),
        "complete": not issues,
        "issues": issues,
        "observations": observations,
        "assets": assets,
        "registry": registry,
        "admin": db,
        "endpoints": endpoints,
        "restore": {
            "intent": semantic_intent(state.get("intent")),
            "sidecar_intent": semantic_intent(state.get("sidecar_intent")),
            "embed_intent": semantic_intent(state.get("embed_intent")),
            "recovery": state.get("recovery_suspended"),
            "selected": active,
            "config_hashes": config_hashes,
        },
        "serving_ready": None,  # requires a reviewed inference smoke, not HTTP liveness
        "experiment_port_open": port_open,
        "experiment_processes": experiment_processes,
    }


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    try:
        result = collect(
            Path("/Volumes/Models"),
            Path(sys.argv[1]),
            hash_weights=sys.argv[2] == "--hash-weights",
            model_name=sys.argv[3] or None,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError):
        print(json.dumps({"complete": False, "issues": ["preflight_read_failed"]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
