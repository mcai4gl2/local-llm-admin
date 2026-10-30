"""Single-flight runner for mlx-model lifecycle actions, plus SQLite job/switch history."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .mlx import build_command
from .storage import Database

log = logging.getLogger(__name__)

_TERM_GRACE_S = 5.0
LIFECYCLE_ACTIONS = (
    "switch",
    "start",
    "stop",
    "add",
    "engine",
    "unlock",
    "quality",
    "rotate-logs",
    "remove",
    "profile",
    "decision",
    "sidecar",
    "service",
)
# driven step by step by their owner (begin/run_command/finish), not by a thread here
HELD_ACTIONS = ("quality",)
_ACTIVATING = ("switch", "start", "engine")
_ACTIVE_RESULTS = ("succeeded", "observed")
_OUTPUT_TAIL_BYTES = 64 * 1024

WAIT_POLL_S = 0.5
JOB_SOURCES = ("admin", "watchdog", "benchmark", "maintenance")
_JOB_COLUMNS = (
    "id",
    "action",
    "name",
    "from_name",
    "to_name",
    "source",
    "status",
    "returncode",
    "started",
    "finished",
    "rolled_back",
    "observed_engine",
)
JobHook = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class Step:
    """One step of a job: run a command, poll a predicate, or call a function."""

    label: str
    argv: list[str] | None = None
    wait: Callable[[], bool] | None = None
    call: Callable[[], None] | None = None
    timeout_s: float = 30.0
    optional: bool = False  # a failure is logged and the sequence continues
    steps: tuple[Step, ...] = ()  # one failure stops this group
    cleanup: tuple[Step, ...] = ()
    cleanup_after: int = 0  # a refused preflight must not stop an existing owner


class JobConflict(Exception):
    """Another lifecycle job is already running."""


class JobStorageError(Exception):
    """The job reservation could not be persisted, so nothing was run."""


def ops_tz(offset_min: int) -> timezone:
    """JavaScript ``getTimezoneOffset`` convention: -480 means UTC+8."""
    return timezone(timedelta(minutes=-offset_min))


class JobStore(Database):
    def add_history(
        self,
        *,
        ts: float,
        action: str,
        from_name: str | None,
        to_name: str | None,
        result: str,
        duration_s: float | None,
        output_tail: str,
        source: str,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO switch_history (ts, action, from_name, to_name, result, duration_s,"
                " output_tail, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, action, from_name, to_name, result, duration_s, output_tail, source),
            )

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM switch_history ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def backfill(self, markers: Iterable[tuple[str, str]], tz_offset_min: int) -> int:
        """Insert ``===== <ts> start <name> =====`` markers once; returns rows added."""
        tz = ops_tz(tz_offset_min)
        added = 0
        with self._conn() as c:
            for raw_ts, name in markers:
                try:
                    ts = datetime.fromisoformat(raw_ts).replace(tzinfo=tz).timestamp()
                except ValueError:
                    continue
                cur = c.execute(
                    "INSERT OR IGNORE INTO switch_history (ts, action, from_name, to_name, result,"
                    " duration_s, output_tail, source) VALUES (?, 'start', NULL, ?, 'observed',"
                    " NULL, '', 'log')",
                    (ts, name),
                )
                added += cur.rowcount
        return added

    def active_by_day(self, dates: list[str], tz_offset_min: int) -> dict[str, list[str]]:
        """Names whose weights were serving at some point on each local date."""
        tz = ops_tz(tz_offset_min)
        with self._conn() as c:
            events = c.execute(
                "SELECT ts, to_name FROM switch_history WHERE to_name IS NOT NULL"
                f" AND action IN {_ACTIVATING} AND result IN {_ACTIVE_RESULTS} ORDER BY ts, id"
            ).fetchall()
        out: dict[str, list[str]] = {}
        for d in dates:
            start = datetime.fromisoformat(d).replace(tzinfo=tz).timestamp()
            end = start + 86400
            names: list[str] = []
            before = [e["to_name"] for e in events if e["ts"] < start]
            if before:
                names.append(before[-1])
            for e in events:
                if start <= e["ts"] < end and e["to_name"] not in names:
                    names.append(e["to_name"])
            out[d] = names
        return out

    def save_job(self, job: dict[str, Any]) -> None:
        cols = (*_JOB_COLUMNS, "output")
        with self._conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO jobs ({', '.join(cols)})"
                f" VALUES ({', '.join(':' + k for k in cols)})",
                job,
            )

    def interrupt_running(self, now: float) -> list[dict[str, Any]]:
        """Mark jobs a previous process left ``running`` as interrupted, with a history row.

        Their CLI may or may not have finished; nothing is replayed."""
        with self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM jobs WHERE status = 'running'")]
            for r in rows:
                c.execute(
                    "UPDATE jobs SET status = 'interrupted', finished = ? WHERE id = ?",
                    (now, r["id"]),
                )
                c.execute(
                    "INSERT INTO switch_history (ts, action, from_name, to_name, result,"
                    " duration_s, output_tail, source) VALUES (?, ?, ?, ?, 'interrupted', NULL,"
                    " '', ?)",
                    (r["started"], r["action"], r["from_name"], r["to_name"], r["source"]),
                )
        return rows

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


class JobRunner:
    """Runs one ``mlx-model`` action at a time in a background thread.

    Output goes to a file, not a pipe: ``mlx-model`` backgrounds the server, which may
    inherit stdout, so waiting for pipe EOF could hang for the server's lifetime. On
    timeout only the CLI process is signalled (SIGTERM, then SIGKILL), never its session,
    so a server it already started keeps running. mlx-model has no cleanup trap, so a
    killed run can leave ``run/mlx.lock`` behind; the admin reports it, never removes it.

    The slot is held from ``claim()`` (or ``submit``) until completion hooks have run.
    A job row is persisted as ``running`` before any step starts, so a restart can tell
    that a mutation was in flight.
    """

    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        current_name: Callable[[], str | None],
        observe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._current_name = current_name
        self.observe = observe
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running: str | None = None
        self._claim: str | None = None
        self._hooks: list[JobHook] = []
        self._held: dict[str, float] = {}  # held job id -> monotonic start
        self._dir = settings.admin_data_dir / "jobs"
        self._dir.mkdir(parents=True, exist_ok=True)

    def add_hook(self, hook: JobHook) -> None:
        """``hook(job)`` runs after the job is recorded and before the slot is released;
        ``job["recorded"]`` is False if persisting the outcome failed."""
        self._hooks.append(hook)

    def claim(self) -> str:
        """Reserve the slot so callers can validate state before choosing a job."""
        with self._lock:
            if self._running is not None or self._claim is not None:
                raise JobConflict(self._running or "claimed")
            self._claim = uuid.uuid4().hex
            return self._claim

    def release(self, claim: str) -> None:
        with self._lock:
            if self._claim == claim:
                self._claim = None

    def reconcile_interrupted(self) -> list[dict[str, Any]]:
        return self.store.interrupt_running(time.time())

    def running(self) -> dict[str, Any] | None:
        with self._lock:
            return self._view(self._running) if self._running else None

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            if job_id in self._jobs:
                return self._view(job_id)
        return self.store.get_job(job_id)

    def _log_path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.log"

    def _read_output(self, job_id: str) -> str:
        try:
            with self._log_path(job_id).open("rb") as fh:
                size = fh.seek(0, 2)
                fh.seek(max(0, size - _OUTPUT_TAIL_BYTES))
                return fh.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _view(self, job_id: str) -> dict[str, Any]:
        job = dict(self._jobs[job_id])
        job["output"] = self._read_output(job_id)
        return job

    def submit(
        self,
        action: str,
        name: str | None,
        path: str | None = None,
        *,
        source: str = "admin",
        claim: str | None = None,
    ) -> dict[str, Any]:
        if action not in LIFECYCLE_ACTIONS or action == "engine":
            raise ValueError(f"unsupported action: {action}")
        argv = build_command(self.settings.mlx_model_path, action, name, path)
        step = Step(
            f"mlx-model {action} {name or ''}".strip(),
            argv=argv,
            timeout_s=self.settings.admin_job_timeout_s,
        )
        return self._start(
            action,
            name=name,
            path=path,
            from_name=self._current_name(),
            to_name=name,
            steps=[step],
            rollback=[],
            rollback_after=0,
            source=source,
            claim=claim,
        )

    def submit_sequence(
        self,
        action: str,
        *,
        name: str | None,
        from_name: str | None,
        to_name: str | None,
        steps: list[Step],
        rollback: list[Step] | None = None,
        rollback_after: int = 0,
        source: str = "admin",
        claim: str | None = None,
    ) -> dict[str, Any]:
        if action not in LIFECYCLE_ACTIONS:
            raise ValueError(f"unsupported action: {action}")
        return self._start(
            action,
            name=name,
            path=None,
            from_name=from_name,
            to_name=to_name,
            steps=steps,
            rollback=rollback or [],
            rollback_after=rollback_after,
            source=source,
            claim=claim,
        )

    def _start(
        self,
        action: str,
        *,
        name: str | None,
        path: str | None,
        from_name: str | None,
        to_name: str | None,
        steps: list[Step],
        rollback: list[Step],
        rollback_after: int,
        source: str,
        claim: str | None,
    ) -> dict[str, Any]:
        view = self._reserve(
            action,
            name=name,
            path=path,
            from_name=from_name,
            to_name=to_name,
            labels=[s.label for s in steps],
            source=source,
            claim=claim,
        )
        threading.Thread(
            target=self._execute, args=(view["id"], steps, rollback, rollback_after), daemon=True
        ).start()
        return view

    def begin(
        self,
        action: str,
        *,
        name: str | None,
        from_name: str | None,
        to_name: str | None,
        source: str,
        claim: str | None,
    ) -> dict[str, Any]:
        """Reserve the slot for a job its caller drives with ``run_command`` and ends
        with ``finish``. The row is persisted as ``running`` first, as for any job."""
        if action not in HELD_ACTIONS:
            raise ValueError(f"unsupported held action: {action}")
        view = self._reserve(
            action,
            name=name,
            path=None,
            from_name=from_name,
            to_name=to_name,
            labels=[],
            source=source,
            claim=claim,
        )
        with self._lock:
            self._held[view["id"]] = time.monotonic()
        return view

    def _check_held(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._held or self._running != job_id:
                raise ValueError(f"job {job_id} is not a held running job")

    def note(self, job_id: str, text: str) -> None:
        self._note(job_id, text)

    def run_command(
        self, job_id: str, label: str, argv: list[str], timeout_s: float
    ) -> tuple[str, int | None]:
        """Run one command inside a held job (bounded by ``timeout_s``, output logged)."""
        self._check_held(job_id)
        return self._run_step(job_id, Step(label, argv=argv, timeout_s=timeout_s))

    def finish(self, job_id: str, status: str, *, rolled_back: bool | None) -> dict[str, Any]:
        """Record a held job's outcome, run hooks and release the slot."""
        self._check_held(job_id)
        with self._lock:
            t0 = self._held.pop(job_id)
        self._complete(job_id, status, None, rolled_back, t0)
        job = self.get(job_id)
        assert job is not None
        return job

    def _reserve(
        self,
        action: str,
        *,
        name: str | None,
        path: str | None,
        from_name: str | None,
        to_name: str | None,
        labels: list[str],
        source: str,
        claim: str | None,
    ) -> dict[str, Any]:
        if source not in JOB_SOURCES:
            raise ValueError(f"unsupported source: {source}")
        with self._lock:
            if self._running is not None or self._claim != claim:
                raise JobConflict(self._running or "claimed")
            job_id = uuid.uuid4().hex[:12]
            job: dict[str, Any] = {
                "id": job_id,
                "action": action,
                "name": name,
                "path": path,
                "from_name": from_name,
                "to_name": to_name,
                "source": source,
                "steps": labels,
                "rolled_back": None,
                "observed_engine": None,
                "status": "running",
                "returncode": None,
                "started": time.time(),
                "finished": None,
            }
            try:
                self.store.save_job({k: job[k] for k in _JOB_COLUMNS} | {"output": ""})
            except Exception as exc:
                self._claim = None
                log.exception("job %s: could not persist the reservation", job_id)
                raise JobStorageError(str(exc)) from exc
            self._jobs[job_id] = job
            self._running = job_id
            self._claim = None
            return self._view(job_id)

    def _note(self, job_id: str, text: str) -> None:
        with self._log_path(job_id).open("a") as fh:
            fh.write(text + "\n")

    def _run_process(
        self, job_id: str, argv: list[str], timeout_s: float
    ) -> tuple[str, int | None]:
        path = self._log_path(job_id)
        try:
            with path.open("ab") as out:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            self._note(job_id, f"failed to run {argv[0]}: {exc}")
            return "failed", None
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # SIGTERM first: gentler than SIGKILL for mlx-model and its curl/lsof children
            proc.terminate()
            try:
                proc.wait(timeout=_TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return "timeout", None
        return ("succeeded" if rc == 0 else "failed"), rc

    def _run_step(self, job_id: str, step: Step) -> tuple[str, int | None]:
        self._note(job_id, f"==> {step.label}")
        status: str
        rc: int | None = None
        try:
            if step.steps:
                status, rc, failed_at = self._run_steps(job_id, list(step.steps))
                if status != "succeeded" and step.cleanup and failed_at >= step.cleanup_after:
                    cleanup_status, _, _ = self._run_steps(job_id, list(step.cleanup))
                    if cleanup_status != "succeeded":
                        # Continuing after failed cleanup could admit another GPU owner.
                        status = "cleanup_failed"
            elif step.argv is not None:
                status, rc = self._run_process(job_id, step.argv, step.timeout_s)
            elif step.wait is not None:
                deadline = time.monotonic() + step.timeout_s
                status = "timeout"
                while time.monotonic() < deadline:
                    if step.wait():
                        status = "succeeded"
                        break
                    time.sleep(WAIT_POLL_S)
            else:
                assert step.call is not None
                step.call()
                status = "succeeded"
        except Exception as exc:  # a step's failure is the job's result, not a crash
            self._note(job_id, f"error: {exc}")
            status = "failed"
        suffix = " (optional, continuing)" if status != "succeeded" and step.optional else ""
        self._note(job_id, f"<== {step.label}: {status}{suffix}")
        return status, rc

    def _run_steps(self, job_id: str, steps: list[Step]) -> tuple[str, int | None, int]:
        """Returns (status, return code of the last command step, index of the failed
        step or ``len(steps)``)."""
        last_rc: int | None = None
        for i, step in enumerate(steps):
            status, rc = self._run_step(job_id, step)
            if step.argv is not None:
                last_rc = rc
            if status == "cleanup_failed":
                return "failed", last_rc, i
            if status != "succeeded" and not step.optional:
                return status, last_rc, i
        return "succeeded", last_rc, len(steps)

    def _observe(self, job_id: str) -> str | None:
        if self.observe is None:
            return None
        try:
            return json.dumps(self.observe())
        except Exception:
            log.exception("job %s: observing the engine failed", job_id)
            return None

    def _execute(
        self, job_id: str, steps: list[Step], rollback: list[Step], rollback_after: int
    ) -> None:
        t0 = time.monotonic()
        status, rc = "failed", None
        rolled_back: bool | None = None
        try:
            status, rc, failed_at = self._run_steps(job_id, steps)
            if status != "succeeded" and rollback and failed_at >= rollback_after:
                self._note(job_id, "rollback: restoring the previous engine")
                rb_status, _rc, _i = self._run_steps(job_id, rollback)
                rolled_back = rb_status == "succeeded"
        except Exception:
            log.exception("job %s: running steps failed", job_id)
            status = "failed"
        self._complete(job_id, status, rc, rolled_back, t0)

    def _complete(
        self, job_id: str, status: str, rc: int | None, rolled_back: bool | None, t0: float
    ) -> None:
        """Persist the outcome, run hooks with the slot held, then release it."""
        recorded = False
        observed: str | None = None
        try:
            observed = self._observe(job_id)
            with self._lock:
                job = dict(self._jobs[job_id])
            output = self._read_output(job_id)
            to_name = job["to_name"]
            if job["action"] == "start" and to_name is None:
                to_name = self._current_name()
            with self._lock:
                self._jobs[job_id]["to_name"] = to_name
            self.store.add_history(
                ts=job["started"],
                action=job["action"],
                from_name=job["from_name"],
                to_name=to_name,
                result=status,
                duration_s=round(time.monotonic() - t0, 3),
                output_tail=output[-2000:],
                source=job["source"],
            )
            job.update(
                to_name=to_name,
                status=status,
                returncode=rc,
                finished=time.time(),
                rolled_back=rolled_back,
                observed_engine=observed,
            )
            self.store.save_job({k: job[k] for k in _JOB_COLUMNS} | {"output": output})
            recorded = True
        except Exception:
            log.exception("job %s: recording failed", job_id)
            status = "failed"
        finally:
            outcome = {
                "status": status,
                "returncode": rc,
                "finished": time.time(),
                "rolled_back": rolled_back,
                "observed_engine": observed,
            }
            with self._lock:
                final = dict(self._jobs[job_id]) | outcome | {"recorded": recorded}
            # hooks run while the slot is still held, so nothing can act on stale state
            for hook in self._hooks:
                try:
                    hook(dict(final))
                except Exception:
                    log.exception("job %s: completion hook failed", job_id)
            # Status flip and slot release are one step, so "finished" always means "free".
            with self._lock:
                self._jobs[job_id].update(outcome)
                self._running = None
