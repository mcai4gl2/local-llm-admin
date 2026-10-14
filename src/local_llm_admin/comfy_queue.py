"""ComfyUI queue inspection and mutation (M5), tied to the installed API.

Verified against ComfyUI 0.37.0 (commit a716932, ``server.py``/``comfy_execution/jobs.py``):

* ``GET /queue`` -> ``{"queue_running": [...], "queue_pending": [...]}``; each item is
  ``[number, prompt_id, prompt, extra_data, outputs_to_execute]`` (sensitive data removed).
  ``extra_data.create_time`` is epoch milliseconds.
* ``POST /queue {"delete": [ids]}`` removes *pending* items only; unknown ids are no-ops.
* ``POST /api/jobs/{id}/cancel`` interrupts that job only if it is still the one running,
  atomically under the queue mutex (``interrupt_if_running``); returns ``{"cancelled": bool}``.
* ``POST /interrupt {"prompt_id": id}`` checks then interrupts (not atomic);
  ``POST /interrupt {}`` interrupts whatever is running (global).
* Prompt ids are canonical lowercase UUIDs (``validate_job_id``).

Prompts and workflows never leave this module: records carry ids, status, times and
node counts only. A queue that cannot be read is unknown, never empty.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .control import ControlService, process_generation
from .engines import ComfyClient
from .jobs import JobConflict, JobStore

log = logging.getLogger("local_llm_admin")

MAX_LISTED = 200  # records returned per state; counts stay exact
MAX_IDS = 50  # ids accepted per delete request
HISTORY_ACTION = "comfyui-queue"


class QueueError(Exception):
    """A refused or failed queue request; ``code`` is the HTTP status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def valid_job_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def validate_ids(raw: Any) -> list[str]:
    """A bounded, de-duplicated list of canonical prompt ids, or ``QueueError(400)``."""
    if not isinstance(raw, list) or not raw:
        raise QueueError(400, "ids must be a non-empty list")
    if len(raw) > MAX_IDS:
        raise QueueError(400, f"at most {MAX_IDS} ids per request")
    if not all(valid_job_id(v) for v in raw):
        raise QueueError(400, "ids must be ComfyUI prompt ids (lowercase UUIDs)")
    return list(dict.fromkeys(raw))


