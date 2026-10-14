"""Daily log rotation through the owning CLI (M4 §3).

The admin never renames host files itself: it runs ``mlx-model rotate-logs`` as a
``maintenance`` job, which holds the runner slot (no lifecycle job can race it) and
takes ``mlx.lock``; the CLI rechecks every writer under that lock and defers a log whose
writer runs. Deferral is visible as a warning condition until a later run clears it.
A deferred log is retried at the next safe boundary: once MLX is not running (and at
most hourly), or the next day. ``mlx-model start`` also rotates its own log while the
server is stopped. Admin logs keep their own ``RotatingFileHandler`` policy.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .alerts import AlertStore, Snapshot
from .config import Settings
from .control import ControlError, ControlService
from .jobs import ops_tz

log = logging.getLogger("local_llm_admin")

ROTATION_KEY = "log_rotation"
ALERT_DEFERRED = "log_rotation_deferred"
ALERT_FAILED = "log_rotation_failed"
RETRY_S = 3600.0
MIB = 1024 * 1024
_NAME = r"(?:mlx-server|chat-qwen|serve-qwen|download-[A-Za-z0-9_-]+)\.log"
_LINE = {
    "rotated": re.compile(rf"^rotated ({_NAME}) ({_NAME}\.\d{{8}}-\d{{6}}) (\d+)$"),
    "deferred": re.compile(rf"^deferred ({_NAME}) (active-writer|writer-unknown) (\d+)$"),
    "skipped": re.compile(rf"^skipped ({_NAME}) below-threshold \d+$"),
    "missing": re.compile(rf"^missing ({_NAME})$"),
    "pruned": re.compile(rf"^pruned ({_NAME}\.\d{{8}}-\d{{6}})$"),
    "errors": re.compile(rf"^error ({_NAME}) ([a-z-]+(?: [\w.-]+)?)$"),
}


def build_rotate_argv(bin_path: Path, max_mib: int, keep: int) -> list[str]:
    """Fixed argv; never passed through a shell."""
    if not 1 <= max_mib <= 10240 or not 1 <= keep <= 50:
        raise ValueError("rotation limits out of range")
    return [str(bin_path), "rotate-logs", "--max-bytes", str(max_mib * MIB), "--keep", str(keep)]


def parse_rotate_output(text: str) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {k: [] for k in _LINE}
    for line in text.splitlines():
        line = line.strip()
        for kind, rx in _LINE.items():
            if not (m := rx.match(line)):
                continue
            if kind == "rotated":
                out[kind].append(
                    {"name": m.group(1), "archive": m.group(2), "bytes": int(m.group(3))}
                )
            elif kind == "deferred":
                out[kind].append(
                    {"name": m.group(1), "reason": m.group(2), "bytes": int(m.group(3))}
                )
            elif kind == "errors":
                out[kind].append({"name": m.group(1), "reason": m.group(2)})
            else:
                out[kind].append(m.group(1))
            break
    return out


class LogMaintenance:
    """Supervisor tick listener plus a job hook recording each run's outcome."""

    def __init__(
        self,
        settings: Settings,
        control: ControlService,
        alerts: AlertStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.control = control
        self.alerts = alerts
        self.clock = clock
        control.runner.add_hook(self.on_job_complete)

    def _get(self) -> dict[str, Any]:
        value: dict[str, Any] | None = self.control.store.get_kv(ROTATION_KEY)
        return value or {}

    def state(self) -> dict[str, Any]:
        return {
            "max_mib": self.settings.log_rotate_max_mib,
            "keep": self.settings.log_rotate_keep,
            "hour": self.settings.log_rotate_hour,
            "last": self.control.store.get_kv(ROTATION_KEY),
        }

    def _due(self, snap: Snapshot, now: float, st: dict[str, Any]) -> str | None:
        if snap.drive_mounted is not True or snap.job_running:
            return None
        if st.get("outcome") == "running":
            job = self.control.runner.get(st.get("job_id") or "")
            if job is not None and job["status"] == "running":
                return None
        local = datetime.fromtimestamp(now, ops_tz(self.settings.litellm_timezone_offset_min))
        if st.get("day") != local.date().isoformat():
            return "daily" if local.hour >= self.settings.log_rotate_hour else None
        deferred = (st.get("result") or {}).get("deferred")
        # a stopped MLX (or ComfyUI owning the GPU) means the MLX logs' writer is gone
        if deferred and snap.engine in ("none", "comfyui"):
            if now - float(st.get("attempted_at") or 0) >= RETRY_S:
                return "retry"
        return None

    async def on_tick(self, snap: Snapshot, observed: dict[str, Any] | None) -> None:
        now = self.clock()
        st = self._get()
        reason = self._due(snap, now, st)
        if reason is None:
            return
        argv = build_rotate_argv(
            self.settings.mlx_model_path,
            self.settings.log_rotate_max_mib,
            self.settings.log_rotate_keep,
        )
        try:
            job = await self.control.rotate_logs(argv)
        except ControlError as exc:
            # busy or locked: try again on a later tick; the day is not used up
            self.control.store.set_kv(
                ROTATION_KEY,
                {**st, "day": st.get("day"), "skipped": exc.message, "skipped_at": now},
            )
            return
        local = datetime.fromtimestamp(now, ops_tz(self.settings.litellm_timezone_offset_min))
        self.control.store.set_kv(
            ROTATION_KEY,
            {
                **st,
                "day": local.date().isoformat(),
                "attempted_at": now,
                "reason": reason,
                "job_id": job["id"],
                "outcome": "running",
                "skipped": None,
            },
        )

    def on_job_complete(self, job: dict[str, Any]) -> None:
        if job["action"] != "rotate-logs":
            return
        now = self.clock()
        view = self.control.runner.get(job["id"]) or {}
        result = parse_rotate_output(view.get("output", ""))
        status = job["status"]
        st = self._get()
        self.control.store.set_kv(
            ROTATION_KEY,
            {**st, "job_id": job["id"], "outcome": status, "finished": now, "result": result},
        )
        if result["deferred"]:
            detail = "; ".join(
                f"{d['name']} ({d['bytes'] / MIB:.1f} MiB): "
                + ("its writer is running" if d["reason"] == "active-writer" else "writer unknown")
                for d in result["deferred"]
            )
            self.alerts.raise_condition(
                ALERT_DEFERRED,
                "warning",
                "Log rotation deferred",
                detail + ". Rotation retries after the writer stops (for example at the next"
                " model stop or switch); inference is never interrupted for it.",
                now,
            )
        else:
            self.alerts.resolve_condition(ALERT_DEFERRED, now)
        if status != "succeeded" or result["errors"]:
            detail = "; ".join(f"{e['name']}: {e['reason']}" for e in result["errors"])
            self.alerts.raise_condition(
                ALERT_FAILED,
                "warning",
                "Log rotation failed",
                (detail or f"mlx-model rotate-logs ended {status}")
                + f" (job {job['id']}). The original logs were kept.",
                now,
            )
        else:
            self.alerts.resolve_condition(ALERT_FAILED, now)
        log.info("log rotation job %s: %s %s", job["id"], status, result)
