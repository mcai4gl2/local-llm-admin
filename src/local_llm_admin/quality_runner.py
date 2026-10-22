"""M2 quality runs: benchmark, compatibility or (M10) an eval suite over one or all
registered models.

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
from .chat import ACTIVE_MODEL_ID
from .config import Settings
from .control import ControlError, ControlService
from .eval_graders import final_answer, grade_response
from .eval_judge import MAX_TOKENS as JUDGE_MAX_TOKENS
from .eval_judge import JudgeTarget, build_judge_messages, judge_one, judge_target
from .eval_store import EvalStore
from .eval_suites import EvalCase, Judge, Suite, expand, messages_for
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

KINDS = ("benchmark", "compatibility", "eval")
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
    original: dict[str, Any]  # {"engine": "mlx"|"none"|"strata", "name": str}
    deadline: float
    controlled: bool = True
    switched: bool = False
    runtime_fingerprint: str | None = None
    failures: list[str] = field(default_factory=list)
    suite: Suite | None = None
    thinking: bool = False
    eval_cases: dict[str, int] = field(default_factory=dict)  # model -> its eval case row


def eval_request_timeout(base_s: float, max_tokens: int) -> float:
    """A non-streamed eval answer arrives whole: allow at least 5 tokens/s for it."""
    return max(base_s, max_tokens / 5)


def eval_payload(
    case: EvalCase, *, thinking: bool, engine: str, max_tokens_thinking: int
) -> dict[str, Any]:
    """One non-streamed eval request. ``engine`` is ``"mlx"`` (MLX's full active path is
    sent) or the model id another engine serves. Thinking is set both ways the servers
    read it."""
    payload: dict[str, Any] = {
        "model": ACTIVE_MODEL_ID if engine == "mlx" else engine,
        "messages": messages_for(case),
        "stream": False,
        "temperature": 0,
        "seed": 0,
        "max_tokens": max_tokens_thinking if thinking else case.max_tokens,
        "enable_thinking": thinking,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    if case.tools:
        payload["tools"] = case.tools
    return payload


def health_context(health: Any, strata: bool) -> int | None:
    """Context size from a /health body: ``max_context`` for Strata, ``loaded_context_size``
    for MLX; None when absent or unusable."""
    if not isinstance(health, dict):
        return None
    raw = health.get("max_context" if strata else "loaded_context_size")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None


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
        evals: EvalStore | None = None,
        judge_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.control = control
        self.store = store
        self.alerts = alerts
        self.sampler = sampler
        self.transport = transport
        self.cases = cases
        self.compat_cases = compat_cases
        self.evals = evals
        self.judge_transport = judge_transport
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
    @staticmethod
    def _check_scope(scope: str, model: str | None) -> None:
        if scope not in SCOPES:
            raise QualityError(400, "scope must be current, all or model")
        if (scope == "model") != (model is not None):
            raise QualityError(400, "a model name is required for scope model, and only then")
        if model is not None:
            try:
                validate_name(model)
            except ValueError:
                raise QualityError(400, "invalid model name") from None

    async def plan_targets(self, scope: str, model: str | None) -> RunPlan:
        """The models a run of this scope would visit, in order (for an estimate); the
        plan is never started."""
        self._check_scope(scope, model)
        plan, _ = await self._plan_run("eval", scope, model, True)
        return plan

    async def start(
        self,
        kind: str,
        scope: str,
        model: str | None = None,
        *,
        confirm_off: bool = False,
        suite: str | None = None,
        thinking: bool = False,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise QualityError(400, "kind must be benchmark, compatibility or eval")
        self._check_scope(scope, model)
        loaded = self._suite(kind, suite, thinking)
        runner = self.control.runner
        try:
            claim = runner.claim()
        except JobConflict:
            raise QualityError(409, self.control.busy_message()) from None
        try:
            plan, warning = await self._plan_run(kind, scope, model, confirm_off)
            plan.suite, plan.thinking = loaded, thinking
            self._check_judge(plan)
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
                    "settings": self._run_settings(plan),
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

    def _suite(self, kind: str, suite: str | None, thinking: bool) -> Suite | None:
        if kind != "eval":
            if suite is not None or thinking:
                raise QualityError(400, "suite and thinking apply to eval runs only")
            return None
        if suite is None:
            raise QualityError(400, "an eval run needs a suite")
        if self.evals is None:
            raise QualityError(503, "evals are not available")
        loaded = self.evals.suite(suite)
        if loaded is None:
            raise QualityError(404, "unknown eval suite")
        return loaded

    def _check_judge(self, plan: RunPlan) -> None:
        """A suite with judge cases needs a usable judge setting; a local judge must be a
        registered model, and never on Strata (a run never switches it)."""
        if plan.suite is None or not any(isinstance(c.grader, Judge) for c in plan.suite.cases):
            return
        try:
            target = judge_target(self.settings)
        except ValueError as exc:
            raise QualityError(409, f"invalid judge setting: {exc}") from None
        if target is None or target.mlx_name is None:
            return
        if plan.original["engine"] == "strata":
            raise QualityError(
                409, "Strata is running and is never switched; use an external judge or none"
            )
        if target.mlx_name not in plan.dirs:
            raise QualityError(409, f"the judge model {target.mlx_name} is not registered")

    def _run_settings(self, plan: RunPlan) -> dict[str, Any] | None:
        if plan.kind == "benchmark":
            return EFFECTIVE_SETTINGS
        if plan.suite is None:
            return None
        return {
            "suite_id": plan.suite.id,
            "suite_version": plan.suite.version,
            "suite_hash": plan.suite.content_hash(),
            "thinking": plan.thinking,
        }

    def _deadline(self, kind: str) -> float:
        s = self.settings
        return time.monotonic() + (
            s.eval_run_deadline_s if kind == "eval" else s.quality_run_deadline_s
        )

    async def _plan_run(
        self, kind: str, scope: str, model: str | None, confirm_off: bool
    ) -> tuple[RunPlan, str]:
        try:
            await asyncio.to_thread(self.control.preflight)
        except ControlError as exc:
            raise QualityError(exc.code, exc.message) from None
        observed = await asyncio.to_thread(self.control.observe)
        engine = observed["engine"]
        if engine == "strata":
            return self._plan_strata(kind, scope)
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
            deadline=self._deadline(kind),
        )
        return plan, warning

    def _plan_strata(self, kind: str, scope: str) -> tuple[RunPlan, str]:
        """Strata serves one model and is never switched by a run: only the compatibility
        cases or an eval of the running engine (Strata Phase 4, M10). Sidecars cannot run
        beside it."""
        if kind not in ("compatibility", "eval") or scope != "current":
            raise QualityError(
                409,
                "Strata is running; only compatibility checks and evals of the current engine"
                " run on it",
            )
        plan = RunPlan(
            run_id=uuid.uuid4().hex[:12],
            job_id="",
            kind=kind,
            targets=["strata"],
            dirs={},
            original={"engine": "strata", "name": "strata"},
            deadline=self._deadline(kind),
        )
        return plan, EXTERNAL_WARNING

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

    def _eval_deadline(self, plan: RunPlan, max_tokens: int) -> float:
        budget = eval_request_timeout(self.settings.quality_request_timeout_s, max_tokens)
        return max(0.01, min(budget, plan.deadline - time.monotonic()))

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
        if name == "strata":
            if obs["engine"] != "strata" or obs["generation"] != generation:
                raise InterventionRequired(
                    f"the engine changed outside the run (expected Strata; observed {obs['engine']})"
                )
            return
        if obs["engine"] != "mlx" or obs["mlx_name"] != name or obs["generation"] != generation:
            raise InterventionRequired(
                f"the engine changed outside the run (expected MLX {name}; observed"
                f" {obs['engine']} {obs['mlx_name'] or '-'})"
            )

    def _open(self, strata: bool) -> httpx.AsyncClient:
        key = self.settings.strata_api_key.get_secret_value() if strata else ""
        return httpx.AsyncClient(
            base_url=self.settings.strata_api_base if strata else self.settings.mlx_api_base,
            timeout=REQUEST_TIMEOUT,
            transport=self.transport,
            headers={"Authorization": f"Bearer {key}"} if key else None,
        )

    async def current_context(self, plan: RunPlan) -> int | None:
        """The running engine's context size (for estimating a needle suite), or None."""
        strata = plan.original["engine"] == "strata"
        async with self._open(strata) as http:
            return health_context(await self._get(http, "/health"), strata)

    # --- the run ---------------------------------------------------------------------
    async def _execute(self, plan: RunPlan) -> None:
        status, error = "failed", None
        shutdown = False
        strata = plan.original["engine"] == "strata"
        async with self._open(strata) as http:
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
                await self._judge_phase(http, plan)
                await self._after_models(plan)
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
        if self.evals is not None:
            with contextlib.suppress(Exception):  # after the runs it orphans are gone
                self.evals.prune(now, self.settings.quality_retention_days)
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

    async def _strata_ready(self, http: httpx.AsyncClient) -> tuple[float | None, Any]:
        doc = await self._get(http, "/health")
        if not (isinstance(doc, dict) and doc.get("service") == "strata" and doc.get("loaded")):
            raise ModelLoadFailed("Strata is not loaded")
        return None, (await self._observe())["generation"]

    async def _model(self, http: httpx.AsyncClient, plan: RunPlan, name: str, index: int) -> None:
        path = plan.dirs.get(name)
        try:
            if plan.original["engine"] == "strata":
                load_s, generation = await self._strata_ready(http)
            else:
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
            if plan.kind in ("benchmark", "eval"):
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
        elif plan.kind == "eval":
            await self._eval(http, plan, name, path, info, load_s, generation, index)
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
        h: dict[str, Any] = health if isinstance(health, dict) else {}
        if plan.original["engine"] == "strata":
            # Strata parses tool calls itself and says whether it reads images
            probe_info = {
                "tool_parser": "strata",
                "vision": h.get("images") is True,
                "model_id": h.get("model"),
                "engine": "strata",
            }
        else:
            probe_info = {"tool_parser": h.get("loaded_tool_parser"), "vision": info["vision"]}
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

    async def _eval(
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
        """One ``eval:<suite>`` case row per model, one result row per eval case. A case
        that fails (HTTP error, timeout, bad body) is recorded as ``failed`` and the run
        moves on; aborts close the row through ``_case_guard``."""
        suite, evals = plan.suite, self.evals
        assert suite is not None and evals is not None
        settings_doc = await self._get(http, "/v1/settings")
        fp = settings_doc.get("fingerprint") if isinstance(settings_doc, dict) else None
        if plan.runtime_fingerprint is None and fp:
            plan.runtime_fingerprint = fp
            self.store.update_run(plan.run_id, runtime_fingerprint=fp)
        engine, fingerprint = "mlx", info["fingerprint"]
        strata = plan.original["engine"] == "strata"
        health: Any = None
        if strata or suite.needle is not None:
            health = await self._get(http, "/health")
        if strata:  # no weights path: key it by the served id
            served = health.get("model") if isinstance(health, dict) else None
            if isinstance(served, str) and served:
                engine = served
                fingerprint = "strata-" + hashlib.sha256(served.encode()).hexdigest()[:16]
        thinking_max = self.settings.eval_thinking_max_tokens
        effective = {
            "temperature": 0,
            "seed": 0,
            "thinking": plan.thinking,
            "max_tokens_thinking": thinking_max if plan.thinking else None,
        }
        profile = self._profile_fingerprint()
        s_hash = settings_hash(effective, fp, profile)
        suite_hash = suite.content_hash()
        # a needle suite skips lengths that do not fit the loaded context
        context = health_context(health, strata)
        cases = await asyncio.to_thread(expand, suite, context)
        self._check(plan)
        await self._owned(name, generation)
        case_id = self.store.add_case(
            plan.run_id,
            model=name,
            model_path=path,
            fingerprint=fingerprint,
            case_key=f"eval:{suite.id}",
            kind="eval",
            settings_hash=s_hash,
            cache_state=None,
            load_s=load_s,
            profile_fingerprint=profile,
        )
        plan.eval_cases[name] = case_id

        async def body() -> None:
            failed = 0
            try:
                for case in cases:
                    self._check(plan)
                    await self._owned(name, generation)
                    self._progress(
                        plan,
                        model=name,
                        index=index,
                        total=len(plan.targets),
                        step=f"{suite.id}:{case.id}",
                    )
                    payload = eval_payload(
                        case,
                        thinking=plan.thinking,
                        engine=engine,
                        max_tokens_thinking=thinking_max,
                    )
                    fields = await self._cancellable(self._eval_case(http, plan, case, payload))
                    failed += fields["grade_state"] == "failed"
                    evals.add_result(
                        run_id=plan.run_id,
                        quality_case_id=case_id,
                        model=name,
                        fingerprint=fingerprint,
                        settings_hash=s_hash,
                        suite_id=suite.id,
                        suite_hash=suite_hash,
                        case_id=case.id,
                        category=case.category,
                        thinking=int(plan.thinking),
                        **fields,
                    )
            except InterventionRequired:
                # the answers so far stay as rows, but an intervened run never becomes latest
                self._finalize_eval(plan, name, None, promote=False)
                raise
            self._check(plan)  # a deadline or cancel during the last request wins
            if failed:
                plan.failures.append(f"{name} {suite.id}: {failed} of {len(cases)} cases failed")
            # scored now, promoted only once every model finished cleanly (_after_models)
            self._finalize_eval(plan, name, case_id, promote=False)

        await self._case_guard(plan, case_id, body())

    async def _judge_phase(self, http: httpx.AsyncClient, plan: RunPlan) -> None:
        """After every candidate answered (one switch to a local judge, not one per
        candidate): score the ``needs_judge`` rows, then re-score each model. With no judge
        they become ``ungraded``; a judge that gives no verdict leaves ``judge_error``
        (excluded and counted, never 0). Aborts propagate like during the candidates, so
        nothing is promoted; restoration undoes a judge switch."""
        evals, suite = self.evals, plan.suite
        if plan.kind != "eval" or evals is None or suite is None:
            return
        rows = evals.needs_judge(plan.run_id)
        if not rows:
            return
        target = judge_target(self.settings)  # checked at start
        if target is None:
            for r in rows:
                evals.set_judged(r["id"], score=None, passed=None, detail="no judge configured",
                                 judge=None, self_judged=False, state="ungraded")  # fmt: skip
        elif target.kind == "external":
            async with httpx.AsyncClient(
                base_url=target.base_url,
                timeout=REQUEST_TIMEOUT,
                transport=self.judge_transport,
                headers={"Authorization": f"Bearer {target.api_key}"} if target.api_key else None,
            ) as judge_http:
                await self._judge_rows(judge_http, plan, target, rows, None)
        else:
            assert target.mlx_name is not None
            self._check(plan)
            self._progress(plan, model=target.mlx_name, step="judge:load")
            try:
                _, generation = await self._serve(http, target.mlx_name)
            except ModelLoadFailed as exc:
                self._check(plan)
                for r in rows:
                    evals.set_judged(r["id"], score=None, passed=None, detail=f"judge: {exc}",
                                     judge=target.label, self_judged=False,
                                     state="judge_error")  # fmt: skip
                plan.failures.append(f"judge: {exc}")
            else:
                await self._judge_rows(http, plan, target, rows, generation)
        for name, case_id in plan.eval_cases.items():
            self._finalize_eval(plan, name, case_id, promote=False)

    async def _judge_rows(
        self,
        http: httpx.AsyncClient,
        plan: RunPlan,
        target: JudgeTarget,
        rows: list[dict[str, Any]],
        generation: Any,
    ) -> None:
        evals, suite = self.evals, plan.suite
        assert evals is not None and suite is not None
        cases = {c.id: c for c in suite.cases}
        errors = 0
        for n, r in enumerate(rows):
            self._check(plan)
            if target.mlx_name is not None:
                await self._owned(target.mlx_name, generation)
            self._progress(plan, model=r["model"], step=f"judge:{r['case_id']}", index=n,
                           total=len(rows))  # fmt: skip
            case = cases[r["case_id"]]
            spec = case.grader
            assert isinstance(spec, Judge)
            answer, _ = final_answer(r["response"], r["finish_reason"], r["reasoning_chars"] or 0,
                                     thinking=bool(r["thinking"]))  # fmt: skip
            messages = build_judge_messages(case, answer or "", spec)
            verdict = await self._cancellable(
                judge_one(http, target, messages, scale=spec.scale, until=plan.deadline,
                          deadline_s=self._eval_deadline(plan, JUDGE_MAX_TOKENS))
            )  # fmt: skip
            self._check(plan)  # a deadline or cancel during the request wins
            self_judged = target.mlx_name is not None and r["model"] == target.mlx_name
            if verdict is None:
                errors += 1
                evals.set_judged(r["id"], score=None, passed=None,
                                 detail="the judge gave no usable verdict", judge=target.label,
                                 self_judged=self_judged, state="judge_error")  # fmt: skip
                continue
            score, reason = verdict
            evals.set_judged(r["id"], score=score, passed=score >= 0.5, detail=reason,
                             judge=target.label, self_judged=self_judged,
                             state="graded")  # fmt: skip
        if target.mlx_name is not None:  # a switch during the last verdict counts too
            await self._owned(target.mlx_name, generation)
        if errors:
            plan.failures.append(f"judge: {errors} of {len(rows)} cases got no verdict")

    async def _after_models(self, plan: RunPlan) -> None:
        """After the last model, before restoration (a run that aborted never gets here):
        promote every model's eval scores if the run stayed controlled. A model with any
        ``failed`` case (timeout, HTTP error) keeps its summary but is not promoted, so a
        flaky or out-of-memory run never becomes its latest score."""
        if plan.kind != "eval" or not plan.controlled:
            return
        assert self.evals is not None
        for name, case_id in plan.eval_cases.items():
            failed = any(r["grade_state"] == "failed" for r in self.evals.results(plan.run_id, name))
            self._finalize_eval(plan, name, case_id, promote=not failed)

    def _finalize_eval(
        self, plan: RunPlan, name: str, case_id: int | None, *, promote: bool
    ) -> dict[str, Any]:
        """Score one model's results (promoting them when asked) and, given its case row,
        record the summary there."""
        assert self.evals is not None
        summary = self.evals.finalize(plan.run_id, name, promote=promote)
        if case_id is not None:
            status = "complete" if summary["n"] else "incomplete"
            self.store.complete_eval_case(case_id, status, summary)
        return summary

    async def _eval_case(
        self, http: httpx.AsyncClient, plan: RunPlan, case: EvalCase, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST one case and grade it: the result row's fields (never raises for the
        case's own failure)."""
        deadline = self._eval_deadline(plan, payload["max_tokens"])
        t0 = time.monotonic()

        def failed(error: str) -> dict[str, Any]:
            return {
                "error": error,
                "grade_detail": error,
                "grade_state": "failed",
                "latency_s": round(time.monotonic() - t0, 3),
            }

        try:
            async with asyncio.timeout(deadline):
                resp = await http.post(
                    "/v1/chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(deadline, connect=5.0),  # not streamed: one long read
                )
        except TimeoutError:
            return failed(f"no response within {deadline:g}s")
        except httpx.HTTPError as exc:
            return failed(f"request failed: {exc}")
        if resp.status_code != 200:
            return failed(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            doc = resp.json()
            choice = doc["choices"][0]
            msg = choice["message"]
            if not isinstance(msg, dict):
                raise TypeError("message is not an object")
        except (ValueError, KeyError, IndexError, TypeError):
            return failed("invalid response from the model server")
        latency = round(time.monotonic() - t0, 3)
        content = msg.get("content") if isinstance(msg.get("content"), str) else None
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
        calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else None
        finish = choice.get("finish_reason")
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        fields: dict[str, Any] = {
            "response": content,
            "reasoning_chars": reasoning_chars,
            "tool_calls": calls,
            "finish_reason": finish,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "latency_s": latency,
        }
        # off the event loop: a custom regex can still backtrack for a while
        graded = await asyncio.to_thread(
            grade_response, case.grader, content, finish, reasoning_chars, calls,
            thinking=plan.thinking,
        )  # fmt: skip
        return fields | graded

    # --- restoration -------------------------------------------------------------------
    async def _restore(self, http: httpx.AsyncClient, plan: RunPlan) -> dict[str, Any]:
        want = plan.original
        detail = ""
        try:
            obs = await self._observe()
            if want["engine"] == "strata":  # never switched: only confirm it still runs
                ok = obs["engine"] == "strata"
                return {
                    "outcome": "not_needed" if ok else "failed",
                    "detail": "" if ok else f"engine is {obs['engine']}",
                    "observed": obs,
                }
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
