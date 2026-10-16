"""Read-only view of the MLX server: mlx-model CLI output, run/ files, logs, /health.

Nothing here writes under the MLX root. Lifecycle actions only build argv for
``mlx-model``; ``jobs.py`` runs them.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil

from .config import Settings
from .logs import tail_lines

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ACTIONS_WITH_NAME = {
    "switch": True,
    "start": None,
    "stop": False,
    "unlock": False,
    "list": False,
    "status": False,
    "add": True,
    "select": True,
}
_RECENT_WRITE_S = 120.0  # a weights dir written to this recently counts as downloading
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_RATE_RE = re.compile(r"rate=([0-9.]+)\s*tok/s")
_GEN_RE = re.compile(r"generated_tokens=(\d+)")
_TTFT_RE = re.compile(r"time_to_first_token=([0-9.]+)s")
_IN_FLIGHT_RE = re.compile(r"\bin_flight=(\d+)")
_PROCESS_RE = re.compile(r"^process:\s*(running|stopped)(?:\s+pid\s+(\d+))?", re.M)
_START_MARKER_RE = re.compile(r"^=====\s+(.+?)\s+start\s+(\S+)\s+=====\s*$")
_REQUEST_END_RE = re.compile(
    r"Decode completed|Request (completed|failed|cancelled)|Generation (cancelled|failed)"
)
_HEALTHY = {"ok", "healthy", "ready", "serving"}
_ERROR = {"error", "failed", "unhealthy"}


@dataclass(frozen=True)
class ModelEntry:
    name: str
    dir: str
    selected: bool


@dataclass(frozen=True)
class ProcessStatus:
    running: bool
    pid: int | None


@dataclass(frozen=True)
class InferenceStats:
    last_decode_rate: float | None = None
    last_prefill_rate: float | None = None
    last_generated_tokens: int | None = None
    last_request_at: str | None = None
    in_flight: bool = False
    in_flight_count: int | None = None
    last_ttft_s: float | None = None


def validate_name(name: str) -> str:
    """Return ``name`` if it is a safe model name, else raise ValueError."""
    if not NAME_RE.fullmatch(name) or name.startswith("-") or ".." in name or name == ".":
        raise ValueError(f"invalid model name: {name!r}")
    return name


def build_command(
    bin_path: Path, action: str, name: str | None, path: str | None = None
) -> list[str]:
    """Fixed argv for ``mlx-model``. Never passed through a shell."""
    if action not in _ACTIONS_WITH_NAME:
        raise ValueError(f"unsupported action: {action}")
    if (action == "add") != (path is not None):
        raise ValueError("a directory is required for add, and only for add")
    if path is not None and not path.startswith("/"):
        raise ValueError("model directory must be an absolute path")
    needs = _ACTIONS_WITH_NAME[action]
    if needs is True and name is None:
        raise ValueError(f"{action} requires a model name")
    if needs is False and name is not None:
        raise ValueError(f"{action} takes no model name")
    argv = [str(bin_path), action]
    if name is not None:
        argv.append(validate_name(name))
    if path is not None:
        argv.append(path)
    return argv


def suggest_name(dir_name: str) -> str:
    """A registry name for a weights directory: lowercase, safe characters only."""
    name = re.sub(r"[^a-z0-9._]+", "-", dir_name.lower()).strip("-.")
    name = re.sub(r"\.{2,}", ".", name)
    return name or "model"


def weights_state(d: Path, now: float, recent_s: float = _RECENT_WRITE_S) -> dict[str, Any]:
    """Is a weights directory complete, still downloading, or incomplete?

    curl writes shards straight to their final names, so presence is not enough:
    shard sizes are compared with ``metadata.total_size`` in the index (the tensors'
    byte count; each file adds a small header, so complete files sum slightly above it).
    """
    out: dict[str, Any] = {
        "state": "no-weights",
        "present_bytes": 0,
        "expected_bytes": None,
        "progress": None,
        "missing_shards": 0,
        "last_write_ago_s": None,
    }
    try:
        entries = list(d.iterdir())
    except OSError:
        return out
    mtimes = []
    for e in entries:
        try:
            mtimes.append(e.stat().st_mtime)
        except OSError:
            continue
    if mtimes:
        out["last_write_ago_s"] = max(0.0, now - max(mtimes))
    recent = out["last_write_ago_s"] is not None and out["last_write_ago_s"] < recent_s
    index = d / "model.safetensors.index.json"
    if index.exists():
        try:
            doc = json.loads(index.read_text())
            shards = sorted(set(doc["weight_map"].values()))
            total = (doc.get("metadata") or {}).get("total_size")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            out["state"] = "unknown"
            return out
    elif (d / "model.safetensors").exists():
        shards, total = ["model.safetensors"], None
    else:
        return out
    present = missing = 0
    for f in shards:
        try:
            present += (d / f).stat().st_size
        except OSError:
            missing += 1
    out.update(present_bytes=present, missing_shards=missing)
    if isinstance(total, int) and total > 0:
        out["expected_bytes"] = total
        out["progress"] = round(min(1.0, present / total), 3)
    short = isinstance(total, int) and present < total
    if missing or short:
        out["state"] = "downloading" if recent else "incomplete"
    else:
        out["state"] = "complete"
    return out


def discover_models(root: Path, registered: dict[str, str], now: float) -> list[dict[str, Any]]:
    """Every weights directory under ``models/`` (symlinks such as ``active`` skipped)."""
    try:
        dirs = [
            p
            for p in root.iterdir()
            if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
        ]
    except OSError:
        return []
    out = []
    for d in sorted(dirs, key=lambda p: p.name.lower()):
        try:
            cfg = json.loads((d / "config.json").read_text())
            summary = summarize_model_config(cfg if isinstance(cfg, dict) else {})
            has_config = True
        except (OSError, ValueError):
            summary, has_config = summarize_model_config({}), (d / "config.json").exists()
        out.append(
            {
                "dir_name": d.name,
                "path": str(d),
                "registered_as": registered.get(str(d.resolve())),
                "suggested_name": suggest_name(d.name),
                "has_config": has_config,
                **summary,
                **weights_state(d, now),
            }
        )
    return out


def parse_active_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def parse_model_list(text: str) -> list[ModelEntry]:
    """Parse ``mlx-model list``: ``[*] name <ws> dir`` per line."""
    entries: list[ModelEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        selected = line.startswith("*")
        if selected:
            line = line[1:].strip()
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name, path = parts[0], parts[1].strip()
        if not NAME_RE.fullmatch(name) or not path.startswith("/"):
            continue
        entries.append(ModelEntry(name=name, dir=path, selected=selected))
    return entries


def parse_status(text: str) -> ProcessStatus:
    m = _PROCESS_RE.search(text)
    if m:  # ``mlx-model status``: "process: running pid N" or "process: stopped"
        running = m.group(1) == "running"
        return ProcessStatus(running, int(m.group(2)) if running and m.group(2) else None)
    low = text.lower()
    running = bool(re.search(r"\brunning\b", low)) and not re.search(
        r"\bnot running\b|\bstopped\b", low
    )
    m = re.search(r"pid\W{0,3}(\d+)", low)
    return ProcessStatus(running=running, pid=int(m.group(1)) if running and m else None)


def parse_server_log(lines: Iterable[str]) -> InferenceStats:
    """Latest rates, TTFT and in-flight state from ``mlx-server.log`` lines.

    The server logs ``in_flight=N`` on request start/complete/fail lines; once seen, that
    counter is authoritative (continuous batching can overlap requests). Older logs
    without it fall back to pairing ``Request started`` with the end lines.
    """
    decode = prefill = ttft = None
    gen: int | None = None
    last_at: str | None = None
    in_flight = False
    count: int | None = None
    for line in lines:
        counter = _IN_FLIGHT_RE.search(line)
        if "Request started" in line:
            ts = _TS_RE.match(line)
            last_at = ts.group(1).replace("T", " ") if ts else last_at
        if counter:
            count = int(counter.group(1))
            in_flight = count > 0
        elif count is None:
            if "Request started" in line:
                in_flight = True
            elif _REQUEST_END_RE.search(line):
                in_flight = False
        if "Prefill completed" in line:
            m = _RATE_RE.search(line)
            prefill = float(m.group(1)) if m else prefill
        elif "Decode completed" in line:
            m = _RATE_RE.search(line)
            decode = float(m.group(1)) if m else decode
            g = _GEN_RE.search(line)
            gen = int(g.group(1)) if g else gen
        elif "Decode started" in line:
            t = _TTFT_RE.search(line)
            ttft = float(t.group(1)) if t else ttft
    return InferenceStats(decode, prefill, gen, last_at, in_flight, count, ttft)


def parse_start_markers(lines: Iterable[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in lines:
        m = _START_MARKER_RE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def summarize_model_config(cfg: dict[str, Any]) -> dict[str, Any]:
    archs = cfg.get("architectures")
    arch = archs[0] if isinstance(archs, list) and archs else cfg.get("model_type")
    quant = cfg.get("quantization") or cfg.get("quantization_config") or {}
    bits = quant.get("bits") if isinstance(quant, dict) else None
    mpe = cfg.get("max_position_embeddings")
    if mpe is None and isinstance(cfg.get("text_config"), dict):
        mpe = cfg["text_config"].get("max_position_embeddings")
    return {"architecture": arch, "quant_bits": bits, "max_position_embeddings": mpe}


def is_serving(health: dict[str, Any] | None) -> bool:
    status = str((health or {}).get("status", "")).lower()
    return health is not None and status in _HEALTHY and bool(health.get("loaded_model"))


def resolve_state(
    *,
    drive_mounted: bool,
    job_running: bool,
    process_running: bool,
    chat_ui: bool,
    health: dict[str, Any] | None,
) -> str:
    """One badge state: serving|loading|switching|stopped|chat-ui|drive-missing|error."""
    if not drive_mounted:
        return "drive-missing"
    if job_running:
        return "switching"
    if is_serving(health):
        return "serving"
    status = str((health or {}).get("status", "")).lower()
    if not process_running:
        return "chat-ui" if chat_ui else "stopped"
    if status in _ERROR:
        return "error"
    return "loading"


class MlxClient:
    """Gathers status through the four allowed interfaces: CLI, HTTP API, read-only files, OS."""

    SIZE_CACHE_TTL_S = 15.0  # short: sizes of downloading dirs should move

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._size_cache: dict[str, tuple[float, int]] = {}

    # --- CLI -------------------------------------------------------------
    def run_cli(self, action: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        argv = build_command(self.settings.mlx_model_path, action, None)
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)

    def list_models(self) -> list[dict[str, Any]]:
        try:
            proc = self.run_cli("list")
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        out: list[dict[str, Any]] = []
        for e in parse_model_list(proc.stdout):
            d = Path(e.dir)
            exists = d.is_dir()
            out.append(
                {
                    "name": e.name,
                    "dir": e.dir,
                    "selected": e.selected,
                    "dir_exists": exists,
                    "has_config": (d / "config.json").is_file() if exists else False,
                    "size_bytes": self.dir_size(d) if exists else None,
                    "weights": weights_state(d, time.time()) if exists else None,
                }
            )
        return out

    def discover(self, registered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Weights dirs on disk, read fresh on every call (downloads show up live)."""
        by_path = {}
        for m in registered:
            try:
                by_path[str(Path(m["dir"]).resolve())] = m["name"]
            except OSError:
                continue
        return discover_models(self.settings.mlx_root / "models", by_path, time.time())

    def process_status(self) -> ProcessStatus:
        try:
            proc = self.run_cli("status", timeout=10.0)
            if proc.returncode in (0, 1, 3) and proc.stdout.strip():
                return parse_status(proc.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
        pid = self.pid_from_file()
        if pid is not None and psutil.pid_exists(pid):
            return ProcessStatus(True, pid)
        return ProcessStatus(False, None)

    # --- files -----------------------------------------------------------
    def drive_mounted(self) -> bool:
        root = self.settings.mlx_root
        if not root.is_dir():
            return False
        return os.path.ismount(root) or not self.settings.mlx_root_must_be_mount

    def engine_running(self) -> bool:
        """MLX owns the GPU if its pid is alive, :8080 is listening, or Chat UI mode
        (chat-qwen.sh on :7860, a loaded model with the API down) is up. CLI-independent;
        ``mlx-model stop`` stops all three. Strata serves on the same port when it is the
        engine: while its pid file names a live process, the port is Strata's."""
        pid = self.pid_from_file()
        if pid is not None and psutil.pid_exists(pid):
            return True
        if self.port_listening(self.settings.chat_ui_port):
            return True
        if self.settings.strata_port == self.settings.mlx_port and self.strata_pid_alive():
            return False
        return self.port_listening(self.settings.mlx_port)

    def strata_pid_alive(self) -> bool:
        try:
            pid = int((self.settings.strata_root / "run" / "strata.pid").read_text().strip())
        except (OSError, ValueError):
            return False
        return psutil.pid_exists(pid)

    def lock_exists(self) -> bool:
        # mlx-model takes the lock with `mkdir run/mlx.lock`
        try:
            return (self.settings.run_dir / "mlx.lock").exists()
        except OSError:
            return False

    def access_error(self) -> str | None:
        """macOS privacy control (TCC) can deny a LaunchAgent access to an external volume."""
        try:
            os.listdir(self.settings.run_dir)
        except PermissionError:
            exe = os.path.realpath(sys.executable)
            return (
                f"Permission denied reading {self.settings.mlx_root}. On macOS, allow {exe} in"
                " System Settings > Privacy & Security > Files and Folders > Removable Volumes"
                " (or Full Disk Access), then restart the admin."
            )
        except OSError:
            return None
        return None

    def lock_age_s(self) -> float | None:
        try:
            return max(0.0, time.time() - (self.settings.run_dir / "mlx.lock").stat().st_mtime)
        except OSError:
            return None

    def pid_from_file(self) -> int | None:
        try:
            return int((self.settings.run_dir / "mlx-server.pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def active_env(self) -> dict[str, str]:
        try:
            return parse_active_env((self.settings.run_dir / "active.env").read_text())
        except OSError:
            return {}

    def dir_size(self, path: Path) -> int:
        key = str(path.resolve())
        now = time.monotonic()
        hit = self._size_cache.get(key)
        if hit and now - hit[0] < self.SIZE_CACHE_TTL_S:
            return hit[1]
        total = 0
        for dirpath, _dirs, files in os.walk(key):
            for f in files:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    continue
        self._size_cache[key] = (now, total)
        return total

    def model_details(self, path: str) -> dict[str, Any]:
        d = Path(path)
        try:
            cfg = json.loads((d / "config.json").read_text())
            summary = summarize_model_config(cfg if isinstance(cfg, dict) else {})
        except (OSError, ValueError):
            summary = summarize_model_config({})
        summary["size_bytes"] = self.dir_size(d) if d.is_dir() else None
        return summary

    def inference_stats(self, lines: int = 2000) -> InferenceStats:
        return parse_server_log(tail_lines(self.settings.mlx_log_dir / "mlx-server.log", lines))

    # --- network ---------------------------------------------------------
    async def health(self) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=2.0) as http:
                resp = await http.get(f"{self.settings.mlx_api_base}/health")
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("service") == "strata":
            return None  # Strata on the shared port is not the MLX server
        return data

    @staticmethod
    def port_listening(port: int, host: str = "127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    async def status(self, job_running: bool) -> dict[str, Any]:
        health = await self.health()
        mounted = self.drive_mounted()
        chat_ui = await asyncio.to_thread(self.port_listening, self.settings.chat_ui_port)
        env: dict[str, str] = {}
        proc = ProcessStatus(False, None)
        stats = InferenceStats()
        model: dict[str, Any] = {}
        if mounted:
            env = self.active_env()
            proc = await asyncio.to_thread(self.process_status)
            stats = await asyncio.to_thread(self.inference_stats)
            path = env.get("MLX_ACTIVE_PATH")
            if path:
                model = await asyncio.to_thread(self.model_details, path)
        state = resolve_state(
            drive_mounted=mounted,
            job_running=job_running,
            process_running=proc.running,
            chat_ui=chat_ui,
            health=health,
        )
        return {
            "state": state,
            "selected": env.get("MLX_ACTIVE_NAME"),
            "selected_path": env.get("MLX_ACTIVE_PATH"),
            "drive_mounted": mounted,
            "access_error": self.access_error() if mounted else None,
            "lock": mounted and self.lock_exists(),
            "lock_age_s": self.lock_age_s() if mounted else None,
            "process": {"running": proc.running, "pid": proc.pid},
            "health": health,
            "chat_ui": chat_ui,
            "inference": asdict(stats),
            "model": model,
            "checked_at": time.time(),
        }
