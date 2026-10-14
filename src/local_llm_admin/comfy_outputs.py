"""Read-only browsing of ComfyUI outputs (M5).

Files are addressed by opaque ids that map, server side, to a relative path plus the
device/inode seen at scan time. Clients never send paths. Serving opens the file with
``O_NOFOLLOW``, then checks the opened descriptor is a regular file with the recorded
identity, so a file swapped for a symlink or another file after the scan is refused.
Symlinks are never listed or followed. Only allow-listed image/video types are served.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class OutputGone(Exception):
    """The file behind an id is missing or is no longer the file that was scanned."""


class RangeInvalid(Exception):
    """An unsatisfiable byte range (HTTP 416)."""


@dataclass(frozen=True)
class Entry:
    id: str
    rel: str  # POSIX path relative to the output root, from the scan only
    size: int
    mtime: float
    dev: int
    ino: int

    @property
    def mime(self) -> str:
        return MIME[os.path.splitext(self.rel)[1].lower()]

    @property
    def kind(self) -> str:
        return self.mime.split("/")[0]

    def public(self) -> dict[str, Any]:
        folder, _, name = self.rel.rpartition("/")
        return {
            "id": self.id,
            "name": name,
            "subfolder": folder,
            "kind": self.kind,
            "mime": self.mime,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass(frozen=True)
class Scan:
    entries: list[Entry]  # newest first
    truncated: bool  # the scan stopped at its entry budget
    skipped: int  # unsupported types, symlinks, special files, unreadable entries
    scanned_at: float


def make_id(secret: bytes, rel: str, dev: int, ino: int) -> str:
    msg = f"{rel}\0{dev}\0{ino}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def scan(root: Path, secret: bytes, *, max_entries: int, max_depth: int) -> Scan:
    """Bounded walk of ``root``: at most ``max_entries`` directory entries are examined
    and at most ``max_depth`` directory levels are entered. Raises ``FileNotFoundError``
    if the root is missing (for example, the drive is not mounted)."""
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    entries: list[Entry] = []
    skipped = seen = 0
    truncated = False
    stack: list[tuple[str, str, int]] = [(str(root), "", 0)]
    while stack and not truncated:
        path, rel_dir, depth = stack.pop()
        try:
            it = os.scandir(path)
        except OSError:
            skipped += 1
            continue
        with it:
            for de in it:
                seen += 1
                if seen > max_entries:
                    truncated = True
                    break
                if de.name.startswith("."):
                    continue
                rel = f"{rel_dir}/{de.name}" if rel_dir else de.name
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if depth < max_depth:
                        stack.append((de.path, rel, depth + 1))
                    continue
                ext = os.path.splitext(de.name)[1].lower()
                if not stat.S_ISREG(st.st_mode) or ext not in MIME:
                    skipped += 1  # symlinks and special files included
                    continue
                entries.append(
                    Entry(
                        make_id(secret, rel, st.st_dev, st.st_ino),
                        rel,
                        st.st_size,
                        st.st_mtime,
                        st.st_dev,
                        st.st_ino,
                    )
                )
    entries.sort(key=lambda e: (-e.mtime, e.rel))
    return Scan(entries, truncated, skipped, time.time())


def open_entry(root: Path, entry: Entry) -> tuple[int, os.stat_result]:
    """Open the scanned file read-only; verify it is still that regular file."""
    parts = entry.rel.split("/")
    if entry.rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise OutputGone("invalid path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(root.joinpath(*parts), flags)
    except OSError as exc:
        raise OutputGone(str(exc)) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (entry.dev, entry.ino):
            raise OutputGone("file changed since it was listed")
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """A single ``bytes=`` range -> inclusive (start, end); None serves the whole file.

    Multi-range and non-byte units are ignored (allowed by RFC 9110); a malformed or
    unsatisfiable byte range raises ``RangeInvalid``."""
    if not header:
        return None
    header = header.strip()
    if not header.startswith("bytes=") or "," in header:
        return None
    m = _RANGE_RE.match(header)
    if not m or (not m.group(1) and not m.group(2)):
        raise RangeInvalid(header)
    first, last = m.group(1), m.group(2)
    if not first:  # suffix: the last N bytes
        n = int(last)
        if n == 0 or size == 0:
            raise RangeInvalid(header)
        return max(0, size - n), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or (last and int(last) < start):
        raise RangeInvalid(header)
    return start, end


class FileStream:
    """Iterates ``length`` bytes from ``start`` in bounded chunks and owns ``fd``.

    A generator would not run its ``finally`` if the response were dropped before the
    first chunk, so the descriptor is closed on exhaustion, ``close()`` or collection."""

    def __init__(self, fd: int, start: int, length: int) -> None:
        self._fd: int | None = fd
        self._remaining = length
        self._started = False
        self._start = start

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._fd is None or self._remaining <= 0:
            self.close()
            raise StopIteration
        try:
            if not self._started:
                os.lseek(self._fd, self._start, os.SEEK_SET)
                self._started = True
            chunk = os.read(self._fd, min(CHUNK, self._remaining))
        except OSError:
            self.close()
            raise
        if not chunk:  # truncated since it was opened
            self.close()
            raise StopIteration
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __del__(self) -> None:
        self.close()


def history_index(history: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    """``GET /history`` -> {(subfolder, filename): {job_id, finished}} for saved outputs.

    Only ``type == "output"`` items are indexed; prompts and workflows are dropped."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(history, dict):
        return out
    for job_id, item in history.items():
        if not isinstance(item, dict):
            continue
        finished = None
        status = item.get("status")
        messages = status.get("messages") if isinstance(status, dict) else None
        for msg in messages if isinstance(messages, list) else []:
            if (
                isinstance(msg, list)
                and len(msg) >= 2
                and msg[0] in ("execution_success", "execution_error", "execution_interrupted")
                and isinstance(msg[1], dict)
                and isinstance(msg[1].get("timestamp"), (int, float))
            ):
                finished = msg[1]["timestamp"] / 1000.0
        outputs = item.get("outputs")
        for node in outputs.values() if isinstance(outputs, dict) else []:
            if not isinstance(node, dict):
                continue
            for files in node.values():
                for f in files if isinstance(files, list) else []:
                    if (
                        isinstance(f, dict)
                        and f.get("type") == "output"
                        and isinstance(f.get("filename"), str)
                    ):
                        raw_sub = f.get("subfolder")
                        sub = raw_sub if isinstance(raw_sub, str) else ""
                        out[(sub, f["filename"])] = {"job_id": str(job_id), "finished": finished}
    return out


class OutputIndex:
    """The latest scan, shared by listing and content requests."""

    def __init__(self, root: Path, *, max_entries: int, max_depth: int, ttl_s: float) -> None:
        self.root = root
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.ttl_s = ttl_s
        self._secret = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._scan: Scan | None = None
        self._by_id: dict[str, Entry] = {}
        self._at = 0.0

    def fresh(self) -> Scan | None:
        with self._lock:
            if self._scan and time.monotonic() - self._at < self.ttl_s:
                return self._scan
        return None

    def rescan(self) -> Scan:
        result = scan(
            self.root, self._secret, max_entries=self.max_entries, max_depth=self.max_depth
        )
        with self._lock:
            self._scan = result
            self._by_id = {e.id: e for e in result.entries}
            self._at = time.monotonic()
        return result

    def lookup(self, entry_id: str) -> Entry | None:
        with self._lock:
            return self._by_id.get(entry_id)
