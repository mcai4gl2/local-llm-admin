"""FastAPI app: auth, pages, and the JSON/SSE API."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import logging.handlers
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__, compatibility
from . import quality as quality_mod
from .alerts import AlertStore, Snapshot
from .auth import COOKIE_NAME, LoginLimiter, SessionManager, csrf_ok, verify_password
from .availability import AvailabilityRecorder, AvailabilityStore, summarize
from .backend_availability import BackendAvailabilityStore, backend_state
from .backend_types import EngineObservation, resolve_observations
from .backends import BackendUnavailable
from .chat import CHAT_OUTPUT_CAP, ChatRequest, effective_chat, stream_chat
from .comfy_outputs import (
    ID_RE,
    Entry,
    FileStream,
    OutputGone,
    OutputIndex,
    RangeInvalid,
    history_index,
    open_entry,
    parse_range,
)
from .comfy_queue import ComfyQueue, QueueError, public_view
from .config import Settings
from .control import ControlError, ControlService
from .decision import DecisionClient, DecisionError
from .decision_keys import ClientKeys, KeyError_
from .decision_store import DecisionStore, request_summary
from .diagnostics import Diagnostics, ProbeRunner
from .download_store import DownloadStore
from .downloads import DownloadError, DownloadService
from .embed import QUERY_INSTRUCTION, EmbedClient, EmbedError, validate_embed, validate_rerank
from .embed_store import EmbedStore
from .engine_telemetry import EngineTelemetryStore
from .engines import ComfyClient, StrataClient, observation_badge, strata_pack_of
from .eval_graders import grade_response
from .eval_judge import judge_target
from .eval_stats import paired
from .eval_store import EvalStore
from .eval_suites import (
    NEEDLE_MARGIN,
    EvalCase,
    Judge,
    Suite,
    SuiteError,
    builtin_suites,
    expand,
    messages_for,
    parse_jsonl,
)
from .inference_gateway import create_inference_app
from .ingestion import HostLogs, IngestStore
from .inventory import InventoryStore, build_inventory
from .jobs import JobRunner, JobStore, ops_tz
from .litellm_usage import LiteLLMUsage, UsageError, validate_range
from .log_rotation import LogMaintenance
from .logs import (
    Follower,
    download_log_ids,
    download_summary,
    file_info,
    filter_lines,
    resolve_source,
    source_ids,
    tail_lines,
)
from .metrics import Sampler, window_seconds
from .mlx import MlxClient, parse_start_markers, validate_name
from .ocr import OcrClient
from .ocr_routes import install_ocr_routes
from .performance_store import (
    PARSER,
    PERF_SOURCE,
    PerfLogs,
    PerformanceStore,
    request_context,
)
from .profiles import ProfileError, ProfileService
from .quality_runner import QualityError, QualityRunner, model_info
from .quality_store import QualityStore
from .service_store import ServiceStore
from .sidecars import (
    SidecarMonitor,
    SidecarPlan,
    SidecarRegistry,
    SidecarSpec,
    read_intent,
    read_service_intent,
    service_unavailable_reason,
)
from .splash import SplashClient
from .splash_monitor import SplashMonitor
from .strata_usage import StrataUsage
from .tts import TtsClient, TtsError, text_weight, validate_speech
from .tts_store import TtsStore
from .watchdog import Supervisor

log = logging.getLogger("local_llm_admin")
HERE = Path(__file__).parent
PAGES = {
    "/": "dashboard",
    "/models": "models",
    "/chat": "chat",
    "/logs": "logs",
    "/usage": "usage",
    "/alerts": "alerts",
    "/quality": "quality",
    "/performance": "performance",
    "/outputs": "outputs",
    "/decision": "decision",
    "/tts": "tts",
    "/embed": "embed",
    "/ocr": "ocr",
}
REQUEST_STATES = Literal["completed", "cancelled", "error", "incomplete"]
_PUBLIC = ("/login", "/healthz", "/static/")
INVENTORY_CACHE_S = 30.0
RUN_ID_RE = re.compile(r"^[a-z0-9]{12}$")


class ModelAction(BaseModel):
    name: str | None = None


class RegisterRequest(BaseModel):
    dir_name: str
    name: str


class RemoveRequest(BaseModel):
    name: str
    delete_files: bool = False
    confirmation: str | None = None


class DownloadPreviewRequest(BaseModel):
    repo: str
    revision: str = "main"


class DownloadStartRequest(BaseModel):
    repo: str
    revision: str
    dest: str


class ProfileRequest(BaseModel):
    launch: dict[str, Any] = {}
    chat: dict[str, Any] = {}


class DecisionClientRequest(BaseModel):
    name: str = Field(max_length=48)
    note: str | None = Field(default=None, max_length=200)


class EngineRequest(BaseModel):
    target: str
    force: bool = False


class SidecarStartRequest(BaseModel):
    confirm: bool = False


class SimilarityRequest(BaseModel):
    texts: list[str] = Field(min_length=2, max_length=8)
    query_first: bool = False


class AdminRerankRequest(BaseModel):
    query: str
    documents: list[str] = Field(min_length=1, max_length=20)
    instruction: str | None = None


class QueueDeleteRequest(BaseModel):
    ids: list[Any]
    session: str | None = None


class QueueSessionRequest(BaseModel):
    session: str | None = None


class QueueCancelRequest(BaseModel):
    id: Any = None
    session: str | None = None


class InterruptRequest(BaseModel):
    expected_running: list[Any] = []
    session: str | None = None


class EvalEstimateRequest(BaseModel):
    suite: str
    scope: str
    model: str | None = None
    thinking: bool = False


# decode and prefill speeds when no benchmark baseline exists for a model's weights
DEFAULT_DECODE_TOK_S = 20.0
DEFAULT_PREFILL_TOK_S = 300.0
MODEL_SWITCH_S = 60.0
GIB = 1024**3


class QualityRequest(BaseModel):
    kind: str
    scope: str
    model: str | None = None
    confirm_off: bool = False
    suite: str | None = None
    thinking: bool = False


def _setup_logging(settings: Settings) -> None:
    settings.admin_log_dir.mkdir(parents=True, exist_ok=True)
    path = (settings.admin_log_dir / "admin.log").resolve()
    if any(getattr(h, "baseFilename", None) == str(path) for h in log.handlers):
        return
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _backfill(settings: Settings, store: JobStore) -> int:
    path = settings.mlx_log_dir / "mlx-server.log"
    try:
        with path.open(errors="replace") as fh:  # streamed, never read whole into memory
            markers = parse_start_markers(line for line in fh if line.startswith("====="))
    except OSError:
        return 0
    return store.backfill(markers, settings.litellm_timezone_offset_min)


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


def create_app(
    settings: Settings | None = None,
    *,
    start_background: bool = True,
    chat_transport: httpx.AsyncBaseTransport | None = None,
    comfy_transport: httpx.MockTransport | None = None,
    quality_transport: httpx.AsyncBaseTransport | None = None,
    decision_transport: httpx.MockTransport | None = None,
    tts_transport: httpx.MockTransport | None = None,
    embed_transport: httpx.MockTransport | None = None,
    ocr_transport: httpx.MockTransport | None = None,
    strata_transport: httpx.MockTransport | None = None,
    splash_transport: httpx.MockTransport | None = None,
    judge_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()
    mlx = MlxClient(settings)
    comfy = ComfyClient(settings, transport=comfy_transport)
    strata = StrataClient(settings, transport=strata_transport)
    splash = SplashClient(settings, transport=splash_transport)
    store = JobStore(settings.admin_data_dir / "admin.sqlite")
    runner = JobRunner(
        settings, store, current_name=lambda: mlx.active_env().get("MLX_ACTIVE_NAME")
    )
    decision = DecisionClient(settings, transport=decision_transport)
    tts = TtsClient(settings, transport=tts_transport)
    embed = EmbedClient(settings, transport=embed_transport)
    ocr = OcrClient(settings, transport=ocr_transport)
    sidecar_registry = SidecarRegistry(
        settings,
        {"openjev": decision_transport, "qwen-tts": tts_transport, "qwen-embed": embed_transport,
         "paddleocr-vl": ocr_transport},
    )
    sidecar_registry.set_warmup("qwen-tts", tts.warmup_sync)
    sidecar_registry.set_warmup("qwen-embed", embed.warmup_sync)
    sidecar_registry.set_warmup("paddleocr-vl", lambda: ocr.warmup_sync(restarted=True))
    sidecar_registry.set_activity("paddleocr-vl", lambda: ocr.busy)
    control = ControlService(
        settings,
        store,
        runner,
        mlx,
        comfy,
        decision=decision,
        sidecars=sidecar_registry,
        strata=strata,
        splash=splash,
    )

    # The process tile follows the same unambiguous generation as lifecycle control.
    def engine_pid() -> int | None:
        generation = control.observe()["generation"]
        return int(generation["pid"]) if generation else None

    def metric_observation() -> EngineObservation | None:
        observed, selected = control._snapshot()
        if selected is not None or observed["engine"] == "none":
            return selected
        return EngineObservation(observed["engine"])

    sampler = Sampler(settings, mlx_pid=engine_pid, engine_observation=metric_observation)
    usage = LiteLLMUsage(settings)
    db_path = settings.admin_data_dir / "admin.sqlite"
    events = IngestStore(db_path)
    alerts = AlertStore(db_path)
    probes = ProbeRunner()
    perf = PerformanceStore(db_path)
    perf_logs = PerfLogs(settings, events, perf)
    native_telemetry = EngineTelemetryStore(db_path)
    splash_monitor = SplashMonitor(settings, splash, native_telemetry)
    backend_availability = BackendAvailabilityStore(db_path)

    def backend_health(observed: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        descriptions = {
            b.spec.id: {
                "label": b.spec.label,
                "enabled": b.spec.enabled,
                "auto_recover": b.spec.auto_recover,
            }
            for b in control.backends
        }
        cached = splash_monitor.snapshot()
        if cached.get("ready") is True:
            generation = (observed or {}).get("generation") or {}
            members = [
                {k: p[k] for k in ("pid", "started", "role")}
                for p in generation.get("processes", [])
            ]
            expected = cached.get("identity", {}).get("processes")
            if (observed or {}).get("engine") != "splash" or not members or members != expected:
                cached.update(
                    state="unknown", ready=None, profile=None, fingerprint=None, epoch=None
                )
        descriptions["splash"].update(cached)
        return descriptions

    supervisor = Supervisor(
        settings,
        control,
        alerts,
        events,
        HostLogs(settings, events),
        probes,
        sampler,
        mlx,
        backend_health=backend_health,
    )

    async def ingest_requests(snap: Snapshot, observed: dict[str, Any] | None) -> None:
        # its own probe: a slow first scan or a hung drive never delays the watchdog's
        # log evidence, which the "ingest" probe reads
        if snap.drive_mounted is not True:
            return
        res = await probes.run(
            "perf-ingest", lambda: app.state.perf_logs.poll(), settings.probe_timeout_s
        )
        if res.state == "error":
            log.warning("request ingestion failed: %s", res.error)

    last_backend_sample: float | None = None

    async def record_backend_availability(snap: Snapshot, observed: dict[str, Any] | None) -> None:
        nonlocal last_backend_sample
        if (
            last_backend_sample is not None
            and 0 <= snap.wall - last_backend_sample < settings.availability_interval_s
        ):
            return
        last_backend_sample = snap.wall
        if settings.splash_enabled or (snap.intent or "").startswith("splash:"):
            splash_sample = backend_state(snap, "splash")
            # The monitor's cached bundle must match this tick's local generation.
            identity = snap.backends.get("splash", {}).get("identity", {})
            generation = (observed or {}).get("generation") or {}
            observed_members = [
                {k: p[k] for k in ("pid", "started", "role")}
                for p in generation.get("processes", [])
            ]
            if splash_sample["state"] == "serving" and (
                not observed_members or observed_members != identity.get("processes")
            ):
                splash_sample["state"] = "unknown"

            def persist() -> None:
                backend_availability.add("splash", snap.wall, splash_sample)
                backend_availability.prune(
                    snap.wall - settings.observability_retention_days * 86400
                )

            await probes.run("backend-availability", persist, settings.probe_timeout_s)

        if settings.strata_enabled or (snap.intent or "").partition(":")[0] == "strata":
            descriptor: dict[str, Any] = {"enabled": settings.strata_enabled, "state": "unknown", "ready": False}
            if snap.engine == "strata" and (observed or {}).get("generation"):
                health = await strata.get_json("/health")
                checked = await probes.run("strata-availability-identity", control.observe, settings.probe_timeout_s)
                if checked.state == "done" and checked.value == observed and isinstance(health, dict) and health.get("service") == "strata":
                    loaded = health.get("loaded") is True
                    descriptor.update(ready=loaded, state="ready" if loaded else "starting", profile=strata_pack_of(health.get("model")))
            strata_sample = backend_state(dataclasses.replace(snap, backends={**snap.backends, "strata": descriptor}), "strata")

            def persist_strata() -> None:
                backend_availability.add("strata", snap.wall, strata_sample)
                backend_availability.prune(snap.wall - settings.observability_retention_days * 86400)

            await probes.run("strata-availability", persist_strata, settings.probe_timeout_s)

    supervisor.add_listener(record_backend_availability)
    supervisor.add_listener(ingest_requests)
    strata_usage = StrataUsage(settings, strata, perf)
    supervisor.add_listener(strata_usage.on_tick)
    availability = AvailabilityStore(db_path)
    inventory = InventoryStore(db_path)
    downloads_svc = DownloadService(settings, DownloadStore(db_path), probes)
    supervisor.add_listener(downloads_svc.on_tick)

    def prune_requests(before: float) -> None:
        # token totals of pruned rows are folded per ops day, so they need its offset
        perf.prune_bounded(before, tz_offset_min=settings.litellm_timezone_offset_min)

    supervisor.add_listener(
        AvailabilityRecorder(availability, settings, prune_extra=prune_requests).on_tick
    )
    maintenance = LogMaintenance(settings, control, alerts)
    supervisor.add_listener(maintenance.on_tick)
    decision_history = DecisionStore(db_path)
    decision_keys = ClientKeys(db_path)
    tts_history = TtsStore(db_path)
    embed_history = EmbedStore(db_path)
    service_history = ServiceStore(db_path, settings)

    def ocr_reason() -> str | None:
        reason = service_unavailable_reason(
            sidecar_registry, store, "paddleocr-vl", control.exclusive_running,
        )
        return reason if reason is not None else ocr.unavailable_reason_sync()

    def embed_reason() -> str | None:
        return service_unavailable_reason(
            sidecar_registry, store, "qwen-embed", control.exclusive_running
        )

    supervisor.add_listener(
        SidecarMonitor(
            settings,
            sidecar_registry,
            store,
            alerts,
            history=decision_history,
            backends=control.backends,
            prune_extra=[tts_history.prune, embed_history.prune, service_history.prune],
        )
    )
    quality_store = QualityStore(db_path)
    evals = EvalStore(db_path)
    quality = QualityRunner(
        settings,
        control,
        quality_store,
        alerts,
        sampler,
        transport=quality_transport,
        evals=evals,
        judge_transport=judge_transport,
    )
    profiles = ProfileService(settings, mlx, control, quality_store)
    comfy_queue = ComfyQueue(comfy, control, store)
    outputs = OutputIndex(
        settings.comfyui_root / "output",
        max_entries=settings.comfyui_output_scan_max,
        max_depth=settings.comfyui_output_scan_depth,
        ttl_s=settings.comfyui_output_cache_s,
    )
    diagnostics = Diagnostics(
        settings,
        mlx,
        control,
        sampler,
        events,
        probes,
        watchdog_state=supervisor.state,
        backend_health=lambda: backend_health(control.observe()),
    )
    sessions = SessionManager(
        settings.admin_session_secret.get_secret_value(), settings.admin_session_max_age_s
    )
    limiter = LoginLimiter(settings.admin_login_max_failures, settings.admin_login_window_s)
    templates = Jinja2Templates(directory=HERE / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _setup_logging(settings)
        log.info("admin %s starting", __version__)
        # before any request can submit a job: a row still "running" belongs to a
        # previous process
        control.reconcile()
        quality.reconcile()
        evals.sync_builtins(builtin_suites())  # refuses an edited builtin suite version

        async def backfill() -> None:
            # never block startup on the model drive: a macOS privacy prompt or a hung
            # USB volume can stall the first read indefinitely
            added = await asyncio.to_thread(_backfill, settings, store)
            log.info("backfilled %d switch-history rows", added)

        backfill_task = asyncio.create_task(backfill())
        app.state.backfill_task = backfill_task
        # observation touches the drive, so it never blocks startup either
        startup_task = asyncio.create_task(asyncio.to_thread(control.adopt_initial))
        app.state.startup_task = startup_task
        if start_background:
            sampler.start()
            splash_monitor.start()
            supervisor.start()
        yield
        await quality.shutdown()  # before anything it depends on stops
        await splash_monitor.stop()
        await supervisor.stop()
        await sampler.stop()
        probes.shutdown()  # never waits for a hung probe thread
        for task in (backfill_task, startup_task):
            if not task.done():
                task.cancel()

    app = FastAPI(
        title="Local LLM Admin",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.control = control
    app.state.backends = control.backends
    app.state.decision = decision
    app.state.sidecars = sidecar_registry
    app.state.tts = tts
    app.state.tts_history = tts_history
    app.state.embed = embed
    app.state.embed_history = embed_history
    app.state.embed_reason = embed_reason
    app.state.ocr = ocr
    app.state.ocr_reason = ocr_reason
    app.state.service_history = service_history
    app.state.decision_history = decision_history
    app.state.decision_keys = decision_keys
    app.state.sampler_pressure = lambda: sampler.current().get("pressure")
    control.memory_pressure = lambda: app.state.sampler_pressure()
    app.state.alerts = alerts
    app.state.supervisor = supervisor
    app.state.quality = quality
    app.state.evals = evals
    app.state.maintenance = maintenance
    app.state.perf_logs = perf_logs
    app.state.strata_usage = strata_usage
    app.state.splash_monitor = splash_monitor
    app.state.backend_availability = backend_availability
    app.state.inventory_timeout_s = settings.inventory_timeout_s
    app.state.inventory_cache = None  # (monotonic time, result)
    app.state.downloaded_at = downloads_svc.downloaded_at  # published path -> time
    app.state.downloads = downloads_svc
    app.state.profiles = profiles
    app.state.outputs = outputs
    app.state.follow_interval_s = 1.0
    app.state.follow_max_s = 1800.0
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    inference = create_inference_app(
        settings,
        control.backends,
        transport=chat_transport,
        busy=lambda: runner.running() is not None,
    )
    app.state.inference = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.inference.mount("/v1", inference)

    @app.middleware("http")
    async def require_session(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path == "/healthz" or path.startswith(_PUBLIC):
            return await call_next(request)
        session = sessions.load(request.cookies.get(COOKIE_NAME))
        if session is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "login required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD") and not csrf_ok(
            session, request.headers.get("x-csrf-token")
        ):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        request.state.session = session
        return await call_next(request)

    # --- auth & pages ----------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(request: Request) -> Response:
        ip = request.client.host if request.client else "?"
        if not limiter.allowed(ip):
            log.warning("login rate-limited for %s", ip)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Too many attempts. Try again later."},
                status_code=429,
            )
        form = await request.form()
        password = str(form.get("password", ""))
        if not verify_password(settings.admin_password_hash.get_secret_value(), password):
            limiter.fail(ip)
            log.warning("failed login from %s", ip)
            return templates.TemplateResponse(
                request, "login.html", {"error": "Wrong password."}, status_code=401
            )
        limiter.reset(ip)
        cookie, _csrf = sessions.issue()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE_NAME,
            cookie,
            max_age=settings.admin_session_max_age_s,
            httponly=True,
            samesite="strict",
            secure=settings.admin_cookie_secure,
        )
        log.info("login from %s", ip)
        return resp

    @app.post("/logout")
    async def logout() -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    def _page(page: str) -> Callable[[Request], Awaitable[Response]]:
        async def render(request: Request) -> Response:
            if page == "decision" and not settings.decision_enabled:
                raise HTTPException(404, "the decision sidecar is not enabled")
            if page == "tts" and not settings.tts_enabled:
                raise HTTPException(404, "the TTS sidecar is not enabled")
            if page == "embed" and not settings.embed_enabled:
                raise HTTPException(404, "the embedding service is not enabled")
            if page == "ocr" and not settings.ocr_enabled:
                raise HTTPException(404, "the OCR service is not enabled")
            return templates.TemplateResponse(
                request,
                f"{page}.html",
                {
                    "page": page,
                    "csrf": request.state.session["csrf"],
                    "version": __version__,
                    "ops_timezone_offset_min": settings.litellm_timezone_offset_min,
                    "decision_enabled": settings.decision_enabled,
                    "tts_enabled": settings.tts_enabled,
                    "embed_enabled": settings.embed_enabled,
                    "ocr_enabled": settings.ocr_enabled,
                },
            )

        return render

    for route, page in PAGES.items():
        app.add_api_route(route, _page(page), methods=["GET"], include_in_schema=False)

    @app.get("/api/session")
    async def session_info(request: Request) -> dict[str, str]:
        return {"csrf": request.state.session["csrf"]}

    # --- status & metrics -------------------------------------------------
    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        job = runner.running()
        details = await control.backends.status_details(job_running=job is not None)
        # Keep the existing public fields; normalized metadata stays internal.
        st = dict(details["mlx"])
        st.pop("ready", None)
        st.pop("reference", None)
        comfy_st = dict(details["comfyui"])
        strata_st = dict(details["strata"]) if "strata" in details else None
        if strata_st is not None:
            strata_st.pop("reference", None)
        observations = await asyncio.to_thread(control.backends.from_status, details)
        active = resolve_observations(observations)
        st["state"] = observation_badge(st["state"], observations)
        st["engine"] = {
            "active": active,
            "comfyui": comfy_st,
            "strata": strata_st,
            "strata_enabled": settings.strata_enabled,
            "splash_enabled": settings.splash_enabled,
            "splash": {k: v for k, v in details["splash"].items() if k not in {"target_options"}}
            if "splash" in details
            else None,
            "backends": await asyncio.to_thread(
                control.backends.descriptions, details, observations
            ),
        }
        st["job"] = job
        st["quality_active"] = quality.active()
        st["measurement_coverage"] = {"splash": await asyncio.to_thread(
            native_telemetry.coverage, settings.splash_enabled,
            now=time.time(), stale_s=settings.splash_poll_interval_s * 3,
        )}
        st["checked_at"] = time.time()
        st["intent"] = await asyncio.to_thread(control.intent)
        st["recovery_blocked"] = await asyncio.to_thread(control.recovery_blocked)
        st["alerts"] = await asyncio.to_thread(alerts.summary)
        st["watchdog"] = await asyncio.to_thread(supervisor.state)
        st["decision"] = {
            "enabled": settings.decision_enabled,
            "running": settings.decision_enabled
            and (await asyncio.to_thread(decision.running)).running,
        }
        running_sidecar = await asyncio.to_thread(control.sidecar_running)
        st["sidecar"] = {"running": running_sidecar.name if running_sidecar else None}
        return st

    @app.get("/api/comfyui")
    async def comfyui_card() -> dict[str, Any]:
        card = await comfy.status()
        card["weights"] = await asyncio.to_thread(comfy.weights)
        card["output"] = await asyncio.to_thread(comfy.output)
        return card

    # --- ComfyUI queue (M5) ----------------------------------------------------
    async def _queue(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        try:
            return await call
        except QueueError as exc:
            raise HTTPException(exc.code, exc.message) from None

    @app.get("/api/comfyui/queue")
    async def comfyui_queue() -> dict[str, Any]:
        view = public_view(await comfy_queue.snapshot())
        view["job_running"] = runner.running() is not None
        return view

    @app.post("/api/comfyui/queue/delete")
    async def comfyui_queue_delete(body: QueueDeleteRequest) -> dict[str, Any]:
        return await _queue(comfy_queue.delete(body.ids, body.session))

    @app.post("/api/comfyui/queue/clear")
    async def comfyui_queue_clear(body: QueueSessionRequest) -> dict[str, Any]:
        return await _queue(comfy_queue.clear(body.session))

    @app.post("/api/comfyui/queue/cancel")
    async def comfyui_queue_cancel(body: QueueCancelRequest) -> dict[str, Any]:
        return await _queue(comfy_queue.cancel_running(body.id, body.session))

    @app.post("/api/comfyui/interrupt")
    async def comfyui_interrupt(body: InterruptRequest) -> dict[str, Any]:
        return await _queue(comfy_queue.interrupt(body.expected_running, body.session))

    # --- ComfyUI outputs (M5, read-only) -------------------------------------------
    async def _output_scan(refresh: bool) -> Any:
        cached = None if refresh else outputs.fresh()
        if cached is not None:
            return cached
        res = await probes.run("comfy-outputs", outputs.rescan, settings.comfyui_output_timeout_s)
        if res.state == "done":
            return res.value
        if res.state == "error" and isinstance(res.error, FileNotFoundError):
            return "the output folder was not found (is the model drive mounted?)"
        if res.state == "error":
            log.warning("output scan failed: %s", res.error)
        return {
            "busy": "a previous scan is still running (is the drive responding?)",
            "timeout": "the scan did not finish in time (is the drive responding?)",
        }.get(res.state, "the output folder could not be read")

    @app.get("/api/comfyui/outputs")
    async def comfyui_outputs(
        offset: int = Query(0, ge=0, le=1_000_000),
        limit: int = Query(48, ge=1, le=200),
        refresh: bool = False,
    ) -> dict[str, Any]:
        result = await _output_scan(refresh)
        if isinstance(result, str):
            return {"available": False, "reason": result, "items": [], "total": 0}
        page = result.entries[offset : offset + limit]
        items = [e.public() for e in page]
        history_state = "stopped"
        if items and (await asyncio.to_thread(comfy.running)).running:
            hist = await comfy.history(settings.comfyui_history_items)
            history_state = "ok" if hist is not None else "unavailable"
            index = history_index(hist)
            for it in items:
                it["job"] = index.get((it["subfolder"], it["name"]))
        nxt = offset + limit
        return {
            "available": True,
            "items": items,
            "total": len(result.entries),
            "offset": offset,
            "limit": limit,
            "next_offset": nxt if nxt < len(result.entries) else None,
            "truncated": result.truncated,
            "skipped": result.skipped,
            "scanned_at": result.scanned_at,
            "history": history_state,
        }

    async def _open_output(entry: Entry) -> tuple[int, Any]:
        """Open with a deadline; a descriptor opened after the deadline is closed."""
        fut = asyncio.ensure_future(asyncio.to_thread(open_entry, outputs.root, entry))
        try:
            return await asyncio.wait_for(asyncio.shield(fut), settings.probe_timeout_s)
        except TimeoutError:

            def close_late(f: asyncio.Future[tuple[int, Any]]) -> None:
                if not f.cancelled() and f.exception() is None:
                    os.close(f.result()[0])

            fut.add_done_callback(close_late)
            raise HTTPException(504, "the output file did not open in time") from None

    @app.get("/api/comfyui/outputs/{entry_id}/content")
    async def comfyui_output_content(
        entry_id: str, request: Request, download: bool = False
    ) -> Response:
        if not ID_RE.fullmatch(entry_id):
            raise HTTPException(400, "invalid output id")
        entry = outputs.lookup(entry_id)
        if entry is None and outputs.fresh() is None:
            await _output_scan(True)  # ids survive only in memory: rebuild after a restart
            entry = outputs.lookup(entry_id)
        if entry is None:
            raise HTTPException(404, "unknown output; refresh the list")
        try:
            fd, st = await _open_output(entry)
        except OutputGone:
            raise HTTPException(410, "the file was removed or replaced; refresh the list") from None
        size = st.st_size
        try:
            rng = parse_range(request.headers.get("range"), size)
        except RangeInvalid:
            os.close(fd)
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start, end = rng if rng else (0, size - 1)
        length = max(0, end - start + 1)
        name = entry.rel.rpartition("/")[2]
        disposition = "attachment" if download else "inline"
        headers = {
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
        }
        if rng:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            FileStream(fd, start, length),
            status_code=206 if rng else 200,
            media_type=entry.mime,
            headers=headers,
        )

    @app.get("/api/metrics/current")
    async def metrics_current() -> dict[str, Any]:
        return await asyncio.to_thread(sampler.current)

    @app.get("/api/metrics/history")
    async def metrics_history(window: str = "15m") -> dict[str, Any]:
        try:
            window_seconds(window)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"window": window, "points": sampler.history(window)}

    # --- models & jobs ----------------------------------------------------
    def _models_and_disk() -> dict[str, Any]:
        registered = mlx.list_models()
        fps = profiles.fingerprints()
        for m in registered:
            m["profile_fingerprint"] = fps.get(m["name"], "default")
        return {"models": registered, "discovered": mlx.discover(registered)}

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        # read fresh on every call: registry edits and new downloads show up without a restart
        return await asyncio.to_thread(_models_and_disk)

    async def _control(call: Awaitable[dict[str, Any]]) -> JSONResponse:
        try:
            return JSONResponse(await call, status_code=202)
        except ControlError as exc:
            raise HTTPException(exc.code, exc.message) from None

    @app.post("/api/models/register")
    async def register(body: RegisterRequest) -> JSONResponse:
        return await _control(control.register(body.dir_name, body.name))

    def _scan_inventory() -> dict[str, Any]:
        """Runs inside the probe: the weights fingerprint reads the drive, so it is cached too."""
        inv = build_inventory(mlx, inventory, downloaded=app.state.downloaded_at)
        for m in inv["models"]:
            m["weights_fingerprint"] = model_info(m["dir"] if m["exists"] else None)["fingerprint"]
        return inv

    async def _with_evals(inv: dict[str, Any]) -> dict[str, Any]:
        """Fresh promoted eval means per model (a DB read only), so the 30 s cache never hides a run."""

        def attach() -> dict[str, Any]:
            by_fp = {c["fingerprint"]: c["suites"] for c in evals.scorecard()}
            models = []
            for m in inv.get("models", []):
                fp = m.get("weights_fingerprint")
                suites = by_fp.get(fp, {}) if fp else {}
                # the Models tab shows thinking-off scores only ("<suite>+thinking" cells skipped)
                means = {k: v["mean"] for k, v in suites.items() if not v["thinking"]}
                models.append({**m, "eval": means})
            return {**inv, "models": models}

        return await asyncio.to_thread(attach)

    @app.get("/api/models/inventory")
    async def models_inventory(refresh: bool = False) -> dict[str, Any]:
        cached = app.state.inventory_cache
        if cached and not refresh and time.monotonic() - cached[0] < INVENTORY_CACHE_S:
            value: dict[str, Any] = cached[1]
            return await _with_evals(value)
        res = await probes.run(
            "inventory",
            _scan_inventory,
            app.state.inventory_timeout_s,
        )
        if res.state != "done":
            reason = {
                "busy": "a previous scan is still running (is the drive responding?)",
                "timeout": "the scan did not finish in time (is the drive responding?)",
            }.get(res.state, "the scan failed")
            if res.state == "error":
                log.warning("inventory scan failed: %s", res.error)
            return {"available": False, "reason": reason}
        app.state.inventory_cache = (time.monotonic(), res.value)
        out: dict[str, Any] = res.value
        return await _with_evals(out)

    @app.get("/api/models/{name}/remove-preview")
    async def remove_preview(name: str) -> dict[str, Any]:
        try:
            validate_name(name)
        except ValueError:
            raise HTTPException(400, "invalid model name") from None
        preview = await asyncio.to_thread(control.remove_preview, name)
        if preview is None:
            raise HTTPException(404, "unknown model name")
        return preview

    @app.post("/api/models/remove")
    async def remove_model(body: RemoveRequest) -> JSONResponse:
        app.state.inventory_cache = None
        return await _control(control.remove(body.name, body.delete_files, body.confirmation))

    # --- profiles (M3c) ----------------------------------------------------------------
    def _profile_error(exc: ProfileError) -> HTTPException:
        return HTTPException(exc.code, exc.message)

    @app.get("/api/models/{name}/profile")
    async def profile_get(name: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(profiles.show, name)
        except ProfileError as exc:
            raise _profile_error(exc) from None

    async def _profile_change(name: str, action: str, doc: dict[str, Any] | None) -> JSONResponse:
        try:
            return JSONResponse(await profiles.change(name, action, doc), status_code=202)
        except ProfileError as exc:
            raise _profile_error(exc) from None
        except ControlError as exc:
            raise HTTPException(exc.code, exc.message) from None

    @app.post("/api/models/{name}/profile")
    async def profile_set(name: str, body: ProfileRequest) -> JSONResponse:
        return await _profile_change(name, "set", {"launch": body.launch, "chat": body.chat})

    @app.post("/api/models/{name}/profile/reset")
    async def profile_reset(name: str) -> JSONResponse:
        return await _profile_change(name, "reset", None)

    @app.post("/api/models/{name}/profile/revert")
    async def profile_revert(name: str) -> JSONResponse:
        return await _profile_change(name, "revert", None)

    @app.post("/api/engine")
    async def engine(body: EngineRequest) -> JSONResponse:
        return await _control(control.engine(body.target, body.force))

    @app.post("/api/models/switch")
    async def switch(body: ModelAction) -> JSONResponse:
        if not body.name:
            raise HTTPException(400, "name is required")
        return await _control(control.lifecycle("switch", body.name))

    @app.post("/api/models/start")
    async def start(body: ModelAction | None = None) -> JSONResponse:
        return await _control(control.lifecycle("start", body.name if body else None))

    @app.post("/api/models/stop")
    async def stop() -> JSONResponse:
        return await _control(control.lifecycle("stop", None))

    # --- sidecar slot (M7) ---------------------------------------------------------
    def _budget_view(b: SidecarPlan) -> dict[str, Any]:
        return {
            "ok": b.ok,
            "reasons": b.reasons,
            "lines": b.lines,
            "total_gb": b.total_gb,
            "limit_gb": b.limit_gb,
            "needs_confirm": b.needs_confirm,
            "stop_first": b.stop_first.name if b.stop_first else None,
        }

    async def _service_availability(running: bool, ready: bool, intent: str | None) -> dict[str, Any]:
        owner = await asyncio.to_thread(control.exclusive_running)
        label = control.backends.get(owner).spec.label if owner else None
        state = "ready" if ready else "starting" if running else "paused" if owner and intent == "running" else "down" if intent == "running" else "stopped"
        reason = (
            f"{label} owns the GPU. Switch engines on the Models page; restoration follows saved intent and target compatibility."
            if owner else "Ready" if ready else "Starting; waiting for readiness."
            if running else "Expected to run but unavailable. Inspect the job and logs on Models."
            if intent == "running" else "Stopped. Start it on the Models page."
        )
        return {"available": ready and owner is None, "state": state, "owner": owner,
                "paused": state == "paused", "intent": intent or "stopped", "reason": reason}

    async def _sidecar_view(spec: SidecarSpec) -> dict[str, Any]:
        proc = sidecar_registry.process(spec.name)
        st = await asyncio.to_thread(proc.status)
        ready = st.running and await asyncio.to_thread(proc.ready_sync)
        cli = await asyncio.to_thread(proc.cli_status)
        budget = await control.sidecar_budget(spec.name, app.state.sampler_pressure())
        saved = await asyncio.to_thread(read_intent, store)
        intent = saved.get("value") if saved and saved.get("name") == spec.name else None
        availability = await _service_availability(st.running, ready, intent)
        return {"availability": availability, "paused": availability["paused"], "intent": intent or "stopped", "checked_at": time.time(),
            "name": spec.name,
            "label": spec.label,
            "title": spec.title,
            "running": st.running,
            "pid": st.pid,
            "ready": ready,
            "rss_bytes": proc.rss_bytes(st.pid),
            "listen": spec.api_base,
            "weights": cli["weights"],
            "allowed_mlx": sorted(spec.allowed_mlx),
            "budget": _budget_view(budget),
        }

    @app.get("/api/sidecars")
    async def sidecars_view() -> dict[str, Any]:
        specs = sidecar_registry.specs()
        views = [await _sidecar_view(s) for s in specs]
        intent = await asyncio.to_thread(read_intent, store)
        return {
            "sidecars": views,
            "intent": {"name": intent.get("name"), "value": intent.get("value")}
            if intent
            else None,
            "conflict": sum(v["running"] for v in views) > 1,
        }

    @app.post("/api/sidecars/{name}/start")
    async def sidecar_start(name: str, body: SidecarStartRequest) -> JSONResponse:
        return await _control(
            control.sidecar_start(name, app.state.sampler_pressure(), confirm=body.confirm)
        )

    @app.post("/api/sidecars/{name}/stop")
    async def sidecar_stop(name: str) -> JSONResponse:
        return await _control(control.sidecar_stop(name))

    # --- decision sidecar (M6) -----------------------------------------------
    def _decision_enabled() -> None:
        if not settings.decision_enabled:
            raise HTTPException(404, "the decision sidecar is not enabled")

    @app.get("/api/decision")
    async def decision_status() -> dict[str, Any]:
        _decision_enabled()
        st = await decision.status()
        budget = await control.decision_budget(app.state.sampler_pressure())
        cli = await asyncio.to_thread(decision.cli_status)
        intent = await asyncio.to_thread(read_intent, store)
        return {
            **st,
            "availability": await _service_availability(st["running"], st["ready"], intent["value"] if intent and intent.get("name") == "openjev" else None),
            "intent": intent["value"] if intent and intent.get("name") == "openjev" else None,
            "weights": cli["weights"],
            "allowed_mlx": sorted(settings.decision_allowed),
            "budget": {
                "ok": budget.ok,
                "reasons": budget.reasons,
                "lines": budget.lines,
                "total_gb": budget.total_gb,
                "limit_gb": budget.limit_gb,
            },
        }

    @app.post("/api/decision/start")
    async def decision_start() -> JSONResponse:
        _decision_enabled()
        return await _control(control.decision_start(app.state.sampler_pressure()))

    @app.post("/api/decision/read")
    async def decision_read(request: Request) -> dict[str, Any]:
        """One read through the admin (the test page): the browser never sees the key."""
        _decision_enabled()
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "body is not JSON") from None
        summary = request_summary(body)
        t0 = time.perf_counter()
        try:
            out = await decision.read(body)
        except DecisionError as exc:
            await asyncio.to_thread(
                decision_history.add_request,
                ts=time.time(),
                source="admin-ui",
                summary=summary,
                input_tokens=None,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                model_ms=None,
                status="error",
                error_code=exc.code,
            )
            raise HTTPException(exc.code, exc.message) from None
        await asyncio.to_thread(
            decision_history.add_request,
            ts=time.time(),
            source="admin-ui",
            summary=summary,
            input_tokens=out["usage"]["input_tokens"],
            latency_ms=out["latency_ms"],
            model_ms=out["server_timing"].get("model"),
            status="ok",
            error_code=None,
        )
        return out

    @app.get("/api/decision/history")
    async def decision_history_view(
        days: int = Query(14, ge=1, le=90),
    ) -> dict[str, Any]:
        _decision_enabled()
        now = time.time()
        frm = now - days * 86400
        return {
            "recent": await asyncio.to_thread(decision_history.recent, 100),
            "daily": await asyncio.to_thread(
                decision_history.daily, frm, now, settings.litellm_timezone_offset_min
            ),
            "availability": await asyncio.to_thread(decision_history.availability, frm, now),
            "days": days,
        }

    @app.get("/api/decision/clients")
    async def decision_clients() -> dict[str, Any]:
        _decision_enabled()
        return {"clients": await asyncio.to_thread(decision_keys.list)}

    def _key_error(exc: KeyError_) -> HTTPException:
        return HTTPException(exc.code, exc.message)

    @app.post("/api/decision/clients")
    async def decision_client_create(body: DecisionClientRequest) -> JSONResponse:
        """The key is in this response only; it is never shown or stored again."""
        _decision_enabled()
        try:
            created = await asyncio.to_thread(decision_keys.create, body.name, body.note)
        except KeyError_ as exc:
            raise _key_error(exc) from None
        log.info("decision gateway client %s created", created["name"])
        return JSONResponse(created, status_code=201)

    @app.post("/api/decision/clients/{client_id}/rename")
    async def decision_client_rename(client_id: int, body: DecisionClientRequest) -> dict[str, Any]:
        _decision_enabled()
        try:
            await asyncio.to_thread(decision_keys.rename, client_id, body.name)
        except KeyError_ as exc:
            raise _key_error(exc) from None
        return {"ok": True}

    @app.post("/api/decision/clients/{client_id}/revoke")
    async def decision_client_revoke(client_id: int) -> dict[str, Any]:
        _decision_enabled()
        try:
            await asyncio.to_thread(decision_keys.revoke, client_id)
        except KeyError_ as exc:
            raise _key_error(exc) from None
        log.info("decision gateway client %s revoked", client_id)
        return {"ok": True}

    @app.get("/api/decision/usage")
    async def decision_usage(
        days: int = Query(30, ge=1, le=90),
        group: Literal["day", "client"] = "day",
    ) -> dict[str, Any]:
        _decision_enabled()
        now = time.time()
        names = await asyncio.to_thread(decision_keys.names)
        rows = await asyncio.to_thread(
            decision_history.usage,
            now - days * 86400,
            now,
            settings.litellm_timezone_offset_min,
            group,
            names,
        )
        return {
            "rows": rows,
            "days": days,
            "group": group,
            "gateway": f":{settings.decision_gateway_port}",
            "require_key": settings.decision_gateway_require_key,
        }

    @app.post("/api/decision/stop")
    async def decision_stop() -> JSONResponse:
        _decision_enabled()
        return await _control(control.decision_stop())

    @app.post("/api/diagnostics/unlock")
    async def diagnostics_unlock() -> JSONResponse:
        return await _control(control.unlock())

    @app.post("/api/watchdog/resume")
    async def watchdog_resume() -> dict[str, Any]:
        await asyncio.to_thread(control.resume)
        log.info("watchdog resumed")
        return await asyncio.to_thread(supervisor.state)

    # --- TTS sidecar (M7) --------------------------------------------------------------
    def _tts_enabled() -> None:
        if not settings.tts_enabled:
            raise HTTPException(404, "the TTS sidecar is not enabled")

    @app.get("/api/tts")
    async def tts_status() -> dict[str, Any]:
        _tts_enabled()
        proc = sidecar_registry.process("qwen-tts")
        st = await asyncio.to_thread(proc.status)
        ready = st.running and await asyncio.to_thread(proc.ready_sync)
        intent = await asyncio.to_thread(read_intent, store)
        availability = await _service_availability(st.running, ready, intent.get("value") if intent and intent.get("name") == "qwen-tts" else None)
        return {"availability": availability, "checked_at": time.time(),
            "enabled": True,
            "running": st.running,
            "ready": ready,
            "model_id": settings.tts_model_id,
            "voices": settings.tts_voice_list,
            "default_voice": settings.tts_default_voice or None,
            "max_chars": settings.tts_max_input_chars,
            "chunk_chars": settings.tts_chunk_chars,
            "formats": ["wav", "mp3"],
            "gateway": f":{settings.decision_gateway_port}",
            "require_key": settings.decision_gateway_require_key,
        }

    @app.post("/api/tts/speak")
    async def tts_speak(request: Request) -> Response:
        """One synthesis through the admin (the test page): the browser never sees a key."""
        _tts_enabled()
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "body is not JSON") from None
        t0 = time.perf_counter()
        req: dict[str, Any] | None = None

        async def record(
            status: str, code: int | None, chunks: int | None = None, audio_s: float | None = None
        ) -> None:
            await asyncio.to_thread(
                tts_history.add_request,
                ts=time.time(),
                source="admin-ui",
                chars=len(req["input"]) if req else None,
                chunks=chunks,
                voice=req.get("voice") if req else None,
                fmt=req.get("response_format") if req else None,
                audio_s=audio_s,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                status=status,
                error_code=code,
            )

        try:
            req = validate_speech(body, settings, settings.tts_max_input_chars)
            if await asyncio.to_thread(sidecar_registry.down_sync, "qwen-tts"):
                raise TtsError(503, "the TTS sidecar is stopped; start it on the Models page")
            if not tts.try_acquire(text_weight(req["input"])):
                raise TtsError(429, f"the TTS sidecar is busy; retry in {tts.retry_after_s()} s")
            try:
                res = await tts.speak(req)
            finally:
                tts.release()
        except TtsError as exc:
            await record("error", exc.code)
            raise HTTPException(exc.code, exc.message) from None
        await record("ok", None, res.chunks, res.audio_s)
        return Response(
            res.audio,
            media_type=res.media_type,
            headers={
                "X-Audio-Seconds": f"{res.audio_s:.2f}",
                "X-Chunks": str(res.chunks),
                "X-Latency-Ms": str(res.latency_ms),
            },
        )

    @app.get("/api/tts/history")
    async def tts_history_view(days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
        _tts_enabled()
        now = time.time()
        frm = now - days * 86400
        tz = settings.litellm_timezone_offset_min
        return {
            "recent": await asyncio.to_thread(tts_history.recent, 100),
            "daily": await asyncio.to_thread(tts_history.daily, frm, now, tz),
            "availability": await asyncio.to_thread(
                decision_history.availability, frm, now, "qwen-tts"
            ),
        }

    @app.get("/api/tts/usage")
    async def tts_usage(
        days: int = Query(30, ge=1, le=90), group: Literal["day", "client"] = "day"
    ) -> dict[str, Any]:
        _tts_enabled()
        now = time.time()
        names = {str(c["id"]): c["name"] for c in await asyncio.to_thread(decision_keys.list)}
        rows = await asyncio.to_thread(
            tts_history.usage,
            now - days * 86400,
            now,
            settings.litellm_timezone_offset_min,
            group,
            names,
        )
        return {
            "rows": rows,
            "gateway": f":{settings.decision_gateway_port}",
            "require_key": settings.decision_gateway_require_key,
        }

    # --- embedding service (M8) ---------------------------------------------------------
    def _embed_enabled() -> None:
        if not settings.embed_enabled:
            raise HTTPException(404, "the embedding service is not enabled")

    async def _service_view(spec: SidecarSpec) -> dict[str, Any]:
        proc = sidecar_registry.process(spec.name)
        st = await asyncio.to_thread(proc.status)
        ready = st.running and st.owned and await asyncio.to_thread(proc.ready_sync)
        cli = await asyncio.to_thread(proc.cli_status)
        try:
            budget = _budget_view(await control.service_budget(spec.name, app.state.sampler_pressure()))
        except ControlError as exc:
            budget = {"ok": False, "reasons": [exc.message], "lines": [],
                      "total_gb": None, "limit_gb": None}
        intent = await asyncio.to_thread(read_service_intent, store, spec)
        availability = await _service_availability(st.running, ready, intent)
        if st.running and not st.owned:
            availability.update(available=False, state="failed", reason="Process ownership is unknown.")
        elif st.running and not ready and not runner.running():
            availability.update(available=False, state="failed", reason="Service or dependency is not ready; inspect logs.")
        return {
            "name": spec.name,
            "label": spec.label,
            "title": spec.title,
            "running": st.running,
            "pid": st.pid,
            "ready": ready,
            "paused": availability["paused"],
            "availability": availability,
            "checked_at": time.time(),
            "intent": intent or "stopped",
            "rss_bytes": proc.rss_bytes(st.pid),
            "listen": spec.api_base,
            "weights": cli["weights"],
            "allowed_mlx": sorted(spec.allowed_mlx),
            "models": [m.id for m in spec.models],
            "capabilities": list(dict.fromkeys(m.capability for m in spec.models)),
            "ui_path": spec.ui_path,
            "log_sources": list(spec.log_sources),
            "reservation_gb": spec.footprint_gb or None,
            "enabled": spec.enabled,
            "busy": sidecar_registry.busy(spec.name),
            "history_url": f"/api/services/{spec.name}/history",
            "budget": budget,
        }

    @app.get("/api/services")
    async def services_view() -> dict[str, Any]:
        running = {s.name for s in await asyncio.to_thread(sidecar_registry.services_running)}
        return {"services": [await _service_view(s) for s in sidecar_registry.services(enabled_only=False)
                             if s.enabled or s.name in running]}

    @app.post("/api/services/{name}/start")
    async def service_start(name: str) -> JSONResponse:
        return await _control(control.service_start(name, app.state.sampler_pressure()))

    @app.post("/api/services/{name}/stop")
    async def service_stop(name: str, force: bool = False) -> JSONResponse:
        return await _control(control.service_stop(name, force=force))

    @app.get("/api/services/{name}/history")
    async def service_history_view(name: str, days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
        try:
            sidecar_registry.get_service(name, enabled_only=False)
        except KeyError:
            raise HTTPException(404, "unknown service") from None
        now = time.time()
        start = now - days * 86400
        return {
            "recent": await asyncio.to_thread(service_history.recent, name),
            "daily": await asyncio.to_thread(
                service_history.daily, name, start, now, settings.litellm_timezone_offset_min,
            ),
            "availability": await asyncio.to_thread(decision_history.availability, start, now, name),
        }

    @app.get("/api/ocr")
    async def ocr_status() -> dict[str, Any]:
        if not settings.ocr_enabled:
            raise HTTPException(404, "the OCR service is not enabled")
        return {
            "model": settings.ocr_model_id,
            "reason": await asyncio.to_thread(ocr_reason),
            "admission": ocr.admission, "limits": dataclasses.asdict(ocr.limits),
        }

    if settings.ocr_enabled:
        install_ocr_routes(
            app, settings, ocr, ocr_reason, service_history,
            native_path="/api/ocr/recognize", source="admin-ui",
        )

    @app.get("/api/embed")
    async def embed_status() -> dict[str, Any]:
        _embed_enabled()
        reason = await asyncio.to_thread(embed_reason)
        proc = sidecar_registry.process("qwen-embed")
        ready = reason is None and await asyncio.to_thread(proc.ready_sync)
        spec = sidecar_registry.get_service("qwen-embed")
        intent = await asyncio.to_thread(read_service_intent, store, spec)
        availability = await _service_availability(reason is None, ready, intent)
        return {"availability": availability, "checked_at": time.time(),
            "enabled": True,
            "running": reason is None,
            "ready": ready,
            "reason": reason,
            "embed_model_id": settings.embed_model_id,
            "rerank_model_id": settings.rerank_model_id,
            "dims": settings.embed_dims,
            "max_inputs": settings.embed_max_inputs,
            "max_input_chars": settings.embed_max_input_chars,
            "max_documents": settings.rerank_max_documents,
            "gateway": f":{settings.decision_gateway_port}",
            "require_key": settings.decision_gateway_require_key,
        }

    async def _admin_embed_call(route: str, inputs: int, chars: int, call: Any) -> Any:
        """One request through the admin (the test page), sharing the gateway's pool."""
        t0 = time.perf_counter()

        async def record(status: str, code: int | None, dims: int | None = None) -> None:
            await asyncio.to_thread(
                embed_history.add_request,
                ts=time.time(),
                source="admin-ui",
                route=route,
                inputs=inputs,
                chars=chars,
                tokens=None,
                dims=dims,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                status=status,
                error_code=code,
            )

        reason = await asyncio.to_thread(embed_reason)
        if reason is not None:
            await record("error", 503)
            raise HTTPException(503, f"the embedding service is {reason.replace('_', ' ')}")
        if not embed.try_acquire():
            await record("error", 429)
            raise HTTPException(429, "the embedding service is busy; retry in a second")
        try:
            res = await call()
        except EmbedError as exc:
            await record("error", exc.code)
            raise HTTPException(exc.code, exc.message) from None
        finally:
            embed.release()
        await record("ok", None, getattr(res, "dims", None))
        return res

    @app.post("/api/embed/similarity")
    async def embed_similarity(body: SimilarityRequest) -> dict[str, Any]:
        _embed_enabled()
        texts = list(body.texts)
        if body.query_first:
            texts[0] = QUERY_INSTRUCTION + texts[0]
        try:
            req = validate_embed({"model": settings.embed_model_id, "input": texts}, settings)
        except EmbedError as exc:
            raise HTTPException(exc.code, exc.message) from None
        res = await _admin_embed_call(
            "embed", len(texts), sum(len(t) for t in texts), lambda: embed.embed(req)
        )
        # normalised here, so the page always shows cosine similarity
        vs = [[x / (sum(v * v for v in vec) ** 0.5) for x in vec] for vec in res.vectors]
        matrix = [[round(sum(a * b for a, b in zip(x, y, strict=True)), 4) for y in vs] for x in vs]
        return {"dims": res.dims, "matrix": matrix}

    @app.post("/api/embed/rerank")
    async def embed_rerank(body: AdminRerankRequest) -> dict[str, Any]:
        _embed_enabled()
        try:
            req = validate_rerank(
                {
                    "model": settings.rerank_model_id,
                    "query": body.query,
                    "documents": body.documents,
                    **({"instruction": body.instruction} if body.instruction else {}),
                },
                settings,
            )
        except EmbedError as exc:
            raise HTTPException(exc.code, exc.message) from None
        chars = len(body.query) + sum(len(d) for d in body.documents)
        res = await _admin_embed_call(
            "rerank", len(body.documents), chars, lambda: embed.rerank(req)
        )
        return {"results": res.results}

    @app.get("/api/embed/history")
    async def embed_history_view(days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
        _embed_enabled()
        now = time.time()
        frm = now - days * 86400
        tz = settings.litellm_timezone_offset_min
        return {
            "recent": await asyncio.to_thread(embed_history.recent, 100),
            "daily": await asyncio.to_thread(embed_history.daily, frm, now, tz),
            "availability": await asyncio.to_thread(
                decision_history.availability, frm, now, "qwen-embed"
            ),
        }

    @app.get("/api/embed/usage")
    async def embed_usage(
        days: int = Query(30, ge=1, le=90), group: Literal["day", "client"] = "day"
    ) -> dict[str, Any]:
        _embed_enabled()
        now = time.time()
        names = {str(c["id"]): c["name"] for c in await asyncio.to_thread(decision_keys.list)}
        rows = await asyncio.to_thread(
            embed_history.usage,
            now - days * 86400,
            now,
            settings.litellm_timezone_offset_min,
            group,
            names,
        )
        return {
            "rows": rows,
            "gateway": f":{settings.decision_gateway_port}",
            "require_key": settings.decision_gateway_require_key,
        }

    # --- diagnostics & alerts ------------------------------------------------
    @app.get("/api/diagnostics")
    async def diagnostics_list() -> dict[str, Any]:
        return {"checks": await diagnostics.run()}

    @app.get("/api/alerts")
    async def alerts_list(
        state: Literal["open", "all"] = "open",
        limit: int = Query(50, ge=1, le=200),
        before: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        rows = await asyncio.to_thread(alerts.list, state, limit, before)
        return {"alerts": rows, "summary": await asyncio.to_thread(alerts.summary)}

    @app.post("/api/alerts/ack-all")
    async def alerts_ack_all() -> dict[str, int]:
        return {"acked": await asyncio.to_thread(alerts.ack_all, time.time())}

    @app.post("/api/alerts/{alert_id}/ack")
    async def alerts_ack(alert_id: int) -> dict[str, bool]:
        if not await asyncio.to_thread(alerts.ack, alert_id, time.time()):
            raise HTTPException(404, "unknown alert")
        return {"acked": True}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job

    @app.get("/api/history")
    async def history(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        return {"history": store.history(limit)}

    # --- quality runs ------------------------------------------------------------
    def _quality_error(exc: QualityError) -> HTTPException:
        return HTTPException(exc.code, exc.message)

    @app.post("/api/quality/runs")
    async def quality_start(body: QualityRequest) -> JSONResponse:
        try:
            run = await quality.start(
                body.kind,
                body.scope,
                body.model,
                confirm_off=body.confirm_off,
                suite=body.suite,
                thinking=body.thinking,
            )
        except QualityError as exc:
            raise _quality_error(exc) from None
        return JSONResponse(run, status_code=202)

    @app.get("/api/quality/runs")
    async def quality_runs(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
        return {
            "runs": await asyncio.to_thread(quality_store.list_runs, limit),
            "active": quality.active(),
        }

    @app.get("/api/quality/runs/{run_id}")
    async def quality_run(run_id: str) -> dict[str, Any]:
        run = await asyncio.to_thread(quality_store.get_run, run_id, True)
        if run is None:
            raise HTTPException(404, "unknown quality run")
        return run

    @app.post("/api/quality/runs/{run_id}/cancel")
    async def quality_cancel(run_id: str) -> JSONResponse:
        try:
            return JSONResponse(await quality.cancel(run_id), status_code=202)
        except QualityError as exc:
            raise _quality_error(exc) from None

    @app.get("/api/quality/latest")
    async def quality_latest() -> dict[str, Any]:
        return {
            "compatibility": await asyncio.to_thread(quality_store.latest_compatibility),
            "benchmark_cases": [dataclasses.asdict(c) for c in quality_mod.BENCHMARK_CASES],
            "compatibility_cases": list(compatibility.CASES),
            "settings": quality_mod.EFFECTIVE_SETTINGS,
            "warmup": quality_mod.WARMUP,
            "repeats": quality_mod.REPEATS,
        }

    # --- evals (M10) -------------------------------------------------------------
    @app.get("/api/evals/suites")
    async def eval_suites_list() -> dict[str, Any]:
        return {"suites": await asyncio.to_thread(evals.suites)}

    async def _suite_or_404(suite_id: str, version: int | None = None) -> Suite:
        suite = await asyncio.to_thread(evals.suite, suite_id, version)
        if suite is None:
            raise HTTPException(404, "unknown eval suite")
        return suite

    @app.get("/api/evals/suites/{suite_id}")
    async def eval_suite(suite_id: str, version: int | None = None) -> dict[str, Any]:
        return (await _suite_or_404(suite_id, version)).model_dump(mode="json")

    @app.get("/api/evals/suites/{suite_id}/export")
    async def eval_suite_export(suite_id: str) -> Response:
        suite = await _suite_or_404(suite_id)
        if suite.needle is not None:
            raise HTTPException(400, "generated suite: nothing to export")
        lines = []
        for case in suite.cases:
            doc = case.model_dump(mode="json")
            lines.append(json.dumps({k: v for k, v in doc.items() if v is not None}))
        return Response(
            "\n".join(lines) + "\n",
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{suite.id}.jsonl"'},
        )

    @app.post("/api/evals/suites", status_code=201)
    async def eval_suite_import(
        file: UploadFile = File(...),  # noqa: B008
        id: str = Form(...),  # noqa: A002
        title: str = Form(...),
    ) -> dict[str, Any]:
        raw = await file.read(settings.eval_import_max_bytes + 1)
        if len(raw) > settings.eval_import_max_bytes:
            raise HTTPException(400, f"file is larger than {settings.eval_import_max_bytes} bytes")
        try:
            text = raw.decode()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", id):
                raise SuiteError("id must be lowercase letters, digits, '_', '.' or '-'")
            suite = parse_jsonl(
                text,
                id=id,
                title=title,
                max_bytes=settings.eval_import_max_bytes,
                max_cases=settings.eval_import_max_cases,
            )
        except UnicodeDecodeError:
            raise HTTPException(400, "file is not UTF-8 text") from None
        except SuiteError as exc:
            raise HTTPException(400, exc.message) from None
        try:
            await asyncio.to_thread(evals.add_custom, suite)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        stored = await _suite_or_404(id)
        return {"id": stored.id, "version": stored.version, "cases": len(stored.cases)}

    @app.delete("/api/evals/suites/{suite_id}", status_code=204)
    async def eval_suite_delete(suite_id: str) -> Response:
        if await asyncio.to_thread(evals.delete_custom, suite_id) == 0:
            if await asyncio.to_thread(evals.suite, suite_id) is None:
                raise HTTPException(404, "unknown eval suite")
            raise HTTPException(409, "a builtin suite cannot be deleted")
        return Response(status_code=204)

    @app.post("/api/evals/estimate")
    async def eval_estimate(body: EvalEstimateRequest) -> dict[str, Any]:
        suite = await _suite_or_404(body.suite)
        try:
            plan = await quality.plan_targets(body.scope, body.model)
        except QualityError as exc:
            raise _quality_error(exc) from None
        cases = suite.cases
        if suite.needle is not None:  # generated cases depend on the running engine's context
            ctx = await quality.current_context(plan)
            cases = await asyncio.to_thread(expand, suite, ctx)
        out = []
        for name in plan.targets:
            decode, prefill, basis = DEFAULT_DECODE_TOK_S, DEFAULT_PREFILL_TOK_S, "default"
            fp = (await asyncio.to_thread(model_info, plan.dirs.get(name)))["fingerprint"]
            base = fp and await asyncio.to_thread(quality_store.latest_baseline, fp, "p2k")
            if base:
                med = base["medians"]
                decode = med.get("backend_decode_tok_s") or decode
                prefill = med.get("backend_prefill_tok_s") or prefill
                basis = "benchmark"
            seconds = 0.0 if name == plan.original["name"] else MODEL_SWITCH_S
            for case in cases:
                max_tokens = settings.eval_thinking_max_tokens if body.thinking else case.max_tokens
                out_tokens = (
                    max_tokens / 2 if isinstance(case.grader, Judge) else min(max_tokens, 64)
                )
                prompt_chars = sum(len(m["content"]) for m in messages_for(case))
                seconds += prompt_chars / 4 / prefill + out_tokens / decode
            out.append({"name": name, "seconds": round(seconds, 1), "basis": basis})
        try:
            target = judge_target(settings)
            judge = target.label if target else "none"
        except ValueError as exc:
            judge = f"invalid judge setting: {exc}"
        return {"models": out, "judge": judge}

    @app.get("/api/evals/runs/{run_id}/results")
    async def eval_results(run_id: str, model: str | None = None) -> dict[str, Any]:
        return {"results": await asyncio.to_thread(evals.results, run_id, model)}

    def _regrade(run_id: str) -> dict[str, Any]:
        rows = evals.results(run_id)
        if not rows:
            raise HTTPException(404, "no eval results for this run")
        suites: dict[tuple[str, str], Any] = {}
        for r in rows:
            key = (r["suite_id"], r["suite_hash"])
            if key not in suites:
                suites[key] = evals.suite_by_hash(*key)
            if suites[key] is None:
                raise HTTPException(409, f"suite {key[0]} version of this run is no longer stored")
        # judged and failed rows keep what they have: only deterministic graders re-run
        promoted = {
            (r["model"], r["suite_id"])
            for r in rows
            if r["fingerprint"] and evals.is_promoted(run_id, r["fingerprint"], r["suite_id"])
        }
        regraded = changed = 0
        by_suite: dict[tuple[str, str], dict[str, EvalCase]] = {}
        for r in rows:
            key = (r["suite_id"], r["suite_hash"])
            if key not in by_suite:  # a needle suite's cases are generated, so build them once
                suite = suites[key]
                biggest = max(suite.needle.lengths) + NEEDLE_MARGIN if suite.needle else None
                by_suite[key] = {c.id: c for c in expand(suite, biggest)}
            case = by_suite[key].get(r["case_id"])
            if case is None or isinstance(case.grader, Judge) or r["grade_state"] == "failed":
                continue
            calls = json.loads(r["tool_calls"]) if r["tool_calls"] else None
            g = grade_response(
                case.grader, r["response"], r["finish_reason"], r["reasoning_chars"] or 0, calls,
                thinking=bool(r["thinking"]),
            )  # fmt: skip
            regraded += 1
            new = (g.get("score"), g.get("passed"), g["grade_state"], g.get("grade_detail"))
            if new != (r["score"], r["passed"], r["grade_state"], r["grade_detail"]):
                changed += 1
                evals.update_grade(
                    r["id"], score=new[0], passed=None if new[1] is None else bool(new[1]),
                    state=new[2], detail=new[3],
                )  # fmt: skip
        summaries = []
        for model in dict.fromkeys(r["model"] for r in rows):
            suite_id = next(r["suite_id"] for r in rows if r["model"] == model)
            summary = evals.finalize(run_id, model, promote=(model, suite_id) in promoted)
            case_row = next(r["quality_case_id"] for r in rows if r["model"] == model)
            if case_row is not None:  # status, detail and finished stay as the run left them
                quality_store.set_case_summary(case_row, summary)
            summaries.append(summary)
        return {"run_id": run_id, "regraded": regraded, "changed": changed, "models": summaries}

    @app.post("/api/evals/runs/{run_id}/regrade")
    async def eval_regrade(run_id: str) -> dict[str, Any]:
        active = quality.active()
        if active is not None and active["run_id"] == run_id:
            raise HTTPException(409, "the run is still in progress")
        return await asyncio.to_thread(_regrade, run_id)

    @app.get("/api/evals/scorecard")
    async def eval_scorecard() -> dict[str, Any]:
        cards = await asyncio.to_thread(evals.scorecard)
        for card in cards:
            base = await asyncio.to_thread(
                quality_store.latest_baseline, card["fingerprint"], "p2k"
            )
            med = base["medians"] if base else {}
            rss = med.get("peak_rss_bytes")
            card["speed"] = (
                {
                    "decode_tok_s": med.get("backend_decode_tok_s"),
                    "prefill_tok_s": med.get("backend_prefill_tok_s"),
                    "peak_rss_gb": None if rss is None else round(rss / GIB, 2),
                }
                if base
                else None
            )  # Strata rows are keyed strata-<hash> and have no baseline, so their speed is null
        return {"models": cards}

    @app.get("/api/evals/compare")
    async def eval_compare(
        suite: str, a_run: str, a_model: str, b_run: str, b_model: str
    ) -> dict[str, Any]:
        def load(run: str, model: str) -> list[dict[str, Any]]:
            rows = [r for r in evals.results(run, model) if r["suite_id"] == suite]
            if not rows:
                raise HTTPException(404, "no eval results for that run, model and suite")
            return rows

        rows_a = await asyncio.to_thread(load, a_run, a_model)
        rows_b = await asyncio.to_thread(load, b_run, b_model)
        for field, what in (
            ("suite_hash", "suite version"),
            ("settings_hash", "generation settings (thinking, profile or runtime)"),
        ):
            if {r[field] for r in rows_a} != {r[field] for r in rows_b}:
                raise HTTPException(409, f"not comparable: {what} differs")
        # same suite version on both sides (checked above), so one lookup names its judge cases
        stored = await asyncio.to_thread(evals.suite_by_hash, suite, rows_a[0]["suite_hash"])
        judged_ids = (
            {c.id for c in stored.cases if isinstance(c.grader, Judge)} if stored else set()
        )
        extra: dict[str, Any] = {}
        if judged_ids:

            def judged(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    r for r in rows if r["case_id"] in judged_ids and r["grade_state"] == "graded"
                ]

            ja, jb = judged(rows_a), judged(rows_b)
            if {r["judge"] for r in ja} != {r["judge"] for r in jb}:
                raise HTTPException(409, "not comparable: judge differs")
            own = {"a": any(r["self_judged"] for r in ja), "b": any(r["self_judged"] for r in jb)}
            if any(own.values()):
                extra["self_judged"] = own
        a = await asyncio.to_thread(evals.case_scores, a_run, a_model, suite)
        b = await asyncio.to_thread(evals.case_scores, b_run, b_model, suite)
        try:
            result = paired(a, b)
        except ValueError:
            raise HTTPException(409, "not comparable: no shared cases") from None
        return (
            dataclasses.asdict(result)
            | extra
            | {
                "disagreements": [
                    {"case_id": k, "a_score": a[k], "b_score": b[k]}
                    for k in sorted(a.keys() & b.keys())
                    if a[k] != b[k]
                ]
            }
        )

    # --- chat --------------------------------------------------------------
    @app.get("/api/chat/config")
    async def chat_config() -> dict[str, Any]:
        try:
            descriptor = await control.backends.resolve_inference(profiles.chat_defaults)
        except BackendUnavailable as exc:
            raise HTTPException(409, str(exc)) from None
        return {"identity": dict(descriptor.identity), "defaults": effective_chat(None, dict(descriptor.defaults)),
                "output_cap": CHAT_OUTPUT_CAP, "checked_at": time.time()}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        if quality.active():
            raise HTTPException(409, "a quality run is in progress; test chat is paused")
        try:
            descriptor = await control.backends.resolve_inference(profiles.chat_defaults)
        except BackendUnavailable as exc:
            raise HTTPException(409, str(exc)) from None
        return StreamingResponse(
            stream_chat(
                settings,
                req,
                transport=chat_transport,
                defaults=dict(descriptor.defaults),
                target=descriptor.target,
                estimate_usage=descriptor.estimate_usage,
                identity=descriptor.identity,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- logs --------------------------------------------------------------
    def _source(source_id: str) -> Path:
        try:
            return resolve_source(settings, source_id)
        except KeyError:
            raise HTTPException(404, "unknown log source") from None

    @app.get("/api/logs")
    async def log_sources() -> dict[str, Any]:
        return {
            "sources": [
                {"id": s, **file_info(resolve_source(settings, s))} for s in source_ids(settings)
            ]
        }

    @app.get("/api/logs/{source_id}")
    async def log_tail(
        source_id: str,
        lines: int = Query(500, ge=1, le=5000),
        grep: str | None = Query(None, max_length=200),
        level: Literal["INFO", "WARNING", "ERROR"] | None = None,
    ) -> dict[str, Any]:
        path = _source(source_id)
        # read extra lines when filtering so the result is not nearly empty
        raw = await asyncio.to_thread(tail_lines, path, lines * 5 if grep or level else lines)
        return {
            "id": source_id,
            **file_info(path),
            "lines": filter_lines(raw, grep, level)[-lines:],
        }

    @app.get("/api/downloads")
    async def downloads() -> dict[str, Any]:
        out = []
        for sid in download_log_ids(settings):
            path = resolve_source(settings, sid)
            lines = await asyncio.to_thread(tail_lines, path, 5000)
            out.append({"id": sid, **file_info(path), **download_summary(lines)})
        return {"logs": out}

    # --- model downloads (M3b) --------------------------------------------------------
    async def _dl(fn: Callable[..., dict[str, Any]], *args: Any, code: int = 200) -> JSONResponse:
        try:
            return JSONResponse(await asyncio.to_thread(fn, *args), status_code=code)
        except DownloadError as exc:
            raise HTTPException(exc.code, exc.message) from None

    @app.post("/api/downloads/preview")
    async def download_preview(body: DownloadPreviewRequest) -> JSONResponse:
        return await _dl(downloads_svc.preview, body.repo, body.revision)

    @app.post("/api/downloads")
    async def download_start(body: DownloadStartRequest) -> JSONResponse:
        return await _dl(downloads_svc.start, body.repo, body.revision, body.dest, code=202)

    @app.get("/api/downloads/runs")
    async def download_runs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        return {
            "runs": await asyncio.to_thread(downloads_svc.runs, limit),
            "allowed_owners": settings.download_allowed_owners,
        }

    @app.get("/api/downloads/{run_id}")
    async def download_get(run_id: str) -> JSONResponse:
        if not RUN_ID_RE.fullmatch(run_id):
            raise HTTPException(400, "invalid run id")
        return await _dl(downloads_svc.refresh, run_id)

    @app.post("/api/downloads/{run_id}/cancel")
    async def download_cancel(run_id: str) -> JSONResponse:
        return await _dl(downloads_svc.cancel, run_id, code=202)

    @app.post("/api/downloads/{run_id}/resume")
    async def download_resume(run_id: str) -> JSONResponse:
        return await _dl(downloads_svc.resume, run_id, code=202)

    @app.get("/api/logs/{source_id}/download")
    async def log_download(source_id: str, lines: int = Query(5000, ge=1, le=5000)) -> Response:
        path = _source(source_id)
        text = "".join(f"{line}\n" for line in await asyncio.to_thread(tail_lines, path, lines))
        return PlainTextResponse(
            text, headers={"Content-Disposition": f'attachment; filename="{source_id}.log"'}
        )

    @app.get("/api/logs/{source_id}/follow")
    async def log_follow(
        source_id: str, request: Request,
        grep: str | None = Query(None, max_length=200),
        level: Literal["INFO", "WARNING", "ERROR"] | None = None,
    ) -> StreamingResponse:
        path = _source(source_id)

        async def events() -> AsyncIterator[str]:
            follower = Follower(path)
            yield _sse({"type": "info", **file_info(path)})
            deadline = time.monotonic() + app.state.follow_max_s
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    break
                raw = await asyncio.to_thread(follower.poll)
                for line in filter_lines(raw, grep, level):
                    yield _sse({"type": "line", "line": line})
                await asyncio.sleep(app.state.follow_interval_s)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    # --- usage -------------------------------------------------------------
    def _range(frm: str | None, to: str | None) -> Any:
        try:
            return validate_range(frm, to, today=usage.today())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def _usage_error(exc: UsageError) -> JSONResponse:
        log.warning("usage request failed: %s", exc.message)
        code = 503 if exc.kind == "config" else 502
        return JSONResponse({"error": exc.kind, "message": exc.message}, status_code=code)

    @app.get("/api/usage/daily", response_model=None)
    async def usage_daily(
        frm: str | None = Query(None, alias="from"), to: str | None = None
    ) -> dict[str, Any] | JSONResponse:
        start, end = _range(frm, to)
        try:
            out = await usage.daily(start, end)
        except UsageError as exc:
            return _usage_error(exc)
        active = store.active_by_day(
            [d["date"] for d in out["days"]], settings.litellm_timezone_offset_min
        )
        out["days"] = [
            {
                **d,
                "active_models": active.get(d["date"], []),
                # LiteLLM sees one route: tokens belong to a model only if it was the
                # only one active that day; mixed days stay unallocated (M3a)
                "attributed_model": (
                    active[d["date"]][0] if len(active.get(d["date"], [])) == 1 else None
                ),
            }
            for d in out["days"]
        ]
        return {**out, "tz_offset_min": settings.litellm_timezone_offset_min}

    @app.get("/api/usage/sessions", response_model=None)
    async def usage_sessions(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        page: int = Query(1, ge=1, le=10_000),
    ) -> dict[str, Any] | JSONResponse:
        start, end = _range(frm, to)
        try:
            out = await usage.sessions(start, end, page=page)
            return {**out, "tz_offset_min": settings.litellm_timezone_offset_min}
        except UsageError as exc:
            return _usage_error(exc)

    @app.get("/api/usage/health")
    async def usage_health() -> dict[str, Any]:
        return await usage.health()

    # --- performance & availability (M4) -------------------------------------------
    def _epoch_range(frm: str | None, to: str | None) -> tuple[date, date, float, float]:
        start, end = _range(frm, to)
        tz = ops_tz(settings.litellm_timezone_offset_min)
        lo = datetime.combine(start, datetime.min.time(), tz).timestamp()
        hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz).timestamp()
        return start, end, lo, hi

    @app.get("/api/performance/summary")
    async def performance_summary(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        model: str | None = Query(None, max_length=200),
        source: Literal["mlx-perf", "strata"] | None = None,
    ) -> dict[str, Any]:
        start, end, lo, hi = _epoch_range(frm, to)
        out = await asyncio.to_thread(
            perf.summary,
            lo,
            hi,
            tz_offset_min=settings.litellm_timezone_offset_min,
            slow_ttft_s=settings.perf_slow_ttft_s,
            model=model,
            source=source,
        )
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "tz_offset_min": settings.litellm_timezone_offset_min,
            "models": await asyncio.to_thread(perf.models, lo, hi, source=source),
            "source": {
                "source": source or PERF_SOURCE,
                "includes": [source] if source else [PERF_SOURCE, "strata"],
                "scope": "Recorded engine requests only; excludes Splash native snapshots and LiteLLM gateway totals.",
                "parser": PARSER if source in (None, PERF_SOURCE) else "Strata request metrics" if source == "strata" else "MLX logs + Strata request metrics",
                "last_ingest": await asyncio.to_thread(perf.last_ingest, source or PERF_SOURCE),
            },
            "coverage": {
                "splash": await asyncio.to_thread(
                    native_telemetry.coverage,
                    settings.splash_enabled,
                    now=time.time(),
                    stale_s=settings.splash_poll_interval_s * 3,
                )
            },
            **out,
        }

    @app.get("/api/performance/tokens")
    async def performance_tokens(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        model: str | None = Query(None, max_length=200),
        source: Literal["mlx-perf", "strata"] | None = None,
        origin: Literal["client", "quality"] | None = None,
    ) -> dict[str, Any]:
        start, end, lo, hi = _epoch_range(frm, to)
        tz = settings.litellm_timezone_offset_min

        def both() -> tuple[dict[str, Any], dict[str, Any]]:
            kw: dict[str, Any] = {"tz_offset_min": tz, "model": model, "origin": origin, "source": source}
            return perf.tokens(lo, hi, group="day", **kw), perf.tokens(lo, hi, group="model", **kw)

        days, models = await asyncio.to_thread(both)
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "tz_offset_min": tz,
            "days": days["rows"],
            "models": models["rows"],
            "totals": days["totals"],
            "coverage": {
                "splash": await asyncio.to_thread(
                    native_telemetry.coverage,
                    settings.splash_enabled,
                    now=time.time(),
                    stale_s=settings.splash_poll_interval_s * 3,
                )
            },
        }

    @app.get("/api/performance/engines")
    async def performance_engines(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        profile: str | None = Query(None, max_length=128),
        fingerprint: str | None = Query(None, max_length=128),
        limit: int = Query(200, ge=1, le=500),
        epoch: str | None = Query(None, max_length=128),
        before: str | None = Query(None, max_length=100),
    ) -> dict[str, Any]:
        start, end, lo, hi = _epoch_range(frm, to)
        try:
            page = await asyncio.to_thread(
                native_telemetry.page, lo, hi, profile=profile, fingerprint=fingerprint,
                epoch=epoch, limit=limit, before=before,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "tz_offset_min": settings.litellm_timezone_offset_min,
            "source": "splash",
            **page,
            "coverage": await asyncio.to_thread(
                native_telemetry.coverage,
                settings.splash_enabled,
                now=time.time(),
                stale_s=settings.splash_poll_interval_s * 3,
            ),
            "semantics": "Cumulative native execution snapshots; never sum polls or add to request/gateway totals.",
        }

    @app.get("/api/performance/requests")
    async def performance_requests(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        model: str | None = Query(None, max_length=200),
        source: Literal["mlx-perf", "strata"] | None = None,
        state: REQUEST_STATES | None = None,
        slow: bool = False,
        limit: int = Query(50, ge=1, le=200),
        before: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        _start, _end, lo, hi = _epoch_range(frm, to)
        return await asyncio.to_thread(
            lambda: perf.requests(
                lo,
                hi,
                model=model,
            source=source,
                state=state,
                min_ttft_s=settings.perf_slow_ttft_s if slow else None,
                limit=limit,
                before=before,
            )
        )

    @app.get("/api/performance/requests/{row_id}/context")
    async def performance_context(row_id: int) -> dict[str, Any]:
        row = await asyncio.to_thread(perf.get, row_id)
        if row is None:
            raise HTTPException(404, "unknown request")
        cursor = await asyncio.to_thread(events.cursor, PERF_SOURCE)
        # the drive can hang: bounded like every other probe, never a free path
        res = await probes.run(
            "perf-context",
            lambda: request_context(settings.mlx_log_dir, row, cursor),
            settings.probe_timeout_s,
        )
        if res.state != "done":
            return {"available": False, "lines": [], "reason": "the log could not be read"}
        value: dict[str, Any] = res.value
        return value

    @app.get("/api/availability")
    async def availability_view(
        frm: str | None = Query(None, alias="from"),
        to: str | None = None,
        source: Literal["mlx", "splash", "strata"] = "mlx",
        profile: str | None = Query(None, max_length=128),
    ) -> dict[str, Any]:
        start, end, lo, hi = _epoch_range(frm, to)
        interval = settings.availability_interval_s
        if source in {"splash", "strata"}:
            out = await asyncio.to_thread(
                backend_availability.query,
                source,
                lo,
                hi,
                interval_s=interval,
                tz_offset_min=settings.litellm_timezone_offset_min,
                now=time.time(),
                profile=profile,
            )
            return {"from": start.isoformat(), "to": end.isoformat(), "source": source,
                    "range_start": lo, "range_end": hi,
                    "tz_offset_min": settings.litellm_timezone_offset_min, **out}
        samples = await asyncio.to_thread(availability.samples, lo, hi, 2 * interval)
        out = summarize(
            samples,
            lo,
            hi,
            interval_s=interval,
            tz_offset_min=settings.litellm_timezone_offset_min,
            now=time.time(),
        )
        return {"from": start.isoformat(), "to": end.isoformat(), "source": source,
                    "range_start": lo, "range_end": hi,
                    "tz_offset_min": settings.litellm_timezone_offset_min, **out}

    @app.get("/api/maintenance/log-rotation")
    async def log_rotation_state() -> dict[str, Any]:
        return await asyncio.to_thread(maintenance.state)

    return app
