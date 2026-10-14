"""Per-request performance from ``mlx-server.log`` (M4 §1): line parser and correlator.

Line shapes are the ones mlx-vlm 0.7.3 writes (recorded in ``tests/fixtures/mac``).
``Generation queued``, ``Prefill completed``, ``Decode started`` and ``Decode completed``
carry ``request=<id>``; ``Request started``, ``Request completed`` and ``Request failed``
do not. Ids look like memory addresses and are reused, so a row's key is the byte
position of its ``Generation queued`` line, not the id.

An id-less line is attached to a request only when exactly one candidate fits;
otherwise it is recorded as ambiguous or dropped. A client disconnect logs the id-less
``Request failed … stream_closed…`` first and ``Generation cancelled: request=<id>`` just
after; with several requests open the failure waits for that line instead of guessing.
Requests rejected before queueing (``Unexpected error in <endpoint> endpoint``, ``Error
loading model <client id>``) have no id or start line: each becomes one standalone error. Missing measurements stay null.
Only metadata is kept: no request bodies appear in these lines, and error text is
reduced to a short token.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .ingestion import ParsedEvent

STALE_S = 3600.0  # an open request with no line for this long is incomplete
MAX_OPEN = 64
MAX_PENDING = 16

_PREFIX_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{1,6}) - (?:INFO|WARNING|ERROR) - ([A-Z][a-z ]+): (.*)$"
)
_MARKER_RE = re.compile(r"^=====\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+start\s+(\S+)\s+=====")
_PROCESS_RE = re.compile(r"^INFO:\s+Started server process \[(\d+)\]")
_TS = r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{1,6}) - ERROR - "
_REJECTED = (
    (re.compile(_TS + r"Unexpected error in \S+ endpoint: "), "endpoint_error"),
    # a client-requested id; the server's own weights (a path) and the second
    # "in generation thread" line of the same failure are not requests
    (
        re.compile(_TS + r"Error loading model (?!in generation thread)[^\s/][^\s:]*: "),
        "model_load_failed",
    ),
)
_KV_RE = re.compile(r"(\w+)=(\S+)")
_RID_RE = re.compile(r"^[0-9A-Za-z]{1,32}$")
_ERROR_RE = re.compile(r"^[\w.:-]{1,80}$")
_CANCEL_WORDS = ("stream_closed", "cancel", "disconnect")
_METRICS = ("prompt_tokens", "prefill_tok_s", "ttft_s", "generated_tokens", "decode_tok_s")


def _ts(raw: str, frac: str) -> float | None:
    try:
        base = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").timestamp()  # Mac local time
    except ValueError:
        return None
    return base + int(frac) / float(10 ** len(frac))


def _int(v: str | None) -> int | None:
    return int(v) if v is not None and v.isdigit() else None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v.removesuffix("s"))
    except ValueError:
        return None


def _bool(v: str | None) -> bool | None:
    return {"True": True, "False": False}.get(v or "")


def parse_perf_line(line: str) -> ParsedEvent | None:
    if m := _MARKER_RE.match(line):
        return {
            "kind": "generation",
            "message": "start marker",
            "ts": _ts(m.group(1), "0"),
            "data": {"name": m.group(2)},
        }
    if m := _PROCESS_RE.match(line):
        return {
            "kind": "server_start",
            "message": "server process started",
            "ts": None,
            "data": {"pid": int(m.group(1))},
        }
    for rx, error in _REJECTED:
        if m := rx.match(line):
            return {
                "kind": "req_rejected",
                "message": "request rejected",
                "ts": _ts(m.group(1), m.group(2)),
                "data": {"error": error},
            }
    m = _PREFIX_RE.match(line)
    if not m:
        return None
    ts, what, kv = _ts(m.group(1), m.group(2)), m.group(3), dict(_KV_RE.findall(m.group(4)))
    rid = kv.get("request")
    data: dict[str, Any]
    if what == "Request started":
        kind, data = "req_start", {"stream": _bool(kv.get("stream"))}
    elif what == "Request completed":
        kind, data = (
            "req_done",
            {
                "stream": _bool(kv.get("stream")),
                "prompt_tokens": _int(kv.get("prompt_tokens")),
                "generated_tokens": _int(kv.get("generated_tokens")),
                "elapsed_s": _num(kv.get("elapsed")),
            },
        )
    elif what == "Request failed":
        err = kv.get("error", "")
        kind, data = (
            "req_failed",
            {"stream": _bool(kv.get("stream")), "error": err if _ERROR_RE.match(err) else "error"},
        )
    elif rid is None or not _RID_RE.match(rid):
        return None
    elif what == "Generation queued":
        prompt = _int(kv.get("prompt_tokens"))
        if prompt is None:
            return None
        kind, data = (
            "req_queued",
            {"rid": rid, "prompt_tokens": prompt, "images": _int(kv.get("images"))},
        )
    elif what == "Prefill completed":
        kind, data = (
            "req_prefill",
            {
                "rid": rid,
                "prompt_tokens": _int(kv.get("prompt_tokens")),
                "cached_tokens": _int(kv.get("cached_tokens")),
                "prefill_s": _num(kv.get("elapsed")),
                "prefill_tok_s": _num(kv.get("rate")),
            },
        )
    elif what == "Generation cancelled":
        kind, data = (
            "req_cancel",
            {"rid": rid, "generated_tokens": _int(kv.get("generated_tokens"))},
        )
    elif what == "Decode started":
        kind, data = "req_ttft", {"rid": rid, "ttft_s": _num(kv.get("time_to_first_token"))}
    elif what == "Decode completed":
        kind, data = (
            "req_decode",
            {
                "rid": rid,
                "generated_tokens": _int(kv.get("generated_tokens")),
                "decode_s": _num(kv.get("elapsed")),
                "decode_tok_s": _num(kv.get("rate")),
                "finish_reason": kv.get("finish_reason"),
            },
        )
    else:
        return None
    return {"kind": kind, "message": what, "ts": ts, "data": data}


@dataclass
class PerfState:
    """Correlation state persisted with the ingestion cursor (one JSON value)."""

    generation: dict[str, Any] | None = None  # {name, ts, fresh}
    open: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_starts: list[dict[str, Any]] = field(default_factory=list)
    awaiting: list[dict[str, Any]] = field(default_factory=list)  # decoded, no envelope yet
    pending_cancels: list[dict[str, Any]] = field(default_factory=list)  # failures awaiting an id
    recent_cancelled: list[dict[str, Any]] = field(default_factory=list)  # closed by a failure

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | None) -> PerfState:
        try:
            d = json.loads(raw) if raw else {}
            return cls(**d) if isinstance(d, dict) else cls()
        except (ValueError, TypeError):
            return cls()


@dataclass
class Correlated:
    rows: list[dict[str, Any]] = field(default_factory=list)  # finished requests
    updates: list[dict[str, Any]] = field(default_factory=list)  # {key, fields}


def _span(dedup: str) -> tuple[str, int]:
    identity, _, offset = dedup.rpartition(":")
    return identity, int(offset)


class Correlator:
    def __init__(self, state: PerfState) -> None:
        self.state = state
        self._out = Correlated()

    def apply(self, events: list[dict[str, Any]], gap: bool = False) -> Correlated:
        self._out = Correlated()
        if gap:  # unread lines may be lost: nothing open can be finished honestly
            self._close_all()
            self.state.generation = None
            self._forget()
        for ev in events:
            handler = getattr(self, f"_on_{ev['kind']}")  # the parser emits only these kinds
            if ev["kind"] != "server_start":  # its ts is the observation time, not the line's
                self._expire(ev["ts"])
            handler(ev, ev["data"])
        return self._out

    # --- helpers -------------------------------------------------------------------
    def _new_row(self, ev: dict[str, Any], rid: str | None) -> dict[str, Any]:
        gen = self.state.generation
        identity, offset = _span(ev["dedup"])
        return {
            "key": ev["dedup"],
            "request_id": rid,
            "started": ev["ts"],
            "finished": None,
            "model": gen["name"] if gen else None,
            "attribution": "generation" if gen else "unknown",
            "stream": None,
            "images": None,
            "prompt_tokens": None,
            "cached_tokens": None,
            "generated_tokens": None,
            "prefill_s": None,
            "prefill_tok_s": None,
            "ttft_s": None,
            "decode_s": None,
            "decode_tok_s": None,
            "elapsed_s": None,
            "finish_reason": None,
            "state": "open",
            "error": None,
            "complete": 0,
            "log_identity": identity,
            "log_start": offset,
            "log_end": offset,
            "last": ev["ts"],
        }

    def _finish(self, row: dict[str, Any], state: str, ts: float) -> None:
        row = dict(row)
        row.pop("last", None)
        row["state"] = state
        row["finished"] = ts
        row["complete"] = int(state == "completed" and all(row[k] is not None for k in _METRICS))
        self._out.rows.append(row)

    def _close(self, rid: str) -> None:
        row = self.state.open.pop(rid)
        self._finish(row, "incomplete", row["last"])

    def _close_all(self) -> None:
        for rid in list(self.state.open):
            self._close(rid)

    def _forget(self) -> None:
        for lst in (
            self.state.pending_starts,
            self.state.awaiting,
            self.state.pending_cancels,
            self.state.recent_cancelled,
        ):
            lst.clear()

    def _expire(self, ts: float) -> None:
        for rid, row in list(self.state.open.items()):
            if ts - row["last"] > STALE_S:
                self._close(rid)
        self.state.pending_starts = [
            s for s in self.state.pending_starts if ts - s["ts"] <= STALE_S
        ]
        self.state.awaiting = [a for a in self.state.awaiting if ts - a["ts"] <= STALE_S]
        self.state.pending_cancels = [
            c for c in self.state.pending_cancels if ts - c["ts"] <= STALE_S
        ]
        self.state.recent_cancelled = [
            c for c in self.state.recent_cancelled if ts - c["ts"] <= STALE_S
        ]

    def _touch(self, ev: dict[str, Any]) -> dict[str, Any] | None:
        row = self.state.open.get(ev["data"]["rid"])
        if row is not None:
            row["log_end"] = _span(ev["dedup"])[1]
            row["last"] = ev["ts"]
        return row

    # --- handlers ------------------------------------------------------------------
    def _on_generation(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        self._close_all()
        self._forget()
        self.state.generation = {"name": d["name"], "ts": ev["ts"], "fresh": True}

    def _on_server_start(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        self._close_all()
        self._forget()
        gen = self.state.generation
        if gen and gen["fresh"]:
            gen["fresh"] = False  # the process the marker announced
        else:
            self.state.generation = None  # a process start with no marker: weights unknown

    def _on_req_start(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        self.state.pending_starts.append({"ts": ev["ts"], "stream": d["stream"]})
        del self.state.pending_starts[:-MAX_PENDING]

    def _on_req_queued(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        rid = d["rid"]
        if rid in self.state.open:
            self._close(rid)  # the id was reused: the older request never finished
        if self.state.generation:
            self.state.generation["fresh"] = False
        row = self._new_row(ev, rid)
        row.update(prompt_tokens=d["prompt_tokens"], images=d["images"])
        pending = self.state.pending_starts
        if len(pending) == 1:
            start = pending.pop()
            row["started"] = start["ts"]
            row["stream"] = None if start["stream"] is None else int(start["stream"])
        else:
            pending.clear()  # several unqueued starts: which one this is is unknown
        while len(self.state.open) >= MAX_OPEN:
            self._close(next(iter(self.state.open)))
        self.state.open[rid] = row

    def _on_req_prefill(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        if (row := self._touch(ev)) is None:
            return
        for k in ("prompt_tokens", "cached_tokens", "prefill_s", "prefill_tok_s"):
            if d[k] is not None:
                row[k] = d[k]

    def _on_req_ttft(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        if (row := self._touch(ev)) is not None:
            row["ttft_s"] = d["ttft_s"]

    def _on_req_decode(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        if (row := self._touch(ev)) is None:
            return
        for k in ("generated_tokens", "decode_s", "decode_tok_s", "finish_reason"):
            row[k] = d[k]
        del self.state.open[d["rid"]]
        self._finish(row, "completed", ev["ts"])
        self.state.awaiting.append(
            {
                "key": row["key"],
                "prompt_tokens": row["prompt_tokens"],
                "generated_tokens": row["generated_tokens"],
                "ts": ev["ts"],
            }
        )
        del self.state.awaiting[:-MAX_PENDING]

    def _on_req_done(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        if d["prompt_tokens"] is None or d["generated_tokens"] is None:
            return
        match = [
            a
            for a in self.state.awaiting
            if a["prompt_tokens"] == d["prompt_tokens"]
            and a["generated_tokens"] == d["generated_tokens"]
        ]
        if len(match) != 1:
            return
        self.state.awaiting.remove(match[0])
        fields: dict[str, Any] = {"elapsed_s": d["elapsed_s"]}
        if d["stream"] is not None:
            fields["stream"] = int(d["stream"])
        self._out.updates.append({"key": match[0]["key"], "fields": fields})

    def _on_req_failed(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        err = d["error"]
        state = "cancelled" if any(w in err for w in _CANCEL_WORDS) else "error"
        opened, pending = self.state.open, self.state.pending_starts
        if len(opened) == 1 and not pending:
            rid = next(iter(opened))
            row = opened.pop(rid)
            row.update(error=err, log_end=_span(ev["dedup"])[1])
            self._finish(row, state, ev["ts"])
            if state == "cancelled":  # "Generation cancelled" may still add its count
                self.state.recent_cancelled.append({"rid": rid, "key": row["key"], "ts": ev["ts"]})
                del self.state.recent_cancelled[:-MAX_PENDING]
            return
        if state == "cancelled" and opened:
            # which open request the client left is named by the next "Generation cancelled"
            self.state.pending_cancels.append({"ts": ev["ts"], "error": err})
            del self.state.pending_cancels[:-MAX_PENDING]
            return
        row = self._new_row(ev, None)
        row["error"] = err
        if not opened and len(pending) <= 1:
            start = pending.pop() if pending else None
            if start is not None:
                row["started"] = start["ts"]
            stream = d["stream"] if d["stream"] is not None else (start or {}).get("stream")
            row["stream"] = None if stream is None else int(stream)
        else:
            row.update(model=None, attribution="ambiguous")
            if d["stream"] is not None:
                row["stream"] = int(d["stream"])
        self._finish(row, state, ev["ts"])

    def _on_req_cancel(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        rid, tokens = d["rid"], d["generated_tokens"]
        row = self._touch(ev)
        if row is None:
            recent = [c for c in self.state.recent_cancelled if c["rid"] == rid]
            if recent and tokens is not None:
                self.state.recent_cancelled.remove(recent[0])
                self._out.updates.append(
                    {"key": recent[0]["key"], "fields": {"generated_tokens": tokens}}
                )
            return
        del self.state.open[rid]
        failure = self.state.pending_cancels.pop(0) if self.state.pending_cancels else None
        row.update(
            generated_tokens=tokens,
            error=failure["error"] if failure else "generation_cancelled",
        )
        self._finish(row, "cancelled", ev["ts"])

    def _on_req_rejected(self, ev: dict[str, Any], d: dict[str, Any]) -> None:
        """Rejected before queueing: never an open request, which is someone else's."""
        row = self._new_row(ev, None)
        row["error"] = d["error"]
        if len(self.state.pending_starts) == 1:
            start = self.state.pending_starts.pop()
            row["started"] = start["ts"]
            row["stream"] = None if start["stream"] is None else int(start["stream"])
        self._finish(row, "error", ev["ts"])
