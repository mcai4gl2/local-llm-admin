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
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from . import sidecars as sidecars_mod
from .backend_types import EngineObservation, EngineTarget, generation_record, resolve_observations
from .backends import BackendDisabled, BackendRegistry, build_registry
from .backends import process_generation as process_generation
from .config import Settings
from .decision import DecisionClient
from .engines import (
    ComfyClient,
    StrataClient,
    Transition,
    TransitionRefused,
    plan_transition,
)
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
from .splash import SplashClient

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


class SidecarOpsBinding:
    """The remaining sidecar operations used by the pure resource-policy planner."""

    def __init__(self, settings: Settings, sidecars: SidecarRegistry) -> None:
        self._settings, self._sidecars = settings, sidecars

    @property
    def stop_timeout_s(self) -> float:
        return self._settings.engine_stop_timeout_s

    @property
    def job_timeout_s(self) -> float:
        return self._settings.admin_job_timeout_s

    def sidecar_argv(self, name: str, action: str) -> list[str]:
        return self._sidecars.argv(name, action)

    def sidecar_down(self, name: str) -> bool:
        return self._sidecars.down_sync(name)

    def sidecar_ready(self, name: str) -> bool:
        return self._sidecars.ready_sync(name)

    def sidecar_warmup(self, name: str) -> Callable[[], bool] | None:
        return self._sidecars.warmup(name)


def intent_after(job: dict[str, Any], registry: BackendRegistry | None = None) -> str | None:
    """Intended engine after a successful job; None for actions that leave it alone."""
    action, to_name = job["action"], job["to_name"]
    if action == "stop":
        return "off"
    if action in ("switch", "start"):
        return f"mlx:{to_name}" if to_name else UNKNOWN
    if action == "engine":
        if to_name is None:
            return "off"
        if registry is not None and ":" in to_name:
            kind, _, reference = to_name.partition(":")
            try:
                return registry.get(kind).intent(EngineTarget(kind, reference))
            except ValueError:
                return UNKNOWN
        if registry is not None and to_name != job.get("name"):
            try:
                backend = registry.get(to_name)
            except ValueError:
                pass  # old rows use the MLX model label in to_name
            else:
                return backend.intent(EngineTarget(to_name, job.get("name")))
        if registry is not None:
            return f"mlx:{to_name}"
        return to_name if to_name in ("comfyui", "strata") else f"mlx:{to_name}"
    return None  # add (and later unlock) never change the intended engine


