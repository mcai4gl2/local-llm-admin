"""Durable, bounded log ingestion: byte cursors per source plus parsed events.

Separate from ``logs.Follower`` (the browser's live view, which starts at EOF and keeps
state in memory). Each poll reads at most ``batch_bytes``; complete lines are parsed and
the events plus the new cursor commit in one transaction. Event rows are keyed by the
line's file identity and byte offset, so a replayed range inserts nothing.

A file is (st_dev, inode). macOS gives a remounted drive a new st_dev while inodes stay,
so the cursor also keeps a fingerprint of the file's first bytes: same inode, matching
fingerprint and no shrinkage on another device is the same file, and its identity (the
prefix of every dedup key) is kept. Without that, a remount replays the whole log.

A ``sink`` receives the parsed events inside that same transaction (M4 request rows).
When the file is renamed away (rotation) and ``archives`` lists the renamed file, its
unread tail is drained first, so a rotation after the writer stopped loses nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .logs import download_log_ids
from .storage import Database

ParsedEvent = dict[str, Any]  # kind, message, ts (float | None), data (optional dict)
Parser = Callable[[str], ParsedEvent | None]
# sink(conn, events, gap): runs in the cursor's transaction; gap = unread lines may be lost
Sink = Callable[[sqlite3.Connection, list[dict[str, Any]], bool], None]

BATCH_BYTES = 256 * 1024
_ARCHIVE_SUFFIX = re.compile(r"\.\d{8}-\d{6}$")  # mlx-model rotate-logs names
MAX_LINE = 16 * 1024
BACKFILL_BYTES = 1024 * 1024
HEAD_BYTES = 4096
_ELLIPSIS = "…"


def file_head(fd: int, size: int) -> str | None:
    """``"<n>:<sha256>"`` of the first ``min(size, HEAD_BYTES)`` bytes; None if empty."""
    n = min(size, HEAD_BYTES)
    if n <= 0:
        return None
    data = os.pread(fd, n, 0)
    return f"{len(data)}:{hashlib.sha256(data).hexdigest()}"


def _head_len(head: str | None) -> int:
    try:
        return int((head or "").split(":", 1)[0])
    except ValueError:
        return 0


def head_matches(fd: int, size: int, head: str | None) -> bool:
    """Whether the open file starts with the bytes ``head`` fingerprinted."""
    n = _head_len(head)
    if n <= 0 or size < n:
        return False
    return file_head(fd, n) == head


def same_file(cur: dict[str, Any], fd: int, st: os.stat_result) -> bool:
    """The cursor's file: same (st_dev, inode), or, after a remount changed st_dev, the
    same inode with the recorded fingerprint and at least the bytes already read."""
    if st.st_ino != cur["inode"]:
        return False
    if st.st_dev == cur["dev"]:
        return True
    return st.st_size >= cur["offset"] and head_matches(fd, st.st_size, cur.get("head"))


@dataclass
class PollResult:
    ok: bool  # the file could be read
    caught_up: bool = False  # the cursor reached the end of the file
    events: list[dict[str, Any]] = field(default_factory=list)


class IngestStore(Database):
    def cursor(self, source: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM log_cursors WHERE source = ?", (source,)).fetchone()
        return dict(row) if row else None

    def set_cursor_offset(self, source: str, offset: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE log_cursors SET offset = ?, carry = x'', skipping = 0 WHERE source = ?",
                (offset, source),
            )

    @staticmethod
    def _save_cursor(c: Any, source: str, cur: dict[str, Any]) -> None:
        c.execute(
            "INSERT INTO log_cursors (source, dev, inode, offset, carry, skipping, gap_at,"
            " updated, head) VALUES (:source, :dev, :inode, :offset, :carry, :skipping,"
            " :gap_at, :updated, :head)"
            " ON CONFLICT(source) DO UPDATE SET dev = excluded.dev, inode = excluded.inode,"
            " offset = excluded.offset, carry = excluded.carry, skipping = excluded.skipping,"
            " gap_at = excluded.gap_at, updated = excluded.updated, head = excluded.head",
            {"head": None, **cur, "source": source},
        )

    def commit(
        self,
        source: str,
        cur: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        sink: Sink | None = None,
        gap: bool = False,
        store_events: bool = True,
    ) -> list[dict[str, Any]]:
        inserted: list[dict[str, Any]] = []
        with self._conn() as c:
            if not store_events:
                inserted = [{**ev, "source": source} for ev in events]
            for ev in events if store_events else ():
                row = c.execute(
                    "INSERT OR IGNORE INTO log_events (source, dedup, ts, observed, kind,"
                    " signature, message, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source,
                        ev["dedup"],
                        ev["ts"],
                        ev["observed"],
                        ev["kind"],
                        ev.get("signature"),
                        ev["message"],
                        json.dumps(ev.get("data") or {}),
                    ),
                )
                if row.rowcount:
                    inserted.append({**ev, "id": row.lastrowid, "source": source})
            if sink is not None:
                sink(c, inserted, gap)
            self._save_cursor(c, source, cur)
        return inserted

    def events(
        self,
        *,
        kinds: tuple[str, ...],
        since: float = 0.0,
        source: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(kinds))
        sql = f"SELECT * FROM log_events WHERE kind IN ({marks}) AND ts >= ?"
        args: list[Any] = [*kinds, since]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def prune_events(self, before: float, limit: int = 5000) -> int:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM log_events WHERE id IN (SELECT id FROM log_events WHERE ts < ?"
                " ORDER BY id LIMIT ?)",
                (before, limit),
            )
        return cur.rowcount


class Ingestor:
    def __init__(
        self,
        store: IngestStore,
        source: str,
        path: Path,
        parse: Parser,
        *,
        batch_bytes: int = BATCH_BYTES,
        max_line: int = MAX_LINE,
        backfill_bytes: int = BACKFILL_BYTES,
        max_age_s: float | None = None,
        line_ts: Callable[[str], float | None] | None = None,
        now: Callable[[], float] = time.time,
        sink: Sink | None = None,
        store_events: bool = True,
        archives: Callable[[], list[Path]] | None = None,
    ) -> None:
        self.store = store
        self.source = source
        self.path = path
        self.parse = parse
        self.batch_bytes = batch_bytes
        self.max_line = max_line
        self.backfill_bytes = backfill_bytes
        self.max_age_s = max_age_s
        self.now = now
        # max_age_s filters only the first-sight backfill, never lines written later
        self._backfill_end = 0
        # backfilled lines without their own timestamp take the last one seen before them
        self.line_ts = line_ts
        self._last_ts: float | None = None
        self.sink = sink
        self.store_events = store_events
        self.archives = archives

    def _find_archive(self, cur: dict[str, Any]) -> tuple[Any, os.stat_result] | None:
        """The renamed file the cursor was reading, if ``archives`` still lists it."""
        for path in self.archives() if self.archives else []:
            try:
                fh = path.open("rb")
            except OSError:
                continue
            st = os.fstat(fh.fileno())
            if same_file(cur, fh.fileno(), st):
                return fh, st
            fh.close()
        return None

    def _flush(self, cur: dict[str, Any], now: float, out: list[dict[str, Any]]) -> dict[str, Any]:
        """The writer is gone: an unterminated last line of the old file is complete."""
        carry = bytes(cur["carry"])
        if carry and not cur["skipping"]:
            identity = f"{cur['dev']}:{cur['inode']}:{cur['gap_at'] or 0}"
            out.extend(self._events(carry, cur["offset"] - len(carry), identity, now))
        return {**cur, "carry": b"", "skipping": 0}

    def poll(self) -> PollResult:
        now = self.now()
        try:
            fh = self.path.open("rb")
        except OSError:
            return PollResult(ok=False)
        events: list[dict[str, Any]] = []
        gap = draining = False
        with contextlib.ExitStack() as stack:
            stack.enter_context(fh)
            st = os.fstat(fh.fileno())
            cur = self.store.cursor(self.source)
            if cur is None:
                offset = max(0, st.st_size - self.backfill_bytes)
                skipping = False
                self._backfill_end = st.st_size
                if offset > 0:  # a line cut by the starting point is skipped whole
                    fh.seek(offset - 1)
                    skipping = fh.read(1) != b"\n"
                cur = {
                    "dev": st.st_dev,
                    "inode": st.st_ino,
                    "offset": offset,
                    "carry": b"",
                    "skipping": int(skipping),
                    "gap_at": None,
                    "head": None,
                }
            elif not same_file(cur, fh.fileno(), st):
                old = self._find_archive(cur)
                if old is not None:
                    stack.enter_context(old[0])
                if old is not None and old[1].st_size > cur["offset"]:
                    fh, st, draining = old[0], old[1], True  # finish the renamed file first
                else:
                    if old is not None:
                        cur = self._flush(cur, now, events)
                    gap = old is None  # renamed away unseen: its unread tail may be lost
                    cur = {
                        **cur,
                        "dev": st.st_dev,
                        "inode": st.st_ino,
                        "offset": 0,
                        "carry": b"",
                        "skipping": 0,
                        "gap_at": now if gap else cur["gap_at"],
                        "head": None,
                    }
            elif st.st_size < cur["offset"]:
                # truncated: an unread tail of the old content may be lost
                gap = True
                cur = {
                    **cur,
                    "offset": 0,
                    "carry": b"",
                    "skipping": 0,
                    "gap_at": now,
                    "head": None,
                }
            if _head_len(cur.get("head")) < min(st.st_size, HEAD_BYTES):
                # first sight, a cursor from before schema 9, or a file still shorter
                # than HEAD_BYTES when last fingerprinted; ``fh`` is the cursor's file here
                cur = {**cur, "head": file_head(fh.fileno(), st.st_size)}
            offset = cur["offset"]
            fh.seek(offset)
            chunk = fh.read(min(self.batch_bytes, st.st_size - offset))
        carry: bytes = bytes(cur["carry"])
        skipping = bool(cur["skipping"])
        start = offset - len(carry)  # file offset of carry[0]
        data = carry + chunk
        pos = 0
        # a truncated file reuses offsets, so its content epoch (gap_at) is part of the key
        identity = f"{cur['dev']}:{cur['inode']}:{cur['gap_at'] or 0}"
        while True:
            nl = data.find(b"\n", pos)
            if nl < 0:
                break
            line_start, raw = start + pos, data[pos:nl]
            pos = nl + 1
            if skipping:
                skipping = False
                continue
            events.extend(self._events(raw, line_start, identity, now))
        rest = data[pos:]
        if len(rest) > self.max_line and not skipping:
            # emit the head now and drop the remainder up to the next newline
            events.extend(self._events(rest, start + pos, identity, now))
            rest, skipping = b"", True
        elif skipping:
            rest = b""
        new_offset = offset + len(chunk)
        cur = {
            **cur,
            "offset": new_offset,
            "carry": rest,
            "skipping": int(skipping),
            "updated": now,
        }
        if draining and new_offset >= st.st_size:
            cur = self._flush(cur, now, events)
        inserted = self.store.commit(
            self.source,
            cur,
            events,
            sink=self.sink,
            gap=gap,
            store_events=self.store_events,
        )
        caught_up = not draining and new_offset >= st.st_size
        return PollResult(ok=True, caught_up=caught_up, events=inserted)

    def _events(
        self, raw: bytes, line_start: int, identity: str, now: float
    ) -> list[dict[str, Any]]:
        text = raw[: self.max_line].decode("utf-8", errors="replace").rstrip("\r")
        if len(raw) > self.max_line:
            text += _ELLIPSIS
        backfill = line_start < self._backfill_end
        if backfill and self.line_ts is not None and (stamp := self.line_ts(text)) is not None:
            self._last_ts = stamp
        ev = self.parse(text)
        if ev is None:
            return []
        ts = ev.get("ts")
        if backfill and self.max_age_s is not None:
            if ts is None and self.line_ts is not None:
                ts = self._last_ts
                if ts is None:
                    return []  # age unknown: never report an old line as current
            if ts is not None and ts < now - self.max_age_s:
                return []
        return [
            {
                **ev,
                "ts": ts if ts is not None else now,
                "observed": now,
                "dedup": f"{identity}:{line_start}",
            }
        ]


# --- parsers -------------------------------------------------------------------

_MLX_LEVEL_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ - (ERROR|CRITICAL|WARNING) - (.*)$"
)
_UVICORN_ERROR_RE = re.compile(r"^ERROR:\s+(.*)$")
_EXC_TAIL_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception)): (.*)$")
_ACCESS_RE = re.compile(r'^INFO:\s+([0-9a-fA-F.:]+):\d+ - "(\w+) (\S+) [^"]*" (5\d\d)\b')
_PROCESS_RE = re.compile(r"^INFO:\s+(Started|Finished) server process \[(\d+)\]")
_DL_DONE_RE = re.compile(r"^=====\s+(\S+ \S+)\s+done\s+fail=(\d+)\s+=====")


def _local_ts(raw: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> float | None:
    try:
        return datetime.strptime(raw, fmt).timestamp()  # the Mac writes local time
    except ValueError:
        return None


_LINE_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+ ")


def mlx_line_ts(line: str) -> float | None:
    m = _LINE_TS_RE.match(line)
    return _local_ts(m.group(1)) if m else None


def parse_mlx_line(line: str) -> ParsedEvent | None:
    if m := _PROCESS_RE.match(line):
        kind = "startup" if m.group(1) == "Started" else "shutdown"
        return {
            "kind": kind,
            "message": f"{m.group(1)} server process",
            "ts": None,
            "data": {"pid": int(m.group(2))},
        }
    if m := _MLX_LEVEL_RE.match(line):
        level, message = m.group(2), m.group(3)
        if level == "WARNING" and not message.startswith("Request failed"):
            return None
        return {"kind": "error", "message": message, "ts": _local_ts(m.group(1))}
    if m := _ACCESS_RE.match(line):
        return {
            "kind": "error",
            "message": f"HTTP {m.group(4)} {m.group(2)} {m.group(3)}",
            "ts": None,
            "data": {"client": m.group(1)},
        }
    if m := _UVICORN_ERROR_RE.match(line):
        return {"kind": "error", "message": m.group(1), "ts": None}
    if m := _EXC_TAIL_RE.match(line):
        return {"kind": "error", "message": f"{m.group(1)}: {m.group(2)}", "ts": None}
    return None


def parse_download_line(line: str) -> ParsedEvent | None:
    if m := _DL_DONE_RE.match(line):
        return {
            "kind": "download_done",
            "message": line.strip("= "),
            "ts": _local_ts(m.group(1)),
            "data": {"finished": m.group(1), "failed": int(m.group(2))},
        }
    return None


_REDACT = [
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "sk-<redacted>"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1<redacted>"),
    (re.compile(r"(?i)(api[_-]?key[\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+"), r"\1<redacted>"),
    (re.compile(r"(\"content\"\s*:\s*)\"(?:[^\"\\]|\\.)*\""), r'\1"<redacted>"'),
    (re.compile(r"('content'\s*:\s*)'(?:[^'\\]|\\.)*'"), r"\1'<redacted>'"),
]
_NORMALISE = [
    (re.compile(r"request=\S+"), "request=<id>"),
    (re.compile(r"/Users/[^/\s]+/"), "/Users/<user>/"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"(?<![\w.\-/])\d+(?:\.\d+)?(?:ms|s)?(?![\w.\-/])"), "<n>"),
]


def redact(text: str) -> str:
    for rx, sub in _REDACT:
        text = rx.sub(sub, text)
    return text[:500]


def normalise_error(message: str) -> str:
    """Stable grouping text: ids, numbers and home paths removed; model names and enum
    values kept; secrets and prompt bodies redacted."""
    text = redact(message)
    for rx, sub in _NORMALISE:
        text = rx.sub(sub, text)
    return text


def signature(message: str) -> str:
    return hashlib.sha1(normalise_error(message).encode()).hexdigest()[:12]


_REASONING_RE = re.compile(r"Unexpected reasoning effort")
_LOAD_RE = re.compile(r"Error loading model (\S+)")


def error_hint(message: str) -> str | None:
    if _REASONING_RE.search(message):
        return (
            "A client sends a reasoning_effort value this model's chat template rejects;"
            " drop the parameter or use a value the template accepts."
        )
    if m := _LOAD_RE.search(message):
        return (
            f"A client used model id `{m.group(1)}`; use /Volumes/Models/models/active"
            " (LiteLLM: openai//Volumes/Models/models/active)."
        )
    return None


def with_signature(parse: Parser) -> Parser:
    """Adds ``signature`` and redacts the message of error events."""

    def wrapped(line: str) -> ParsedEvent | None:
        ev = parse(line)
        if ev is not None and ev["kind"] == "error":
            ev = {**ev, "signature": signature(ev["message"]), "message": redact(ev["message"])}
        return ev

    return wrapped


def archive_paths(log_dir: Path, name: str, limit: int = 16) -> list[Path]:
    """Rotated copies ``<name>.YYYYmmdd-HHMMSS`` (regular files only), newest first."""
    try:
        found = [
            p
            for p in log_dir.iterdir()
            if p.name.startswith(name + ".")
            and _ARCHIVE_SUFFIX.fullmatch(p.name[len(name) :])
            and p.is_file()
            and not p.is_symlink()
        ]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name, reverse=True)[:limit]


class HostLogs:
    """Durable ingestion of ``mlx-server.log`` and every ``download-*.log``."""

    MLX_SOURCE = "mlx-server"

    def __init__(
        self,
        settings: Settings,
        store: IngestStore,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.store = store
        self.now = now
        self._ingestors: dict[str, Ingestor] = {}

    def _ingestor(
        self,
        source: str,
        path: Path,
        parse: Parser,
        line_ts: Callable[[str], float | None] | None = None,
        archives: Callable[[], list[Path]] | None = None,
    ) -> Ingestor:
        if source not in self._ingestors:
            # first sight backfills at most the alert window (errors resolve after 60 min)
            self._ingestors[source] = Ingestor(
                self.store,
                source,
                path,
                parse,
                max_age_s=3600,
                line_ts=line_ts,
                now=self.now,
                archives=archives,
            )
        return self._ingestors[source]

    def poll_all(self) -> dict[str, PollResult]:
        out = {
            self.MLX_SOURCE: self._ingestor(
                self.MLX_SOURCE,
                self.settings.mlx_log_dir / "mlx-server.log",
                with_signature(parse_mlx_line),
                line_ts=mlx_line_ts,
                archives=lambda: archive_paths(self.settings.mlx_log_dir, "mlx-server.log"),
            ).poll()
        }
        for sid in download_log_ids(self.settings):
            out[sid] = self._ingestor(
                sid, self.settings.mlx_log_dir / f"{sid}.log", parse_download_line
            ).poll()
        return out
