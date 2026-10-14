"""Allow-listed log sources: reverse tail, filter, and follow by byte offset."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import Settings

MLX_LOG_FILES = {
    "mlx-server": "mlx-server.log",
    "chat-ui": "chat-qwen.log",
    "serve": "serve-qwen.log",
}
FIXED_SOURCE_IDS = (*MLX_LOG_FILES, "admin", "comfyui")
# download scripts each write logs/download-<name>.log; ids are the file stems
DOWNLOAD_ID_RE = re.compile(r"^download-[A-Za-z0-9_-]+$")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\b")


def admin_log_path(settings: Settings) -> Path:
    return settings.admin_log_dir / "admin.log"


def download_log_ids(settings: Settings) -> list[str]:
    try:
        stems = [p.stem for p in settings.mlx_log_dir.glob("download-*.log") if p.is_file()]
    except OSError:
        return []
    return sorted(s for s in stems if DOWNLOAD_ID_RE.fullmatch(s))


def source_ids(settings: Settings) -> list[str]:
    """Fixed sources, the decision sidecar's log when enabled, and every
    ``logs/download-*.log`` present now."""
    extra = ["decision"] if settings.decision_enabled else []
    return [*FIXED_SOURCE_IDS, *extra, *download_log_ids(settings)]


def resolve_source(settings: Settings, source_id: str) -> Path:
    """Map an allow-listed id to its path. Raises KeyError for anything else.

    Download ids must match a strict pattern (no dots or slashes) and name an existing
    file in the fixed MLX logs directory, so client input never becomes a free path.
    """
    if source_id == "admin":
        return admin_log_path(settings)
    if source_id == "comfyui":
        return settings.comfyui_root / "logs" / "comfyui.log"
    if source_id == "decision" and settings.decision_enabled:
        return settings.decision_root / "logs" / "openjev.log"
    if source_id in MLX_LOG_FILES:
        return settings.mlx_log_dir / MLX_LOG_FILES[source_id]
    if DOWNLOAD_ID_RE.fullmatch(source_id):
        path = settings.mlx_log_dir / f"{source_id}.log"
        if path.is_file():
            return path
    raise KeyError(source_id)


def normalise_line(line: str) -> str:
    """curl progress rewrites a line with ``\\r``; keep the last non-empty segment."""
    line = line.rstrip("\n")
    segments = [s for s in line.split("\r") if s.strip()]
    return segments[-1].rstrip() if segments else ""


def tail_lines(path: Path, n: int, block_size: int = 65536) -> list[str]:
    """Return the last ``n`` lines, reading backwards in blocks (never the whole file)."""
    if n <= 0:
        return []
    try:
        fh = path.open("rb")
    except OSError:  # missing, or unreadable (e.g. macOS denied the volume)
        return []
    with fh:
        end = fh.seek(0, os.SEEK_END)
        pos = end
        data = b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block_size, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if pos > 0:
        lines = lines[1:]  # first line may be partial
    return [normalise_line(line) for line in lines[-n:]]


def line_level(line: str) -> str | None:
    m = _LEVEL_RE.search(line)
    if not m:
        return None
    return "WARNING" if m.group(1) == "WARN" else m.group(1)


def filter_lines(lines: list[str], grep: str | None, level: str | None) -> list[str]:
    """Case-insensitive substring match (not regex) and a minimum level."""
    if level is not None and level not in _LEVELS:
        raise ValueError(f"unknown level: {level}")
    needle = grep.lower() if grep else None
    min_level = _LEVELS[level] if level else 0
    out: list[str] = []
    for line in lines:
        if needle and needle not in line.lower():
            continue
        if min_level > _LEVELS["INFO"]:
            lvl = line_level(line)
            if lvl is None or _LEVELS[lvl] < min_level:
                continue
        out.append(line)
    return out


def file_info(path: Path) -> dict[str, int | bool]:
    try:
        return {"exists": True, "size": path.stat().st_size}
    except OSError:
        return {"exists": False, "size": 0}


class Follower:
    """Tails a file by byte offset; survives truncation and rotation (inode change)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._inode: int | None = None
        self._offset = 0
        self._partial = ""
        try:
            st = path.stat()
            self._inode, self._offset = st.st_ino, st.st_size
        except OSError:
            pass

    def poll(self) -> list[str]:
        try:
            st = self.path.stat()
        except OSError:
            self._inode, self._offset, self._partial = None, 0, ""
            return []
        if st.st_ino != self._inode or st.st_size < self._offset:
            self._inode, self._offset, self._partial = st.st_ino, 0, ""
        if st.st_size == self._offset:
            return []
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read(st.st_size - self._offset)
        self._offset += len(chunk)
        text = self._partial + chunk.decode("utf-8", errors="replace")
        parts = text.split("\n")
        self._partial = parts.pop()
        return [normalise_line(p) for p in parts]


_DL_START_RE = re.compile(r"^=====\s+(\S+ \S+)\s+start\s+=====")
_DL_DONE_RE = re.compile(r"^=====\s+(\S+ \S+)\s+done\s+fail=(\d+)\s+=====")
# curl meter: %  Total  %  Received  %  Xferd  AvgDload  AvgUpload  Total  Spent  Left  Speed
_CURL_RE = re.compile(
    r"^\s*(\d{1,3})\s+(\S+)\s+\d{1,3}\s+(\S+)\s+\d{1,3}\s+\S+\s+\S+\s+\S+"
    r"\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s*$"
)


def download_summary(lines: list[str]) -> dict[str, Any]:
    """Runs of ``download-models.log`` and the latest curl progress line.

    Downloads run in parallel, so progress lines cannot be tied to one file reliably;
    the summary reports per run (files requested, start/finish, failures) instead.
    """
    runs: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    for line in lines:
        if m := _DL_START_RE.match(line):
            runs.append({"started": m.group(1), "finished": None, "failed": None, "files": []})
        elif m := _DL_DONE_RE.match(line):
            if runs:
                runs[-1].update(finished=m.group(1), failed=int(m.group(2)))
        elif line.startswith("GET ") and runs:
            runs[-1]["files"].append(line[4:].strip())
        elif m := _CURL_RE.match(line):
            last = {
                "percent": int(m.group(1)),
                "total": m.group(2),
                "received": m.group(3),
                "left": m.group(4),
                "speed": m.group(5),
            }
    return {
        "runs": runs,
        "active": bool(runs) and runs[-1]["finished"] is None,
        "last_progress": last,
    }
