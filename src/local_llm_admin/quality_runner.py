"""M2 quality runs: benchmark or compatibility over one or all registered models.

A run is one held job (``source="benchmark"``): it owns the lifecycle slot from start to
restoration, so manual actions and the watchdog see a busy slot and never act on the
models it switches through. Models are switched with ``mlx-model`` inside that job (no
nested submission). Every HTTP request has connect/read/write timeouts, a per-request
deadline and a run deadline, and cancellation closes the upstream stream.

Restoration runs in ``finally``: back to the original model, or to off plus the original
selection (``mlx-model select``). It is skipped only when someone else changed the
engine during the run (their change is not overwritten) or the admin is shutting down.
Either way, and on a failed restore, the job ends with ``rolled_back=False``, which
sets the intended engine to unknown and suspends automatic recovery (``control.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx
import psutil

from . import compatibility
from .alerts import AlertStore
from .config import Settings
from .control import ControlError, ControlService
from .jobs import JobConflict, JobStorageError
from .mlx import build_command, validate_name
from .quality import (
    BENCHMARK_CASES,
    CASE_VERSION,
    EFFECTIVE_SETTINGS,
    REPEATS,
    REQUEST_TIMEOUT,
    WARMUP,
    Case,
    PeakSampler,
    benchmark_payload,
    build_messages,
    cache_state,
    contaminated,
    measure_stream,
    settings_hash,
)
from .quality_store import QualityStore

log = logging.getLogger("local_llm_admin")
T = TypeVar("T")

KINDS = ("benchmark", "compatibility")
SCOPES = ("current", "all", "model")
EXTERNAL_WARNING = (
    "LiteLLM clients can still send requests to this model during the run. Pause them for"
    " clean measurements; repeats that overlap other requests are excluded."
)
OFF_WARNING = "MLX is off: the run starts MLX and returns it to off with the same selection."
_QUIESCE_S = 15.0


class QualityError(Exception):
    """A refused request; ``code`` is the HTTP status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunCancelled(Exception):
    pass


class RunDeadline(Exception):
    pass


class InterventionRequired(Exception):
    pass


class ModelLoadFailed(Exception):
    pass


@dataclass
class RunPlan:
    run_id: str
    job_id: str
    kind: str
    targets: list[str]
    dirs: dict[str, str]
    original: dict[str, Any]  # {"engine": "mlx"|"none", "name": str}
    deadline: float
    controlled: bool = True
    switched: bool = False
    runtime_fingerprint: str | None = None
    failures: list[str] = field(default_factory=list)


def model_info(path: str | None) -> dict[str, Any]:
    """Weights fingerprint (config.json plus shard names and sizes) and vision evidence."""
    if not path:
        return {"fingerprint": None, "vision": False}
    d = Path(path)
    try:
        raw = (d / "config.json").read_bytes()
    except OSError:
        return {"fingerprint": None, "vision": False}
    h = hashlib.sha256(raw)
    try:
        for f in sorted(d.iterdir()):
            if f.suffix == ".safetensors":
                h.update(f"{f.name}:{f.stat().st_size}".encode())
    except OSError:
        return {"fingerprint": None, "vision": False}
    try:
        vision = "vision_config" in json.loads(raw)
    except ValueError:
        vision = False
    return {"fingerprint": h.hexdigest()[:16], "vision": vision}