def normalize_item(raw: Any, status: str) -> dict[str, Any] | None:
    """One queue tuple -> bounded metadata; None if it has no usable prompt id."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2 or not valid_job_id(raw[1]):
        return None
    number = raw[0] if isinstance(raw[0], (int, float)) and not isinstance(raw[0], bool) else None
    prompt = raw[2] if len(raw) > 2 and isinstance(raw[2], dict) else None
    extra = raw[3] if len(raw) > 3 and isinstance(raw[3], dict) else {}
    outputs = raw[4] if len(raw) > 4 and isinstance(raw[4], list) else None
    created = extra.get("create_time")
    created_s = (
        created / 1000.0
        if isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0
        else None
    )
    return {
        "id": raw[1],
        "status": status,
        "number": number,
        "created": created_s,
        "nodes": len(prompt) if prompt is not None else None,
        "output_nodes": len(outputs) if outputs is not None else None,
    }


def parse_queue(data: Any) -> dict[str, Any] | None:
    """``GET /queue`` -> running/pending records (pending in execution order).

    Returns None when the shape is not the verified one: an unreadable queue is unknown.
    Items without a valid prompt id are counted in ``unparsed``, never dropped silently.
    """
    if not isinstance(data, dict):
        return None
    running_raw, pending_raw = data.get("queue_running"), data.get("queue_pending")
    if not isinstance(running_raw, list) or not isinstance(pending_raw, list):
        return None
    unparsed = 0
    out: dict[str, list[dict[str, Any]]] = {"running": [], "pending": []}
    for key, items in (("running", running_raw), ("pending", pending_raw)):
        for raw in items:
            rec = normalize_item(raw, key)
            if rec is None:
                unparsed += 1
            else:
                out[key].append(rec)
    out["pending"].sort(key=lambda r: (r["number"] is None, r["number"] or 0))
    return {
        "running": out["running"],
        "pending": out["pending"],
        "running_count": len(running_raw),
        "pending_count": len(pending_raw),
        "unparsed": unparsed,
    }


def session_token(pid: int | None) -> str | None:
    """Identity of one ComfyUI process generation (PID plus start time)."""
    gen = process_generation(pid)
    if gen is None:
        return None
    return f"{gen['pid']}-{int(gen['started'] * 1000)}"


def public_view(snap: dict[str, Any]) -> dict[str, Any]:
    """Snapshot for the browser: bounded record lists, exact counts."""
    view = {k: v for k, v in snap.items() if k not in ("running", "pending")}
    if "running" in snap:
        view["running"] = snap["running"][:MAX_LISTED]
        view["pending"] = snap["pending"][:MAX_LISTED]
        view["truncated"] = len(snap["running"]) > MAX_LISTED or len(snap["pending"]) > MAX_LISTED
    return view


class ComfyQueue:
    """Queue reads and mutations. Every mutation holds the control slot (the one engine
    transitions use) and re-reads engine identity and the queue before acting."""

    def __init__(self, comfy: ComfyClient, control: ControlService, store: JobStore) -> None:
        self.comfy = comfy
        self.control = control
        self.store = store

    async def snapshot(self) -> dict[str, Any]:
        proc = await asyncio.to_thread(self.comfy.running)
        base: dict[str, Any] = {"checked_at": time.time()}
        if not proc.running:
            return base | {"state": "stopped", "session": None, "reason": "ComfyUI is not running"}
        session = await asyncio.to_thread(session_token, proc.pid)
        parsed = parse_queue(await self.comfy.queue_raw())
        if parsed is None:
            return base | {
                "state": "unknown",
                "session": session,
                "reason": "ComfyUI is running but its queue could not be read",
            }
        caps = {"per_job_cancel": await self.comfy.per_job_cancel()}
        return base | {"state": "ok", "session": session, "capabilities": caps, **parsed}

    # --- mutations -----------------------------------------------------------
    async def _mutate(
        self,
        session: str | None,
        what: str,
        act: Callable[[dict[str, Any]], Awaitable[tuple[dict[str, Any], str]]],
    ) -> dict[str, Any]:
        try:
            claim = self.control.runner.claim()
        except JobConflict:
            raise QueueError(409, self.control.busy_message()) from None
        t0 = time.monotonic()
        try:
            snap = await self.snapshot()
            if snap["state"] == "stopped":
                raise QueueError(409, "ComfyUI is not running; nothing was changed")
            if snap["session"] is None or snap["session"] != session:
                raise QueueError(
                    409,
                    "ComfyUI restarted or the engine changed since this view was loaded;"
                    " refresh and try again",
                )
            if snap["state"] != "ok":
                raise QueueError(503, "the ComfyUI queue could not be read; nothing was changed")
            try:
                result, summary = await act(snap)
            except httpx.HTTPError as exc:
                self._record(t0, "unknown", f"{what}: ComfyUI error ({type(exc).__name__})")
                raise QueueError(
                    502,
                    "ComfyUI did not complete the request; the queue may or may not have"
                    " changed. Refresh to see its current state.",
                ) from None
            self._record(t0, "succeeded", f"{what}: {summary}")
            return result
        finally:
            self.control.runner.release(claim)

    def _record(self, t0: float, result: str, text: str) -> None:
        """A concise history row: ids and counts only, never prompt bodies."""
        log.info("comfyui queue %s (%s)", text, result)
        try:
            self.store.add_history(
                ts=time.time(),
                action=HISTORY_ACTION,
                from_name=None,
                to_name=None,
                result=result,
                duration_s=round(time.monotonic() - t0, 3),
                output_tail=text[:2000],
                source="admin",
            )
        except Exception:
            log.exception("could not record a ComfyUI queue action")

    async def _delete(self, snap: dict[str, Any], ids: list[str]) -> dict[str, str]:
        pending = {r["id"] for r in snap["pending"]}
        running = {r["id"] for r in snap["running"]}
        targets = [i for i in ids if i in pending]
        results = {i: "running" if i in running else "not_pending" for i in ids if i not in pending}
        if targets:
            await self.comfy.post("/queue", {"delete": targets})
            after = parse_queue(await self.comfy.queue_raw())
            if after is None:
                results |= {i: "unconfirmed" for i in targets}
            else:
                now_pending = {r["id"] for r in after["pending"]}
                now_running = {r["id"] for r in after["running"]}
                for i in targets:
                    # a pending item can start between the re-read and the delete;
                    # /queue delete never interrupts it
                    results[i] = (
                        "still_pending"
                        if i in now_pending
                        else "started"
                        if i in now_running
                        else "removed"
                    )
        return results

    @staticmethod
    def _tally(results: dict[str, str]) -> str:
        counts: dict[str, int] = {}
        for v in results.values():
            counts[v] = counts.get(v, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do"

    async def delete(self, ids_raw: Any, session: str | None) -> dict[str, Any]:
        """Remove selected pending items. Ids that are no longer pending are reported,
        never cancelled by some other means."""
        ids = validate_ids(ids_raw)

        async def act(snap: dict[str, Any]) -> tuple[dict[str, Any], str]:
            results = await self._delete(snap, ids)
            return {"results": results}, f"delete {len(ids)} pending ({self._tally(results)})"

        return await self._mutate(session, "delete", act)

    async def clear(self, session: str | None) -> dict[str, Any]:
        """Remove every item pending at the re-read. Work queued after that by another
        ComfyUI client is left alone (unlike upstream ``{"clear": true}``)."""

        async def act(snap: dict[str, Any]) -> tuple[dict[str, Any], str]:
            ids = [r["id"] for r in snap["pending"]]
            results = await self._delete(snap, ids)
            note = " (items without a prompt id were left)" if snap["unparsed"] else ""
            return (
                {"results": results, "unparsed": snap["unparsed"]},
                f"clear {len(ids)} pending ({self._tally(results)}){note}",
            )

        return await self._mutate(session, "clear", act)

    async def cancel_running(self, job_id: Any, session: str | None) -> dict[str, Any]:
        """Stop one running generation by id. Uses the atomic per-job cancel when the
        installed API has it; otherwise ``/interrupt {prompt_id}``, which checks then
        interrupts and can race a generation that starts in between."""
        if not valid_job_id(job_id):
            raise QueueError(400, "id must be a ComfyUI prompt id (lowercase UUID)")

        async def act(snap: dict[str, Any]) -> tuple[dict[str, Any], str]:
            if job_id not in {r["id"] for r in snap["running"]}:
                return {"result": "not_running"}, f"cancel {job_id}: not running"
            if snap["capabilities"]["per_job_cancel"]:
                resp = await self.comfy.post(f"/api/jobs/{job_id}/cancel", {})
                try:
                    cancelled = bool(resp.json().get("cancelled"))
                except (ValueError, AttributeError):
                    cancelled = False
                result = "cancelled" if cancelled else "not_running"
                return {"result": result, "atomic": True}, f"cancel {job_id}: {result}"
            await self.comfy.post("/interrupt", {"prompt_id": job_id})
            return (
                {"result": "interrupt_requested", "atomic": False},
                f"interrupt {job_id} (non-atomic)",
            )

        return await self._mutate(session, "cancel", act)

    async def interrupt(self, expected_running: Any, session: str | None) -> dict[str, Any]:
        """Global ``/interrupt``: stops whatever is running. Refused if the running set
        differs from what the browser showed. Another ComfyUI client can still start a
        generation between this check and the interrupt; upstream has no conditional
        global interrupt."""
        if not isinstance(expected_running, list) or not all(
            valid_job_id(v) for v in expected_running
        ):
            raise QueueError(400, "expected_running must be a list of prompt ids")
        if len(expected_running) > MAX_IDS:
            raise QueueError(400, f"at most {MAX_IDS} ids")

        async def act(snap: dict[str, Any]) -> tuple[dict[str, Any], str]:
            running = {r["id"] for r in snap["running"]}
            if running != set(expected_running) or snap["running_count"] != len(running):
                raise QueueError(409, "the running generation changed; refresh and try again")
            if not running:
                return {"result": "nothing_running"}, "interrupt: nothing running"
            await self.comfy.post("/interrupt", {})
            ids = ", ".join(sorted(running))
            return {"result": "interrupted", "global": True}, f"global interrupt ({ids})"

        return await self._mutate(session, "interrupt", act)
