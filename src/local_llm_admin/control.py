"""Lifecycle control shared by the HTTP routes and (later) the watchdog.

Every mutation claims the runner slot first and only then validates the drive, the
``mlx.lock`` and the running engines, so the checks cannot go stale before submission.
The intended engine (``off``, ``comfyui``, ``mlx:<name>`` or ``unknown``) is persisted
only from recorded job outcomes, never inferred from history rows.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from . import sidecars as sidecars_mod
from .config import Settings
from .decision import DecisionClient
from .engines import ComfyClient, TransitionRefused, parse_target, plan_transition, resolve_active
from .inventory import build_remove_argv, removal_preview
from .jobs import JobConflict, JobRunner, JobStorageError, JobStore, Step
from .mlx import MlxClient, build_command, validate_name
from .sidecars import (
    SidecarPlan,
    SidecarRegistry,
    SidecarSpec,
    plan_service_start,
    plan_sidecar_start,
)

log = logging.getLogger("local_llm_admin")

INTENT_KEY = "intent"
SUSPENDED_KEY = "recovery_suspended"
CRASHES_KEY = "watchdog_crashes"  # wall-clock times of crashes the watchdog acted on
UNKNOWN = "unknown"
# jobs that never start or stop an engine: interrupting one leaves the intent known
MAINTENANCE_ACTIONS = ("rotate-logs", "remove", "profile", "decision", "sidecar", "service")


class ControlError(Exception):
    """A refused lifecycle request; ``code`` is the HTTP status the routes return."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EngineOpsBinding:
    """``engines.EngineOps`` bound to this app's clients (argv via the owning CLIs only)."""

    def __init__(
        self,
        settings: Settings,
        mlx: MlxClient,
        comfy: ComfyClient,
        sidecars: SidecarRegistry,
    ) -> None:
        self._settings = settings
        self._mlx = mlx
        self._comfy = comfy
        self._sidecars = sidecars
        self.stop_timeout_s = settings.engine_stop_timeout_s
        self.ready_timeout_s = settings.comfyui_ready_timeout_s
        self.job_timeout_s = settings.admin_job_timeout_s

    def mlx_argv(self, action: str, name: str | None) -> list[str]:
        return build_command(self._settings.mlx_model_path, action, name)

    def comfy_argv(self, action: str) -> list[str]:
        return self._comfy.argv(action)

    def mlx_down(self) -> bool:
        return not self._mlx.engine_running()

    def comfy_down(self) -> bool:
        return self._comfy.down_sync()

    def comfy_ready(self) -> bool:
        return self._comfy.ready_sync()

    def comfy_cancel_all(self) -> None:
        self._comfy.cancel_all_sync()

    def sidecar_argv(self, name: str, action: str) -> list[str]:
        return self._sidecars.argv(name, action)

    def sidecar_down(self, name: str) -> bool:
        return self._sidecars.down_sync(name)

    def sidecar_ready(self, name: str) -> bool:
        return self._sidecars.ready_sync(name)

    def sidecar_warmup(self, name: str) -> Callable[[], bool] | None:
        return self._sidecars.warmup(name)


def process_generation(pid: int | None) -> dict[str, Any] | None:
    """PID plus process creation time: a reused PID is a different generation."""
    if pid is None:
        return None
    try:
        return {"pid": pid, "started": psutil.Process(pid).create_time()}
    except (psutil.Error, ValueError):
        return None


def intent_after(job: dict[str, Any]) -> str | None:
    """Intended engine after a successful job; None for actions that leave it alone."""
    action, to_name = job["action"], job["to_name"]
    if action == "stop":
        return "off"
    if action in ("switch", "start"):
        return f"mlx:{to_name}" if to_name else UNKNOWN
    if action == "engine":
        if to_name is None:
            return "off"
        return "comfyui" if to_name == "comfyui" else f"mlx:{to_name}"
    return None  # add (and later unlock) never change the intended engine


def adopted_intent(observed: dict[str, Any]) -> str:
    engine = observed["engine"]
    if engine == "mlx":
        return f"mlx:{observed['mlx_name']}" if observed["mlx_name"] else UNKNOWN
    return {"comfyui": "comfyui", "none": "off"}.get(engine, UNKNOWN)