def adopted_intent(observed: dict[str, Any]) -> str:
    engine = observed["engine"]
    if engine == "mlx":
        return f"mlx:{observed['mlx_name']}" if observed["mlx_name"] else UNKNOWN
    return {"comfyui": "comfyui", "strata": "strata", "none": "off"}.get(engine, UNKNOWN)


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
        strata: StrataClient | None = None,
        splash: SplashClient | None = None,
        backends: BackendRegistry | None = None,
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
        self.strata = strata or StrataClient(settings)
        self.splash = splash or SplashClient(settings)
        self.backends = backends or build_registry(settings, mlx, comfy, self.strata, self.splash)
        self.ops = SidecarOpsBinding(settings, self.sidecars)
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
                target = intent_after(job, self.backends)
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
        """Cheap synchronous monitoring snapshot; no HTTP or owning CLI invocation."""
        return self._snapshot()[0]

    def _snapshot(self) -> tuple[dict[str, Any], EngineObservation | None]:
        if not self.mlx.drive_mounted():
            return {"engine": UNKNOWN, "mlx_name": None, "generation": None}, None
        observations = self.backends.observe(include_disabled=True)
        active = resolve_observations(observations)
        selected = next((o for o in observations if o.backend == active), None)
        return {
            "engine": active,
            "mlx_name": selected.reference if selected and active == "mlx" else None,
            "generation": generation_record(selected) if selected else None,
        }, selected

    def exclusive_running(self) -> str | None:
        """Known or ambiguous exclusive ownership blocks sidecar activation."""
        for backend in self.backends:
            if backend.spec.exclusive and backend.observe().running:
                return backend.spec.id
        return None

    def _guard_transition(
        self, plan: Transition, observations: Sequence[EngineObservation], target: EngineTarget
    ) -> Transition:
        """Add generation predicates at execution boundaries using the existing Step runner.

        A newly started generation is captured before readiness polling, so rollback can
        stop only that generation. A failed command that leaves an unrecorded process is
        deliberately refused rather than granting permission to stop it.
        """
        expected = {o.backend: o for o in observations}
        commands: dict[tuple[str, ...], tuple[str, str]] = {}
        engine_executables: set[str] = set()
        callbacks: dict[Callable[[], None], str] = {}
        for backend in self.backends:
            kind = backend.spec.id
            if "queue" in backend.spec.operations:
                for preparation in backend.stop_preparation(None, True):
                    if preparation.call is not None:
                        callbacks[preparation.call] = kind
            references = (
                [EngineTarget(kind, expected[kind].reference)]
                if kind in expected and expected[kind].running
                else []
            )
            if kind == target.kind:
                references.append(target)
            for step in backend.stop_steps():
                if step.argv is not None:
                    commands[tuple(step.argv)] = (kind, "stop")
                    engine_executables.add(step.argv[0])
            for reference in references:
                try:
                    activation_steps = backend.start_steps(reference)
                except ValueError:
                    # A known disabled owner remains stoppable without its inventory.
                    # An actual unmatched activation command is refused below.
                    activation_steps = []
                for step in activation_steps:
                    if step.argv is not None:
                        commands[tuple(step.argv)] = (kind, "start")
                if "switch" in backend.spec.operations and reference.name is not None:
                    for step in backend.switch_steps(reference):
                        if step.argv is not None:
                            commands[tuple(step.argv)] = (kind, "switch")

        def verify(kind: str, action: str) -> None:
            current = self.backends.observe(include_disabled=True)
            if resolve_observations(current) == "unknown":
                raise RuntimeError("engine ownership is unknown at execution")
            for observation in current:
                previous = expected.get(observation.backend)
                if observation.running and (
                    previous is None
                    or not previous.running
                    or generation_record(observation) != generation_record(previous)
                    or observation.reference is not None
                    and observation.reference != previous.reference
                ):
                    raise RuntimeError(f"{observation.backend} generation changed before execution")
            if action != "stop":
                backend = self.backends.get(kind)
                if not backend.spec.enabled:
                    raise RuntimeError(f"the {backend.spec.label} engine is not enabled")
                if any(o.running and (o.backend != kind or action == "start") for o in current):
                    raise RuntimeError("an engine still owns resources before activation")

        def capture(kind: str) -> None:
            observation = self.backends.get(kind).observe()
            if observation.running and observation.ownership != "verified":
                raise RuntimeError(f"{kind} ownership is unknown after activation")
            expected[kind] = observation

        def guarded_callback(kind: str, callback: Callable[[], None]) -> None:
            verify(kind, "stop")
            callback()

        def guard_steps(steps: list[Step], boundary: int = -1) -> tuple[list[Step], int]:
            result: list[Step] = []
            adjusted = 0
            for index, step in enumerate(steps):
                if index == boundary:
                    adjusted = len(result)
                command = commands.get(tuple(step.argv)) if step.argv is not None else None
                if step.argv and step.argv[0] in engine_executables and command is None:
                    raise RuntimeError("engine command has no ownership guard")
                if command:
                    kind, action = command
                    result.append(
                        Step("verify engine ownership", call=partial(verify, kind, action))
                    )
                if step.call is not None and step.call in callbacks:
                    step = replace(
                        step, call=partial(guarded_callback, callbacks[step.call], step.call)
                    )
                result.append(step)
                if command and command[1] != "stop":
                    result.append(
                        Step("record engine generation", call=partial(capture, command[0]))
                    )
            if boundary == len(steps):
                adjusted = len(result)
            return result, adjusted

        steps, boundary = guard_steps(plan.steps, plan.rollback_after)
        rollback, _ = guard_steps(plan.rollback)
        return replace(plan, steps=steps, rollback=rollback, rollback_after=boundary)

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
                observed, selected = self._snapshot()
                intended = (
                    self.backends.get(selected.backend).intent(
                        EngineTarget(selected.backend, selected.reference)
                    )
                    if selected
                    else adopted_intent(observed)
                )
                self._set_intent(intended, "adopted")
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
            if action in ("switch", "start"):
                other = await asyncio.to_thread(self.exclusive_running)
                if other is not None:
                    label = self.backends.get(other).spec.label
                    raise ControlError(
                        409, f"{label} is running; use the Engine control to switch to MLX"
                    )
            if name is not None:
                await self._known_name(name)
            if action in ("switch", "start"):
                target = name or self.mlx.active_env().get("MLX_ACTIVE_NAME")
                await self._check_sidecar_allows(target)
            observations = await asyncio.to_thread(self.backends.observe, include_disabled=True)
            if resolve_observations(observations) == "unknown":
                raise ControlError(409, "engine ownership is unknown")
            command = Step(
                f"mlx-model {action} {name or ''}".strip(),
                argv=build_command(self.settings.mlx_model_path, action, name),
                timeout_s=self.settings.admin_job_timeout_s,
            )
            plan = self._guard_transition(
                Transition([command], [], 0, self.mlx.active_env().get("MLX_ACTIVE_NAME"), name),
                observations,
                EngineTarget("mlx", name),
            )
            job = self._submitted(
                lambda: self.runner.submit_sequence(
                    action,
                    name=name,
                    from_name=plan.from_label,
                    to_name=name,
                    steps=plan.steps,
                    source=source,
                    claim=claim,
                )
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
        claim = self._claim()
        try:
            try:
                target = self.backends.parse(target_raw)
            except BackendDisabled as exc:
                raise ControlError(404, str(exc)) from None
            except ValueError:
                raise ControlError(
                    400,
                    "target must be off, comfyui, strata, strata:<pack>, splash:<profile>, or mlx:<name>",
                ) from None
            observations = await asyncio.to_thread(self.backends.observe, include_disabled=True)
            mlx_running = any(o.backend == "mlx" and o.running for o in observations)
            await asyncio.to_thread(self._preflight, target.kind == "mlx" or mlx_running)
            if target.kind != "off":
                backend = self.backends.get(target.kind)
                try:
                    await asyncio.to_thread(backend.preflight, target)
                except ValueError:
                    raise ControlError(
                        409, "engine target assets or configuration are unavailable"
                    ) from None
                if "multi-model" in backend.spec.operations:
                    targets = await asyncio.to_thread(backend.targets)
                    if target not in targets:
                        raise ControlError(404, "unknown model name")
            observations = await self.backends.observed_details(observations, lifecycle=True)
            queues: dict[str, int | None] = {}
            for observation in observations:
                if (
                    observation.running
                    and "queue" in self.backends.get(observation.backend).spec.operations
                ):
                    queues[observation.backend] = await self.backends.get(
                        observation.backend
                    ).queue_count()
            running_sidecars = await asyncio.to_thread(self.sidecars.running)
            if len(running_sidecars) > 1:
                raise self._conflict(running_sidecars)
            sidecar = running_sidecars[0] if running_sidecars else None
            services = await asyncio.to_thread(self.sidecars.services_running)
            up = {s.name for s in services}
            restore = [
                s
                for s in self.sidecars.services()
                if s.name not in up and sidecars_mod.read_service_intent(self.store, s) == "running"
            ]
            try:
                plan = plan_transition(
                    target,
                    observations=observations,
                    registry=self.backends,
                    queues=queues,
                    force=force,
                    ops=self.ops,
                    sidecar=sidecar,
                    services=services,
                    restore=restore,
                )
            except BackendDisabled as exc:
                raise ControlError(404, str(exc)) from None
            except TransitionRefused as exc:
                raise ControlError(exc.code, exc.message) from None
            plan = self._guard_transition(plan, observations, target)
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
        observations = self.backends.observe(include_disabled=True)
        if resolve_observations(observations) == "unknown":
            raise ControlError(409, "engine ownership is unknown")
        mlx_running = any(o.backend == "mlx" and o.running for o in observations)
        name = next((o.reference for o in observations if o.backend == "mlx" and o.running), None)
        return plan_sidecar_start(
            spec=spec,
            other=other,
            confirm=confirm,
            comfy_running=False,
            exclusive_engines=self.backends.exclusive_labels(observations),
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
        observations = self.backends.observe(include_disabled=True)
        if resolve_observations(observations) == "unknown":
            raise ControlError(409, "engine ownership is unknown")
        mlx_running = any(o.backend == "mlx" and o.running for o in observations)
        name = next((o.reference for o in observations if o.backend == "mlx" and o.running), None)
        slot = self.sidecars.running()
        return plan_service_start(
            spec=spec,
            sidecar=slot[0] if slot else None,
            comfy_running=False,
            exclusive_engines=self.backends.exclusive_labels(observations),
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