class QualityRunner:
    def __init__(
        self,
        settings: Settings,
        control: ControlService,
        store: QualityStore,
        alerts: AlertStore,
        sampler: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cases: tuple[Case, ...] = BENCHMARK_CASES,
        compat_cases: tuple[str, ...] = compatibility.CASES,
    ) -> None:
        self.settings = settings
        self.control = control
        self.store = store
        self.alerts = alerts
        self.sampler = sampler
        self.transport = transport
        self.cases = cases
        self.compat_cases = compat_cases
        self._task: asyncio.Task[None] | None = None
        self._plan: RunPlan | None = None
        self._cancel = asyncio.Event()

    # --- state ---------------------------------------------------------------------
    def active(self) -> dict[str, Any] | None:
        if self._plan is None or self._task is None or self._task.done():
            return None
        return {"run_id": self._plan.run_id, "job_id": self._plan.job_id}

    def reconcile(self) -> list[dict[str, Any]]:
        """Startup: runs a previous process left running are interrupted, not resumed
        (their job row is reconciled by ``ControlService.reconcile``)."""
        return self.store.reconcile_interrupted()

    async def wait(self) -> None:
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # --- start ---------------------------------------------------------------------
    async def start(
        self, kind: str, scope: str, model: str | None = None, *, confirm_off: bool = False
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise QualityError(400, "kind must be benchmark or compatibility")
        if scope not in SCOPES:
            raise QualityError(400, "scope must be current, all or model")
        if (scope == "model") != (model is not None):
            raise QualityError(400, "a model name is required for scope model, and only then")
        if model is not None:
            try:
                validate_name(model)
            except ValueError:
                raise QualityError(400, "invalid model name") from None
        runner = self.control.runner
        try:
            claim = runner.claim()
        except JobConflict:
            raise QualityError(409, self.control.busy_message()) from None
        try:
            plan, warning = await self._plan_run(kind, scope, model, confirm_off)
            try:
                job = runner.begin(
                    "quality",
                    name=model,
                    from_name=plan.original["name"],
                    to_name=plan.original["name"],
                    source="benchmark",
                    claim=claim,
                )
            except JobStorageError:
                raise QualityError(503, "the admin database could not record the job") from None
        finally:
            runner.release(claim)
        plan.job_id = job["id"]
        try:
            self.store.create_run(
                {
                    "id": plan.run_id,
                    "job_id": job["id"],
                    "kind": kind,
                    "scope": scope,
                    "model": model,
                    "started": time.time(),
                    "case_version": CASE_VERSION,
                    "original": plan.original,
                    "settings": EFFECTIVE_SETTINGS if kind == "benchmark" else None,
                    "warning": warning,
                }
            )
        except Exception:
            log.exception("quality run %s: could not be recorded", plan.run_id)
            runner.finish(job["id"], "failed", rolled_back=True)  # nothing changed yet
            raise QualityError(503, "the admin database could not record the run") from None
        self._plan = plan
        self._cancel = asyncio.Event()
        self._task = asyncio.create_task(self._execute(plan))
        log.info("quality run %s: %s %s %s", plan.run_id, kind, scope, model or "")
        run = self.store.get_run(plan.run_id)
        assert run is not None
        return run

    async def _plan_run(
        self, kind: str, scope: str, model: str | None, confirm_off: bool
    ) -> tuple[RunPlan, str]:
        try:
            await asyncio.to_thread(self.control.preflight)
        except ControlError as exc:
            raise QualityError(exc.code, exc.message) from None
        observed = await asyncio.to_thread(self.control.observe)
        engine = observed["engine"]
        sidecar = await asyncio.to_thread(self.control.sidecar_running)
        if sidecar is not None:
            raise QualityError(
                409,
                f"the {sidecar.label} sidecar is running and shares the GPU; stop it first so"
                " the measurements stay comparable",
            )
        services = await asyncio.to_thread(self.control.sidecars.services_running)
        if services:
            raise QualityError(
                409,
                f"{services[0].noun} is running and shares the GPU; stop it first so the"
                " measurements stay comparable",
            )
        if engine not in ("mlx", "none"):
            raise QualityError(
                409, f"engine is {engine}; quality runs need MLX running or everything off"
            )
        registered = await asyncio.to_thread(self.control.mlx.list_models)
        dirs = {m["name"]: m["dir"] for m in registered}
        if engine == "mlx":
            original = observed["mlx_name"]
        else:
            original = self.control.mlx.active_env().get("MLX_ACTIVE_NAME")
        if not original or original not in dirs:
            raise QualityError(409, "the selected model is unknown; switch to a registered model")
        if model is not None and model not in dirs:
            raise QualityError(404, "unknown model name")
        if engine == "none" and not confirm_off:
            raise QualityError(409, f"{OFF_WARNING} Please confirm to continue.")
        if scope == "current":
            targets = [original]
        elif scope == "model":
            assert model is not None
            targets = [model]
        else:  # the loaded model first saves one switch
            targets = [original] + [n for n in dirs if n != original]
        warning = EXTERNAL_WARNING
        if engine == "none":
            warning = f"{OFF_WARNING} {EXTERNAL_WARNING}"
        plan = RunPlan(
            run_id=uuid.uuid4().hex[:12],
            job_id="",
            kind=kind,
            targets=targets,
            dirs=dirs,
            original={"engine": engine, "name": original},
            deadline=time.monotonic() + self.settings.quality_run_deadline_s,
        )
        return plan, warning

    # --- cancellation --------------------------------------------------------------
    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise QualityError(404, "unknown quality run")
        active = self.active()
        if active is None or active["run_id"] != run_id:
            raise QualityError(409, "the run is not in progress")
        self._cancel.set()
        log.info("quality run %s: cancel requested", run_id)
        return run

    async def shutdown(self) -> None:
        """App shutdown: stop without restoring (a restore could outlive the process)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _check(self, plan: RunPlan) -> None:
        if self._cancel.is_set():
            raise RunCancelled()
        if time.monotonic() >= plan.deadline:
            raise RunDeadline()

    async def _cancellable(self, coro: Awaitable[T]) -> T:
        """Await ``coro`` unless cancel is requested first; then cancel it (closing any
        upstream stream) and raise RunCancelled."""
        task = asyncio.ensure_future(coro)
        waiter = asyncio.create_task(self._cancel.wait())
        try:
            done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        finally:
            waiter.cancel()
        if task not in done:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise RunCancelled()
        return task.result()

    def _request_deadline(self, plan: RunPlan) -> float:
        return max(
            0.01, min(self.settings.quality_request_timeout_s, plan.deadline - time.monotonic())
        )

    # --- helpers -------------------------------------------------------------------
    def _rss(self) -> int | None:
        pid = self.control.mlx.pid_from_file()
        if pid is None:
            return None
        try:
            return int(psutil.Process(pid).memory_info().rss)
        except (psutil.Error, ValueError):
            return None

    async def _get(self, http: httpx.AsyncClient, path: str) -> Any:
        try:
            async with asyncio.timeout(10):
                resp = await http.get(path)
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, TimeoutError, ValueError):
            return None

    async def _metrics(self, http: httpx.AsyncClient) -> dict[str, Any] | None:
        doc = await self._get(http, "/metrics")
        summary = doc.get("summary") if isinstance(doc, dict) else None
        return summary if isinstance(summary, dict) else None

    async def _serving(self, http: httpx.AsyncClient) -> bool:
        doc = await self._get(http, "/health")
        return isinstance(doc, dict) and doc.get("status") == "healthy"

    async def _observe(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.control.observe)

    def _progress(self, plan: RunPlan, **progress: Any) -> None:
        self.store.update_run(plan.run_id, progress=progress)

    async def _run_cli(self, act: str, name: str | None, label: str | None = None) -> str:
        """One ``mlx-model`` command inside the held job, recorded in switch history."""
        plan = self._plan
        assert plan is not None
        before = await self._observe()
        argv = build_command(self.settings.mlx_model_path, act, name)
        t0 = time.monotonic()
        status, _rc = await asyncio.to_thread(
            self.control.runner.run_command,
            plan.job_id,
            label or f"mlx-model {act} {name or ''}".strip(),
            argv,
            self.settings.admin_job_timeout_s,
        )
        plan.switched = True
        self.control.store.add_history(
            ts=time.time(),
            action=act,
            from_name=before["mlx_name"] if before["engine"] == "mlx" else None,
            to_name=name,
            result=status,
            duration_s=round(time.monotonic() - t0, 3),
            output_tail="",
            source="benchmark",
        )
        return status

    async def _wait_serving(self, http: httpx.AsyncClient) -> bool:
        end = time.monotonic() + self.settings.quality_ready_timeout_s
        while True:
            if await self._serving(http):
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.5)

    async def _owned(self, name: str, generation: Any) -> None:
        obs = await self._observe()
        if obs["engine"] != "mlx" or obs["mlx_name"] != name or obs["generation"] != generation:
            raise InterventionRequired(
                f"the engine changed outside the run (expected MLX {name}; observed"
                f" {obs['engine']} {obs['mlx_name'] or '-'})"
            )

    # --- the run ---------------------------------------------------------------------
    async def _execute(self, plan: RunPlan) -> None:
        status, error = "failed", None
        shutdown = False
        async with httpx.AsyncClient(
            base_url=self.settings.mlx_api_base, timeout=REQUEST_TIMEOUT, transport=self.transport
        ) as http:
            try:
                version = await self._get(http, "/openapi.json")
                if isinstance(version, dict):
                    self.store.update_run(
                        plan.run_id, runtime_version=(version.get("info") or {}).get("version")
                    )
                for index, name in enumerate(plan.targets):
                    self._check(plan)
                    self._progress(
                        plan, model=name, index=index, total=len(plan.targets), step="load"
                    )
                    await self._model(http, plan, name, index)
                status = "failed" if plan.failures else "succeeded"
                error = "; ".join(plan.failures) or None
            except RunCancelled:
                status = "cancelled"
                await self._quiesce(http)
            except RunDeadline:
                error = "run deadline reached"
            except InterventionRequired as exc:
                status, error = "intervention_required", str(exc)
            except asyncio.CancelledError:
                shutdown = True
                status, error = "interrupted", "the admin shut down during the run"
            except Exception as exc:  # the run must still restore and release the slot
                log.exception("quality run %s failed", plan.run_id)
                error = f"internal error: {exc}"
            if status == "intervention_required" or shutdown:
                restoration = await self._not_restored(error or "")
            else:
                restoration = await self._restore(http, plan)
        await self._conclude(plan, status, error, restoration, shutdown)
        if shutdown:
            raise asyncio.CancelledError()

    async def _not_restored(self, why: str) -> dict[str, Any]:
        try:
            observed = await self._observe()
        except Exception:
            observed = None
        return {"outcome": "not_attempted", "detail": why, "observed": observed}

    async def _quiesce(self, http: httpx.AsyncClient) -> None:
        """After cancelling, wait (bounded) until the server reports nothing in flight."""
        end = time.monotonic() + _QUIESCE_S
        while time.monotonic() < end:
            m = await self._metrics(http)
            if m is None or not m.get("in_flight"):
                return
            await asyncio.sleep(0.2)

    async def _conclude(
        self,
        plan: RunPlan,
        status: str,
        error: str | None,
        restoration: dict[str, Any],
        shutdown: bool,
    ) -> None:
        restored = restoration["outcome"] in ("restored", "not_needed")
        now = time.time()
        try:
            self.store.update_run(
                plan.run_id,
                status=status,
                finished=now,
                error=error,
                restoration=restoration,
                controlled=plan.controlled,
                progress=None,
            )
        except Exception:
            log.exception("quality run %s: recording the outcome failed", plan.run_id)
        job_status = {"succeeded": "succeeded", "cancelled": "cancelled"}.get(status, "failed")
        if shutdown:
            job_status = "interrupted"
        self.control.runner.note(
            plan.job_id, f"quality run {status}; restoration {restoration['outcome']}"
        )
        self.control.runner.finish(plan.job_id, job_status, rolled_back=restored)
        if status == "intervention_required":
            self.alerts.event(
                "quality_intervention",
                "warning",
                "Quality run stopped: the engine changed outside the run",
                f"Run {plan.run_id}: {error}. The run did not restore anything; automatic"
                " recovery is suspended until you check the engine and resume.",
                now,
                dedup=plan.run_id,
            )
        elif not restored and not shutdown:
            observed = restoration.get("observed") or {}
            self.alerts.event(
                "quality_restore_failed",
                "critical",
                "Quality run did not restore the engine",
                f"Run {plan.run_id} should return to {plan.original['engine']}"
                f" {plan.original['name']}; observed {observed.get('engine')}"
                f" {observed.get('mlx_name') or ''}. {restoration.get('detail') or ''}".strip(),
                now,
                dedup=plan.run_id,
            )
        with contextlib.suppress(Exception):
            self.store.prune(now, self.settings.quality_retention_days)
        log.info("quality run %s: %s (restoration %s)", plan.run_id, status, restoration["outcome"])

    async def _serve(self, http: httpx.AsyncClient, name: str) -> tuple[float | None, Any]:
        obs = await self._observe()
        if obs["engine"] == "mlx" and obs["mlx_name"] == name and await self._serving(http):
            return None, obs["generation"]
        act = "switch" if obs["engine"] == "mlx" else "start"
        t0 = time.monotonic()
        status = await self._run_cli(act, name)
        load_s = round(time.monotonic() - t0, 3)
        if status != "succeeded":
            raise ModelLoadFailed(f"mlx-model {act} {name}: {status}")
        if not await self._wait_serving(http):
            raise ModelLoadFailed(f"{name} did not become healthy")
        obs = await self._observe()
        if obs["engine"] != "mlx" or obs["mlx_name"] != name:
            raise ModelLoadFailed(f"{name} is not the serving model after {act}")
        return load_s, obs["generation"]

    async def _model(self, http: httpx.AsyncClient, plan: RunPlan, name: str, index: int) -> None:
        path = plan.dirs.get(name)
        try:
            load_s, generation = await self._serve(http, name)
        except ModelLoadFailed as exc:
            case_id = self.store.add_case(
                plan.run_id,
                model=name,
                model_path=path,
                fingerprint=None,
                case_key="load",
                kind=plan.kind,
                settings_hash=None,
                cache_state=None,
                load_s=None,
            )
            if plan.kind == "benchmark":
                self.store.fail_case(case_id, "failed", str(exc))
            else:
                self.store.set_compatibility(
                    case_id, "unknown", str(exc), {"error_kind": "model_load"}
                )
            plan.failures.append(str(exc))
            self._check(plan)  # a cancel during the switch wins over moving on
            return
        self._check(plan)
        info = await asyncio.to_thread(model_info, path)
        if plan.kind == "benchmark":
            await self._benchmark(http, plan, name, path, info, load_s, generation, index)
        else:
            await self._compatibility(http, plan, name, path, info, load_s, generation, index)

    async def _case_guard(self, plan: RunPlan, case_id: int, coro: Awaitable[None]) -> None:
        """Close a case row that an abort interrupts, then re-raise."""
        try:
            await coro
        except RunCancelled:
            self.store.fail_case(case_id, "cancelled", "cancelled")
            raise
        except RunDeadline:
            self.store.fail_case(case_id, "failed", "run deadline reached")
            raise
        except InterventionRequired as exc:
            self.store.fail_case(case_id, "failed", str(exc))
            raise
        except asyncio.CancelledError:
            self.store.fail_case(case_id, "cancelled", "admin shut down")
            raise

    def _profile_fingerprint(self) -> str | None:
        """Fingerprint of the launch flags mlx-model started the server with, as it
        recorded them in run/active.env; None for a server started before profiles."""
        return self.control.mlx.active_env().get("MLX_PROFILE_FINGERPRINT")

    async def _benchmark(
        self,
        http: httpx.AsyncClient,
        plan: RunPlan,
        name: str,
        path: str | None,
        info: dict[str, Any],
        load_s: float | None,
        generation: Any,
        index: int,
    ) -> None:
        settings_doc = await self._get(http, "/v1/settings")
        fp = settings_doc.get("fingerprint") if isinstance(settings_doc, dict) else None
        if plan.runtime_fingerprint is None and fp:
            plan.runtime_fingerprint = fp
            self.store.update_run(plan.run_id, runtime_fingerprint=fp)
        cache = await self._get(http, "/v1/cache/stats")
        cache_enabled = cache.get("enabled") if isinstance(cache, dict) else None
        # the launch profile the runner's own start just applied (M3c)
        profile = self._profile_fingerprint()
        s_hash = settings_hash(EFFECTIVE_SETTINGS, fp, profile)
        for case in self.cases:
            self._check(plan)
            await self._owned(name, generation)
            case_id = self.store.add_case(
                plan.run_id,
                model=name,
                model_path=path,
                fingerprint=info["fingerprint"],
                case_key=case.key,
                kind="benchmark",
                settings_hash=s_hash,
                cache_state=None,
                load_s=load_s,
                profile_fingerprint=profile,
            )
            ok = await self._benchmark_case_guarded(
                http, plan, name, case, case_id, cache_enabled, generation, index
            )
            if not ok:
                return  # the server went away; later cases would fail the same way

    async def _benchmark_case_guarded(
        self,
        http: httpx.AsyncClient,
        plan: RunPlan,
        name: str,
        case: Case,
        case_id: int,
        cache_enabled: bool | None,
        generation: Any,
        index: int,
    ) -> bool:
        result: list[bool] = []

        async def body() -> None:
            result.append(
                await self._benchmark_case(
                    http, plan, name, case, case_id, cache_enabled, generation, index
                )
            )

        await self._case_guard(plan, case_id, body())
        return result[0]

    async def _benchmark_case(
        self,
        http: httpx.AsyncClient,
        plan: RunPlan,
        name: str,
        case: Case,
        case_id: int,
        cache_enabled: bool | None,
        generation: Any,
        index: int,
    ) -> bool:
        labels: list[str] = []
        interval = self.settings.quality_rss_interval_s
        for rep in range(WARMUP + REPEATS):
            self._check(plan)
            await self._owned(name, generation)
            self._progress(
                plan, model=name, index=index, total=len(plan.targets), step=case.key, rep=rep
            )
            before = await self._metrics(http)
            payload = benchmark_payload(build_messages(case, rep))
            sys_before = self.sampler.current()
            peak = PeakSampler(self._rss, interval)
            async with peak:
                m = await self._cancellable(
                    measure_stream(http, payload, deadline_s=self._request_deadline(plan))
                )
            m.peak_rss_bytes = peak.peak
            after = await self._metrics(http)
            sys_after = self.sampler.current()
            dirty = contaminated(before, after)
            if dirty is None:
                plan.controlled = False
            data = m.as_dict() | {
                "rss_interval_s": interval,
                "system": {
                    "pressure_before": sys_before.get("pressure"),
                    "pressure_after": sys_after.get("pressure"),
                    "swap_used_after": sys_after.get("swap_used"),
                },
            }
            warmup = rep < WARMUP
            self.store.add_measurement(
                case_id, rep, warmup=warmup, contaminated=dirty, error=m.error, data=data
            )
            if m.error_kind == "transport":
                self.store.fail_case(case_id, "failed", m.error)
                plan.failures.append(f"{name} {case.key}: {m.error}")
                return False
            if m.error is None and not warmup:
                labels.append(cache_state(cache_enabled, m.cached_tokens))
        label = labels[0] if labels and len(set(labels)) == 1 else "unknown"
        self.store.set_cache_state(case_id, label)
        self.store.complete_benchmark_case(case_id, run_controlled=plan.controlled)
        return True

    async def _compatibility(
        self,
        http: httpx.AsyncClient,
        plan: RunPlan,
        name: str,
        path: str | None,
        info: dict[str, Any],
        load_s: float | None,
        generation: Any,
        index: int,
    ) -> None:
        health = await self._get(http, "/health")
        probe_info = {
            "tool_parser": health.get("loaded_tool_parser") if isinstance(health, dict) else None,
            "vision": info["vision"],
        }
        profile = self._profile_fingerprint()
        for key in self.compat_cases:
            self._check(plan)
            await self._owned(name, generation)
            self._progress(plan, model=name, index=index, total=len(plan.targets), step=key)
            case_id = self.store.add_case(
                plan.run_id,
                model=name,
                model_path=path,
                fingerprint=info["fingerprint"],
                case_key=key,
                kind="compatibility",
                settings_hash=None,
                cache_state=None,
                load_s=load_s,
                profile_fingerprint=profile,
            )

            async def body(k: str = key, cid: int = case_id) -> None:
                r = await self._cancellable(
                    compatibility.run_case(
                        http, k, probe_info, deadline_s=self._request_deadline(plan)
                    )
                )
                self.store.set_compatibility(cid, r.outcome, r.detail, {"error_kind": r.error_kind})

            await self._case_guard(plan, case_id, body())

    # --- restoration -------------------------------------------------------------------
    async def _restore(self, http: httpx.AsyncClient, plan: RunPlan) -> dict[str, Any]:
        want = plan.original
        detail = ""
        try:
            obs = await self._observe()
            if want["engine"] == "mlx":
                if obs["engine"] == "mlx" and obs["mlx_name"] == want["name"]:
                    ok = True
                elif obs["engine"] in ("mlx", "none"):
                    act = "switch" if obs["engine"] == "mlx" else "start"
                    status = await self._run_cli(
                        act, want["name"], f"restore: mlx-model {act} {want['name']}"
                    )
                    ok = status == "succeeded" and await self._wait_serving(http)
                    detail = "" if ok else f"mlx-model {act} {want['name']}: {status}"
                else:
                    ok, detail = False, f"engine is {obs['engine']}"
            else:
                ok = True
                if obs["engine"] == "mlx":
                    status = await self._run_cli("stop", None, "restore: mlx-model stop")
                    ok = status == "succeeded"
                    detail = "" if ok else f"mlx-model stop: {status}"
                elif obs["engine"] != "none":
                    ok, detail = False, f"engine is {obs['engine']}"
                if ok and self.control.mlx.active_env().get("MLX_ACTIVE_NAME") != want["name"]:
                    status = await self._run_cli(
                        "select", want["name"], f"restore: mlx-model select {want['name']}"
                    )
                    ok = status == "succeeded"
                    detail = "" if ok else f"mlx-model select {want['name']}: {status}"
            final = await self._observe()
            if ok:
                expected = "mlx" if want["engine"] == "mlx" else "none"
                selected = (
                    final["mlx_name"]
                    if expected == "mlx"
                    else self.control.mlx.active_env().get("MLX_ACTIVE_NAME")
                )
                if final["engine"] != expected or selected != want["name"]:
                    ok = False
                    detail = f"observed {final['engine']} {selected or '-'} after restoring"
        except Exception as exc:
            log.exception("quality run %s: restore failed", plan.run_id)
            ok, detail, final = False, f"restore error: {exc}", None
        if ok:
            outcome = "restored" if plan.switched else "not_needed"
        else:
            outcome = "failed"
        return {"outcome": outcome, "detail": detail, "observed": final}