class ControlService:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        runner: JobRunner,
        mlx: MlxClient,
        comfy: ComfyClient,
        *,
        decision: DecisionClient | None = None,
        sidecars: SidecarRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.mlx = mlx
        self.comfy = comfy
        self.decision = decision
        self.sidecars = sidecars or SidecarRegistry(
            settings, {"openjev": decision.transport} if decision else None
        )
        self.ops = EngineOpsBinding(settings, mlx, comfy, self.sidecars)
        # set when the database cannot record an outcome; blocks automatic recovery
        # for the life of this process even though nothing could be persisted
        self._unrecorded: str | None = None
        self._state_lock = threading.Lock()
        runner.observe = self.observe
        runner.add_hook(self.on_job_complete)

    # --- state -------------------------------------------------------------
    def intent(self) -> dict[str, Any] | None:
        value: dict[str, Any] | None = self.store.get_kv(INTENT_KEY)
        return value

    def recovery_blocked(self) -> str | None:
        """Why automatic recovery must not act, or None."""
        if self._unrecorded:
            return self._unrecorded
        suspended = self.store.get_kv(SUSPENDED_KEY)
        return str(suspended["reason"]) if suspended else None

    def _set_intent(self, value: str, reason: str, job: dict[str, Any] | None = None) -> None:
        self.store.set_kv(
            INTENT_KEY,
            {
                "value": value,
                "reason": reason,
                "job_id": job["id"] if job else None,
                "source": job["source"] if job else None,
                "ts": time.time(),
            },
        )

    def _sidecar_outcome(self, job: dict[str, Any]) -> None:
        """Caller holds the state lock. A failed swap whose old sidecar is down leaves
        neither meant to run (D-S3), so no down alert fires for either."""
        name = str(job["name"])
        if job["status"] == "succeeded":
            sidecars_mod.write_intent(self.store, name, str(job["to_name"]), job)
        elif job["from_name"] and self.sidecars.down_sync(str(job["from_name"])):
            sidecars_mod.write_intent(self.store, name, "stopped", job)

    def _service_outcome(self, job: dict[str, Any]) -> None:
        """Caller holds the state lock. A failed start was rolled back (stopped), so the
        service is meant to stay off and no down alert fires (M8)."""
        try:
            spec = self.sidecars.get_service(str(job["name"]))
        except KeyError:
            return
        if job["status"] == "succeeded":
            sidecars_mod.write_service_intent(self.store, spec, str(job["to_name"]), job)
        elif job["to_name"] == "running":
            sidecars_mod.write_service_intent(self.store, spec, "stopped", job)

    def _suspend(self, reason: str) -> None:
        self.store.set_kv(SUSPENDED_KEY, {"reason": reason, "ts": time.time()})

    def suspend(self, reason: str) -> None:
        with self._state_lock:
            self._suspend(reason)

    def set_intent(self, value: str, reason: str) -> None:
        with self._state_lock:
            self._set_intent(value, reason)

    def resume(self) -> None:
        """Clear suspension and the crash budget. Blockers are still checked each tick."""
        with self._state_lock:
            self.store.delete_kv(SUSPENDED_KEY)
            self.store.delete_kv(CRASHES_KEY)
            self._unrecorded = None

    def on_job_complete(self, job: dict[str, Any]) -> None:
        """Runner hook; runs before the slot is released."""
        with self._state_lock:
            try:
                if not job["recorded"]:
                    self._unrecorded = f"job {job['id']} ({job['action']}) outcome was not recorded"
                    self._set_intent(UNKNOWN, "unrecorded", job)
                    self._suspend(self._unrecorded)
                    return
                if job["action"] == "service":
                    self._service_outcome(job)
                    return
                if job["action"] in ("sidecar", "decision"):
                    self._sidecar_outcome(job)
                    return
                if job["action"] == "quality":
                    # a quality run switches models itself and restores at the end;
                    # rolled_back is its restoration outcome
                    if job["rolled_back"] is False:
                        self._set_intent(UNKNOWN, "quality run not restored", job)
                        self._suspend(f"quality run (job {job['id']}) did not restore the engine")
                    return
                target = intent_after(job)
                if target is None:
                    return
                if job["status"] == "succeeded":
                    self._set_intent(target, "job", job)
                    if job["source"] == "admin":  # an explicit manual action resets suspension
                        self.store.delete_kv(SUSPENDED_KEY)
                        self.store.delete_kv(CRASHES_KEY)
                elif job["rolled_back"] is False:
                    self._set_intent(UNKNOWN, "rollback failed", job)
                    self._suspend(f"rollback failed after job {job['id']}")
                # otherwise the engine is back where it was: the prior intent stands
            except Exception:
                log.exception("job %s: persisting the intended engine failed", job["id"])
                self._unrecorded = self._unrecorded or (
                    f"intended engine after job {job['id']} was not recorded"
                )

    # --- observation -------------------------------------------------------
    def observe(self) -> dict[str, Any]:
        """Which engine runs now, and its process generation."""
        if not self.mlx.drive_mounted():
            return {"engine": UNKNOWN, "mlx_name": None, "generation": None}
        mlx_running = self.mlx.engine_running()
        comfy = self.comfy.running()
        active = resolve_active(mlx_running, comfy.running)
        name: str | None = None
        pid: int | None = None
        if active == "mlx":
            name = self.mlx.active_env().get("MLX_ACTIVE_NAME")
            pid = self.mlx.pid_from_file()
        elif active == "comfyui":
            pid = comfy.pid
        return {"engine": active, "mlx_name": name, "generation": process_generation(pid)}

    # --- startup -----------------------------------------------------------
    def reconcile(self) -> list[dict[str, Any]]:
        """Must run before the first submission: marks jobs a previous process left
        running as interrupted. Nothing is replayed; the outcome is unknown."""
        interrupted = self.runner.reconcile_interrupted()
        engine_jobs = [j for j in interrupted if j["action"] not in MAINTENANCE_ACTIONS]
        if interrupted:
            log.warning(
                "jobs interrupted by an admin restart: %s", ", ".join(j["id"] for j in interrupted)
            )
        if engine_jobs:
            ids = ", ".join(j["id"] for j in engine_jobs)
            self._set_intent(UNKNOWN, "interrupted")
            self._suspend(f"job(s) {ids} were interrupted by an admin restart")
        return interrupted

    def adopt_initial(self) -> None:
        """First run only: adopt the observed engine when it is unambiguous."""
        try:
            claim = self.runner.claim()
        except JobConflict:
            return  # a job is deciding the intent right now
        try:
            if self.intent() is None:
                observed = self.observe()
                self._set_intent(adopted_intent(observed), "adopted")
                log.info("adopted intended engine from %s", observed)
        finally:
            self.runner.release(claim)

    def startup(self) -> None:
        self.reconcile()
        self.adopt_initial()

    # --- submission --------------------------------------------------------
    def _claim(self) -> str:
        try:
            return self.runner.claim()
        except JobConflict:
            raise ControlError(409, self.busy_message()) from None

    def busy_message(self) -> str:
        running = self.runner.running()
        if running and running["action"] == "quality":
            return "a quality run is in progress; cancel it on the Quality page first"
        return "another job is running"

    def preflight(self, check_lock: bool = True) -> None:
        self._preflight(check_lock)

    def _preflight(self, check_lock: bool = True) -> None:
        if not self.mlx.drive_mounted():
            raise ControlError(409, "model drive is not mounted")
        if check_lock and self.mlx.lock_exists():
            age_min = int((self.mlx.lock_age_s() or 0) // 60)
            # mlx-model has no cleanup trap: a start that fails leaves the lock behind.
            # The admin never writes under the MLX root, so it cannot clear it.
            raise ControlError(
                409,
                f"run/mlx.lock exists (age {age_min} min). Another mlx-model command is running,"
                " or an earlier one exited without releasing the lock. If no mlx-model process is"
                " running on the Mac, remove /Volumes/Models/run/mlx.lock by hand.",
            )

    def sidecar_running(self) -> SidecarSpec | None:
        running = self.sidecars.running()
        return running[0] if running else None

    def decision_running(self) -> bool:
        return any(s.name == "openjev" for s in self.sidecars.running())

    @staticmethod
    def _conflict(running: list[SidecarSpec]) -> ControlError:
        names = ", ".join(s.name for s in running)
        return ControlError(
            409,
            f"more than one sidecar is running ({names}); stop all but one first"
            " (Models page, Sidecar card)",
        )

    async def _check_sidecar_allows(self, name: str | None) -> None:
        running = await asyncio.to_thread(self.sidecars.running)
        running += await asyncio.to_thread(self.sidecars.services_running)
        for spec in running:
            if not spec.allows(name):
                raise ControlError(
                    409,
                    f"{spec.noun} is running and {name or 'that model'} is not"
                    f" allowed next to it; stop the {spec.kind} first, or use the Engine"
                    " control with confirmation",
                )

    async def _known_name(self, name: str) -> None:
        known = {m["name"] for m in await asyncio.to_thread(self.mlx.list_models)}
        if name not in known:
            raise ControlError(404, "unknown model name")

    @staticmethod
    def _submitted(submit: Any) -> dict[str, Any]:
        try:
            job: dict[str, Any] = submit()
        except JobConflict:
            raise ControlError(409, "another job is running") from None
        except JobStorageError:
            raise ControlError(503, "the admin database could not record the job") from None
        return job

    async def lifecycle(
        self, action: str, name: str | None, *, source: str = "admin"
    ) -> dict[str, Any]:
        if name is not None:
            try:
                validate_name(name)
            except ValueError:
                raise ControlError(400, "invalid model name") from None
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight)
            if (
                action in ("switch", "start")
                and (await asyncio.to_thread(self.comfy.running)).running
            ):
                raise ControlError(
                    409, "ComfyUI is running; use the Engine control to switch to MLX"
                )
            if name is not None:
                await self._known_name(name)
            if action in ("switch", "start"):
                target = name or self.mlx.active_env().get("MLX_ACTIVE_NAME")
                await self._check_sidecar_allows(target)
            job = self._submitted(
                lambda: self.runner.submit(action, name, source=source, claim=claim)
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: %s %s (%s)", job["id"], action, name or "", source)
        return job

    async def register(self, dir_name: str, name: str) -> dict[str, Any]:
        """Register a downloaded weights dir through ``mlx-model add`` (it owns models.conf)."""
        try:
            validate_name(name)
        except ValueError:
            raise ControlError(400, "invalid model name") from None
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight)
            registered = await asyncio.to_thread(self.mlx.list_models)
            discovered = await asyncio.to_thread(self.mlx.discover, registered)
            entry = next((d for d in discovered if d["dir_name"] == dir_name), None)
            if entry is None:
                raise ControlError(404, "model directory not found under models/")
            if entry["registered_as"]:
                raise ControlError(
                    409, f"directory is already registered as {entry['registered_as']}"
                )
            if entry["state"] != "complete" or not entry["has_config"]:
                what = entry["state"] if entry["has_config"] else "missing config.json"
                raise ControlError(
                    409, f"weights are {what}; register once the download is complete"
                )
            if name in {m["name"] for m in registered}:
                # mlx-model add would repoint the existing name at this directory
                raise ControlError(409, "name is already used by another model")
            job = self._submitted(
                lambda: self.runner.submit("add", name, path=entry["path"], claim=claim)
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: add %s %s", job["id"], name, entry["path"])
        return job

    def remove_preview(self, name: str) -> dict[str, Any] | None:
        """Read-only: what ``mlx-model remove`` would touch and why it would refuse."""
        env = self.mlx.active_env()
        return removal_preview(
            self.mlx.list_models(),
            name,
            selected=env.get("MLX_ACTIVE_NAME"),
            loaded=self.mlx.engine_running(),
            models_root=self.settings.mlx_root / "models",
            dir_size=self.mlx.dir_size,
        )

    async def remove(
        self, name: str, delete_files: bool, confirmation: str | None
    ) -> dict[str, Any]:
        """``mlx-model remove`` as a job. The preview is recomputed with the slot held
        and its canonical path and inode are passed on, so the CLI refuses a target
        that changed in between; it also rechecks everything under its own lock."""
        try:
            validate_name(name)
        except ValueError:
            raise ControlError(400, "invalid model name") from None
        if delete_files and confirmation != name:
            raise ControlError(400, "type the model name to confirm deleting its files")
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight)
            preview = await asyncio.to_thread(self.remove_preview, name)
            if preview is None:
                raise ControlError(404, "unknown model name")
            refusals = preview["delete_refusals" if delete_files else "unregister_refusals"]
            if refusals:
                raise ControlError(409, "; ".join(refusals))
            argv = build_remove_argv(
                self.settings.mlx_model_path,
                name,
                delete_files,
                preview["canonical"] if delete_files else None,
                preview["inode"] if delete_files else None,
            )
            label = f"mlx-model remove {name}" + (" --delete-files" if delete_files else "")
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "remove",
                    name=name,
                    from_name=name,
                    to_name=None,
                    steps=[Step(label, argv=argv, timeout_s=self.settings.admin_job_timeout_s)],
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: remove %s (delete_files=%s)", job["id"], name, delete_files)
        return job

    async def cli_job(
        self, action: str, name: str | None, argv: list[str], label: str
    ) -> dict[str, Any]:
        """One mlx-model command that never starts or stops an engine, as a job: it
        holds the runner slot, so it cannot race a lifecycle job or a quality run."""
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight)
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    action,
                    name=name,
                    from_name=None,
                    to_name=None,
                    steps=[Step(label, argv=argv, timeout_s=60.0)],
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: %s", job["id"], label)
        return job

    async def unlock(self) -> dict[str, Any]:
        """``mlx-model unlock`` as a job: holds the slot, skips only the lock preflight."""
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight, False)
            job = self._submitted(lambda: self.runner.submit("unlock", None, claim=claim))
        finally:
            self.runner.release(claim)
        log.info("job %s: unlock", job["id"])
        return job

    async def rotate_logs(self, argv: list[str], timeout_s: float = 120.0) -> dict[str, Any]:
        """``mlx-model rotate-logs`` as a maintenance job: it holds the runner slot, so it
        never races a lifecycle job, and the CLI rechecks writers under its own lock."""
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight)
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "rotate-logs",
                    name=None,
                    from_name=None,
                    to_name=None,
                    steps=[Step("mlx-model rotate-logs", argv=argv, timeout_s=timeout_s)],
                    source="maintenance",
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: rotate-logs", job["id"])
        return job

    async def engine(
        self, target_raw: str, force: bool = False, *, source: str = "admin"
    ) -> dict[str, Any]:
        try:
            target = parse_target(target_raw)
        except ValueError:
            raise ControlError(400, "target must be off, comfyui, or mlx:<name>") from None
        claim = self._claim()
        try:
            mlx_running = await asyncio.to_thread(self.mlx.engine_running)
            comfy_running = (await asyncio.to_thread(self.comfy.running)).running
            # drive always; mlx.lock only when mlx-model will be called
            await asyncio.to_thread(self._preflight, target.kind == "mlx" or mlx_running)
            if target.kind == "mlx":
                assert target.name is not None
                await self._known_name(target.name)
            q = await self.comfy.queue() if comfy_running else None
            running_sidecars = await asyncio.to_thread(self.sidecars.running)
            if len(running_sidecars) > 1:
                raise self._conflict(running_sidecars)
            sidecar = running_sidecars[0] if running_sidecars else None
            services = await asyncio.to_thread(self.sidecars.services_running)
            up = {s.name for s in services}
            restore = [
                s
                for s in self.sidecars.services()
                if s.name not in up
                and sidecars_mod.read_service_intent(self.store, s) == "running"
            ]
            try:
                plan = plan_transition(
                    target,
                    mlx_running=mlx_running,
                    mlx_name=self.mlx.active_env().get("MLX_ACTIVE_NAME") if mlx_running else None,
                    comfy_running=comfy_running,
                    comfy_queue=sum(q) if q is not None else None if comfy_running else 0,
                    force=force,
                    ops=self.ops,
                    sidecar=sidecar,
                    services=services,
                    restore=restore,
                )
            except TransitionRefused as exc:
                raise ControlError(exc.code, exc.message) from None
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "engine",
                    name=target.name,
                    from_name=plan.from_label,
                    to_name=plan.to_label,
                    steps=plan.steps,
                    rollback=plan.rollback,
                    rollback_after=plan.rollback_after,
                    source=source,
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        if sidecar is not None and any(st.label == f"{sidecar.name} stop" for st in plan.steps):
            # the user chose an engine that excludes the sidecar: it is meant to stay off
            with self._state_lock:
                sidecars_mod.write_intent(self.store, sidecar.name, "stopped", job)
        if plan.forced_stops or plan.unrestored:
            # a confirmed switch to a model the service excludes: it is meant to stay off
            with self._state_lock:
                for name in (*plan.forced_stops, *plan.unrestored):
                    spec = self.sidecars.get_service(name)
                    sidecars_mod.write_service_intent(self.store, spec, "stopped", job)
        log.info("job %s: engine %s -> %s (%s)", job["id"], plan.from_label, plan.to_label, source)
        return job

    # --- sidecar slot (M7) ---------------------------------------------------------
    def _require_sidecar(self, name: str) -> SidecarSpec:
        try:
            return self.sidecars.get(name)
        except KeyError:
            raise ControlError(404, f"the {name} sidecar is not enabled") from None

    def _require_decision(self) -> None:
        if self.decision is None or not self.settings.decision_enabled:
            raise ControlError(404, "the decision sidecar is not enabled")

    def _mlx_estimate(self, name: str | None) -> float | None:
        path = self.mlx.active_env().get("MLX_ACTIVE_PATH")
        if not path:
            return None
        cfg = sidecars_mod.read_json(Path(path) / "config.json") or {}
        profile = (
            sidecars_mod.read_json(self.settings.mlx_root / "etc" / "profiles" / f"{name}.json")
            if name
            else None
        )
        tokens = sidecars_mod.context_tokens(cfg, profile)
        return sidecars_mod.mlx_estimate_gb(self.mlx.dir_size(Path(path)), cfg, tokens)

    def _sidecar_plan(
        self, spec: SidecarSpec, other: SidecarSpec | None, confirm: bool, pressure: str | None
    ) -> SidecarPlan:
        mlx_running = self.mlx.engine_running()
        name = self.mlx.active_env().get("MLX_ACTIVE_NAME") if mlx_running else None
        return plan_sidecar_start(
            spec=spec,
            other=other,
            confirm=confirm,
            comfy_running=self.comfy.running().running,
            mlx_running=mlx_running,
            mlx_name=name,
            mlx_gb=self._mlx_estimate(name) if mlx_running else None,
            headroom_gb=self.settings.decision_headroom_gb,
            ram_gb=sidecars_mod.system_ram_gb(),
            wired_limit_gb=sidecars_mod.wired_limit_gb(),
            pressure=pressure,
            services=self.sidecars.services_running(),
        )

    def _sidecar_start_steps(self, spec: SidecarSpec) -> list[Step]:
        steps = [
            Step(f"{spec.name} start", argv=self.sidecars.argv(spec.name, "start"), timeout_s=60.0),
            Step(
                f"wait for :{spec.port} to be ready",
                wait=lambda: self.sidecars.ready_sync(spec.name),
                timeout_s=spec.ready_timeout_s,
            ),
        ]
        warm = self.sidecars.warmup(spec.name)
        if warm is not None:
            # loads the weights now, so the job only succeeds once a request really works
            steps.append(Step(f"warm up {spec.name}", wait=warm, timeout_s=spec.ready_timeout_s))
        return steps

    def _sidecar_stop_steps(self, spec: SidecarSpec, optional: bool = False) -> list[Step]:
        return [
            Step(
                f"{spec.name} stop",
                argv=self.sidecars.argv(spec.name, "stop"),
                timeout_s=60.0,
                optional=optional,
            ),
            Step(
                f"wait for :{spec.port} to close",
                wait=lambda: self.sidecars.down_sync(spec.name),
                timeout_s=self.settings.engine_stop_timeout_s,
            ),
        ]

    async def sidecar_budget(
        self, name: str, pressure: str | None, confirm: bool = True
    ) -> SidecarPlan:
        """Read-only: whether a start (a swap when ``confirm``) would pass now."""
        spec = self._require_sidecar(name)

        def run() -> SidecarPlan:
            others = [s for s in self.sidecars.running() if s.name != spec.name]
            return self._sidecar_plan(spec, others[0] if others else None, confirm, pressure)

        return await asyncio.to_thread(run)

    async def sidecar_start(
        self, name: str, pressure: str | None, *, confirm: bool = False
    ) -> dict[str, Any]:
        """Start ``name``; with ``confirm``, stop the running sidecar first in the same job.
        Every check runs with the slot held; a refused start runs nothing."""
        spec = self._require_sidecar(name)
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight, False)
            running = await asyncio.to_thread(self.sidecars.running)
            if len(running) > 1:
                raise self._conflict(running)
            if running and running[0].name == spec.name:
                raise ControlError(409, f"the {spec.label} sidecar is already running")
            other = running[0] if running else None
            plan = await asyncio.to_thread(self._sidecar_plan, spec, other, confirm, pressure)
            if not plan.ok:
                raise ControlError(409, "; ".join(plan.reasons))
            stop = self._sidecar_stop_steps(other) if other else []
            steps = stop + self._sidecar_start_steps(spec)
            # rollback stops only the new sidecar; the old one is never restarted (D-S3)
            rollback = self._sidecar_stop_steps(spec, optional=True)
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "sidecar",
                    name=spec.name,
                    from_name=other.name if other else None,
                    to_name="running",
                    steps=steps,
                    rollback=rollback,
                    rollback_after=len(stop),
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: sidecar start %s (%s)", job["id"], spec.name, "; ".join(plan.lines))
        return job

    async def sidecar_stop(self, name: str) -> dict[str, Any]:
        spec = self._require_sidecar(name)
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight, False)
            if await asyncio.to_thread(self.sidecars.down_sync, spec.name):
                raise ControlError(409, f"the {spec.label} sidecar is not running")
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "sidecar",
                    name=spec.name,
                    from_name=None,
                    to_name="stopped",
                    steps=self._sidecar_stop_steps(spec),
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: sidecar stop %s", job["id"], spec.name)
        return job

    # --- always-on services (M8) ------------------------------------------------------
    def _require_service(self, name: str) -> SidecarSpec:
        try:
            return self.sidecars.get_service(name)
        except KeyError:
            raise ControlError(404, f"the {name} service is not enabled") from None

    def _service_plan(self, spec: SidecarSpec, pressure: str | None) -> SidecarPlan:
        mlx_running = self.mlx.engine_running()
        name = self.mlx.active_env().get("MLX_ACTIVE_NAME") if mlx_running else None
        slot = self.sidecars.running()
        return plan_service_start(
            spec=spec,
            sidecar=slot[0] if slot else None,
            comfy_running=self.comfy.running().running,
            mlx_running=mlx_running,
            mlx_name=name,
            mlx_gb=self._mlx_estimate(name) if mlx_running else None,
            headroom_gb=self.settings.decision_headroom_gb,
            ram_gb=sidecars_mod.system_ram_gb(),
            wired_limit_gb=sidecars_mod.wired_limit_gb(),
            pressure=pressure,
        )

    async def service_budget(self, name: str, pressure: str | None) -> SidecarPlan:
        """Read-only: whether a start would pass now."""
        spec = self._require_service(name)
        return await asyncio.to_thread(self._service_plan, spec, pressure)

    async def service_start(self, name: str, pressure: str | None) -> dict[str, Any]:
        """Start an always-on service. Every check runs with the slot held; a refused start
        runs nothing; a failed start is rolled back (stopped)."""
        spec = self._require_service(name)
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight, False)
            if not await asyncio.to_thread(self.sidecars.down_sync, spec.name):
                raise ControlError(409, f"{spec.noun} is already running")
            plan = await asyncio.to_thread(self._service_plan, spec, pressure)
            if not plan.ok:
                raise ControlError(409, "; ".join(plan.reasons))
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "service",
                    name=spec.name,
                    from_name=None,
                    to_name="running",
                    steps=self._sidecar_start_steps(spec),
                    rollback=self._sidecar_stop_steps(spec, optional=True),
                    rollback_after=0,
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: service start %s (%s)", job["id"], spec.name, "; ".join(plan.lines))
        return job

    async def service_stop(self, name: str) -> dict[str, Any]:
        spec = self._require_service(name)
        claim = self._claim()
        try:
            await asyncio.to_thread(self._preflight, False)
            if await asyncio.to_thread(self.sidecars.down_sync, spec.name):
                raise ControlError(409, f"{spec.noun} is not running")
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    "service",
                    name=spec.name,
                    from_name=None,
                    to_name="stopped",
                    steps=self._sidecar_stop_steps(spec),
                    claim=claim,
                )
            )
        finally:
            self.runner.release(claim)
        log.info("job %s: service stop %s", job["id"], spec.name)
        return job

    # --- M6 names, kept for /api/decision/* (D-S13) --------------------------------
    async def decision_budget(self, pressure: str | None) -> SidecarPlan:
        self._require_decision()
        return await self.sidecar_budget("openjev", pressure, confirm=False)

    async def decision_start(self, pressure: str | None) -> dict[str, Any]:
        self._require_decision()
        return await self.sidecar_start("openjev", pressure)

    async def decision_stop(self) -> dict[str, Any]:
        self._require_decision()
        return await self.sidecar_stop("openjev")
