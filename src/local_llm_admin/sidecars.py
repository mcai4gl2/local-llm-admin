"""The sidecar slot (M7): small models that run next to the MLX chat model, one at a time,
never next to ComfyUI.

Each sidecar is a ``SidecarSpec`` built from Settings on every call (tests and the admin
change settings at runtime). The rules here are generic; each sidecar keeps its own
adapter (``decision.py``, ``tts.py``). Sidecars are driven only through their host CLI
and loopback HTTP; the admin never imports their packages or uses their venvs.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import psutil

from .config import Settings
from .mlx import MlxClient

if TYPE_CHECKING:
    from .backend_types import BackendLookup

_ACTIONS = ("start", "stop", "status")
_WEIGHTS_RE = re.compile(r"^weights:\s*(\S+)", re.M)


# --- memory budget ---


GIB = 1024**3


def kv_bytes_per_token(cfg: dict[str, Any]) -> int | None:
    """KV cache bytes per token at 16 bits, counting full-attention layers only.

    Linear-attention layers (Qwen3.5/3.6 MoE) keep a fixed-size state, and sliding-window
    layers a bounded one, so neither grows with context.
    """
    raw = cfg.get("text_config")
    t: dict[str, Any] = raw if isinstance(raw, dict) else cfg
    layers = t.get("num_hidden_layers")
    heads = t.get("num_attention_heads")
    kv_heads = t.get("num_key_value_heads") or heads
    head_dim = t.get("head_dim")
    if not head_dim and t.get("hidden_size") and heads:
        head_dim = t["hidden_size"] // heads
    if not (isinstance(layers, int) and isinstance(kv_heads, int) and isinstance(head_dim, int)):
        return None
    types = t.get("layer_types")
    if isinstance(types, list) and types:
        full = sum(1 for x in types if x == "full_attention")
    elif isinstance(t.get("full_attention_interval"), int) and t["full_attention_interval"] > 0:
        full = layers // t["full_attention_interval"]
    else:
        full = layers
    return full * kv_heads * head_dim * 2 * 2


def context_tokens(cfg: dict[str, Any], profile: dict[str, Any] | None) -> int | None:
    """The profile's ``max_kv_size`` if set, else the model's full context."""
    launch = (profile or {}).get("launch")
    if isinstance(launch, dict) and isinstance(launch.get("max_kv_size"), int):
        return int(launch["max_kv_size"])
    raw = cfg.get("text_config")
    t: dict[str, Any] = raw if isinstance(raw, dict) else cfg
    n = t.get("max_position_embeddings") or cfg.get("max_position_embeddings")
    return n if isinstance(n, int) else None


def mlx_estimate_gb(weights_bytes: int, cfg: dict[str, Any], tokens: int | None) -> float | None:
    per_token = kv_bytes_per_token(cfg)
    if per_token is None or tokens is None:
        return None
    return (weights_bytes + per_token * tokens) / GIB


def system_ram_gb() -> float:
    return float(psutil.virtual_memory().total) / GIB


def wired_limit_gb() -> float | None:
    """``iogpu.wired_limit_mb`` in GB; None when unset (0) or unreadable (not macOS)."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "iogpu.wired_limit_mb"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        mb = int(r.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return mb / 1024 if mb > 0 else None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class SidecarSpec:
    name: str  # CLI, job and step name: "openjev", "qwen-tts"
    label: str  # in messages: "the <label> sidecar"
    title: str  # card, check and alert title
    port: int
    allowed_mlx: frozenset[str] = frozenset()
    enabled: bool = True
    cli_path: Path = Path("/nonexistent")
    pid_file: Path = Path("/nonexistent")
    cmdline_marker: str = ""  # a pid whose command line lacks it is a reused pid
    api_base: str = ""
    ready_path: str = "/health"
    ready_timeout_s: float = 120.0
    footprint_gb: float = 0.0
    down_alert_key: str = ""
    weights_hint: str = ""
    kind: str = "sidecar"  # "sidecar" (the one-at-a-time slot) or "service" (always on, M8)
    intent_key: str = ""  # services keep their own kv intent; slot sidecars share INTENT_KEY

    @property
    def noun(self) -> str:
        return f"the {self.label} {self.kind}"

    @property
    def check_id(self) -> str:
        return self.label.lower()

    def allows(self, mlx_name: str | None) -> bool:
        return mlx_name in self.allowed_mlx


@dataclass(frozen=True)
class SidecarStatus:
    running: bool
    pid: int | None


def _names(csv: str) -> frozenset[str]:
    return frozenset(x.strip() for x in csv.split(",") if x.strip())


def openjev_spec(s: Settings) -> SidecarSpec:
    return SidecarSpec(
        name="openjev",
        label="decision",
        title="Decision sidecar",
        port=s.decision_port,
        allowed_mlx=_names(s.decision_allowed_mlx),
        enabled=s.decision_enabled,
        cli_path=s.decision_path,
        pid_file=s.decision_root / "run" / "openjev.pid",
        cmdline_marker="openjev",
        api_base=s.decision_api_base,
        ready_path="/health",
        ready_timeout_s=s.decision_ready_timeout_s,
        footprint_gb=s.decision_footprint_gb,
        down_alert_key="decision_down",
        weights_hint="Finish the DiffusionGemma download under decision/models/.",
    )


def tts_spec(s: Settings) -> SidecarSpec:
    return SidecarSpec(
        name="qwen-tts",
        label="TTS",
        title="TTS sidecar",
        port=s.tts_port,
        allowed_mlx=_names(s.tts_allowed_mlx),
        enabled=s.tts_enabled,
        cli_path=s.tts_path,
        pid_file=s.tts_root / "run" / "qwen-tts.pid",
        cmdline_marker="mlx_audio",  # Phase 0 fallback: "tts/app/server.py"
        api_base=s.tts_api_base,
        ready_path=s.tts_ready_path,
        ready_timeout_s=s.tts_ready_timeout_s,
        footprint_gb=s.tts_footprint_gb,
        down_alert_key="tts_down",
        weights_hint="Finish the Qwen3-TTS download under tts/models/.",
    )


# One builder per sidecar, in display order.
BUILDERS: list[Callable[[Settings], SidecarSpec]] = [openjev_spec, tts_spec]

INTENT_KEY = "sidecar_intent"  # {"name", "value": "running"|"stopped", "job_id", "ts"}


def read_intent(store: Any) -> dict[str, Any] | None:
    value = store.get_kv(INTENT_KEY)
    return value if isinstance(value, dict) else None


def write_intent(store: Any, name: str, value: str, job: dict[str, Any] | None = None) -> None:
    store.set_kv(
        INTENT_KEY,
        {
            "name": name,
            "value": value,
            "job_id": job["id"] if job else None,
            "ts": time.time(),
        },
    )


def embed_spec(s: Settings) -> SidecarSpec:
    return SidecarSpec(
        name="qwen-embed",
        label="embeddings",
        title="Embeddings service",
        port=s.embed_port,
        allowed_mlx=_names(s.embed_allowed_mlx),
        enabled=s.embed_enabled,
        cli_path=s.embed_path,
        pid_file=s.embed_root / "run" / "qwen-embed.pid",
        cmdline_marker=s.embed_cmdline_marker,
        api_base=s.embed_api_base,
        ready_path=s.embed_ready_path,
        ready_timeout_s=s.embed_ready_timeout_s,
        footprint_gb=s.embed_footprint_gb,
        down_alert_key="embed_down",
        weights_hint="Finish the Qwen3-Embedding and Qwen3-Reranker downloads under embed/models/.",
        kind="service",
        intent_key="embed_intent",
    )


# Always-on services (M8): never slot occupants; they run next to whichever sidecar holds
# the slot, stop for ComfyUI, and keep their own intent ({"value", "job_id", "ts"}).
SERVICE_BUILDERS: list[Callable[[Settings], SidecarSpec]] = [embed_spec]


def read_service_intent(store: Any, spec: SidecarSpec) -> str | None:
    value = store.get_kv(spec.intent_key)
    return value.get("value") if isinstance(value, dict) else None


def write_service_intent(
    store: Any, spec: SidecarSpec, value: str, job: dict[str, Any] | None = None
) -> None:
    store.set_kv(
        spec.intent_key, {"value": value, "job_id": job["id"] if job else None, "ts": time.time()}
    )


class SidecarProcess:
    """One sidecar's process, CLI and health, seen from the admin."""

    def __init__(
        self,
        spec_fn: Callable[[], SidecarSpec],
        transport: httpx.MockTransport | None,
        cli_timeout_s: float,
    ) -> None:
        self._spec_fn = spec_fn
        self.transport = transport
        self._cli_timeout_s = cli_timeout_s

    @property
    def spec(self) -> SidecarSpec:
        return self._spec_fn()

    def argv(self, action: str) -> list[str]:
        if action not in _ACTIONS:
            raise ValueError(f"unsupported {self.spec.name} action: {action}")
        return [str(self.spec.cli_path), action]

    def pid_from_file(self) -> int | None:
        try:
            return int(self.spec.pid_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def status(self) -> SidecarStatus:
        """The pid file's process only if its command line has the marker, else the port."""
        spec = self.spec
        pid = self.pid_from_file()
        if pid is not None:
            try:
                if spec.cmdline_marker in " ".join(psutil.Process(pid).cmdline()):
                    return SidecarStatus(True, pid)
            except (psutil.Error, OSError):
                pass
        return SidecarStatus(MlxClient.port_listening(spec.port), None)

    def down_sync(self) -> bool:
        return not self.status().running

    def ready_sync(self) -> bool:
        spec = self.spec
        try:
            with httpx.Client(base_url=spec.api_base, timeout=2.0, transport=self.transport) as h:
                return h.get(spec.ready_path).status_code == 200
        except httpx.HTTPError:
            return False

    def rss_bytes(self, pid: int | None) -> int | None:
        if pid is None:
            return None
        try:
            return int(psutil.Process(pid).memory_info().rss)
        except (psutil.Error, OSError):
            return None

    def cli_status(self) -> dict[str, Any]:
        """``<cli> status`` as data; the weights line comes from the CLI's own check."""
        try:
            r = subprocess.run(
                self.argv("status"),
                capture_output=True,
                text=True,
                timeout=self._cli_timeout_s,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": str(exc), "weights": None}
        m = _WEIGHTS_RE.search(r.stdout)
        return {
            "ok": r.returncode == 0,
            "error": r.stderr.strip() or None,
            "weights": m.group(1) if m else None,
            "output": r.stdout,
        }


class SidecarRegistry:
    def __init__(
        self,
        settings: Settings,
        transports: dict[str, httpx.MockTransport | None] | None = None,
    ) -> None:
        self.settings = settings
        self._transports = transports or {}
        self._warmups: dict[str, Callable[[], bool]] = {}

    def specs(self, enabled_only: bool = True) -> list[SidecarSpec]:
        out = [build(self.settings) for build in BUILDERS]
        return [s for s in out if s.enabled or not enabled_only]

    def get(self, name: str) -> SidecarSpec:
        for spec in self.specs():
            if spec.name == name:
                return spec
        raise KeyError(name)

    def services(self, enabled_only: bool = True) -> list[SidecarSpec]:
        out = [build(self.settings) for build in SERVICE_BUILDERS]
        return [s for s in out if s.enabled or not enabled_only]

    def get_service(self, name: str) -> SidecarSpec:
        for spec in self.services():
            if spec.name == name:
                return spec
        raise KeyError(name)

    def services_running(self) -> list[SidecarSpec]:
        return [s for s in self.services() if self.process(s.name).status().running]

    def _any(self, name: str) -> SidecarSpec:
        for spec in self.specs(enabled_only=False) + self.services(enabled_only=False):
            if spec.name == name:
                return spec
        raise KeyError(name)

    def process(self, name: str) -> SidecarProcess:
        self._any(name)  # unknown names fail here, not on first use
        return SidecarProcess(
            lambda: self._any(name),
            self._transports.get(name),
            self.settings.probe_timeout_s * 3,
        )

    def running(self) -> list[SidecarSpec]:
        return [s for s in self.specs() if self.process(s.name).status().running]

    def argv(self, name: str, action: str) -> list[str]:
        return self.process(name).argv(action)

    def down_sync(self, name: str) -> bool:
        enabled = {s.name for s in self.specs()} | {s.name for s in self.services()}
        if name not in enabled:
            return True
        return self.process(name).down_sync()

    def ready_sync(self, name: str) -> bool:
        return self.process(name).ready_sync()

    def set_warmup(self, name: str, fn: Callable[[], bool]) -> None:
        self._warmups[name] = fn

    def warmup(self, name: str) -> Callable[[], bool] | None:
        return self._warmups.get(name)


def service_unavailable_reason(
    registry: SidecarRegistry, store: Any, name: str, exclusive: Callable[[], str | None]
) -> str | None:
    """None while the service's process runs; otherwise why not (D-E12), so clients can
    tell "wait" (comfyui_active, strata_active) from "broken" (down) from "off" (stopped).
    ``exclusive`` names the engine that excludes every service, if one runs."""
    try:
        spec = registry.get_service(name)
    except KeyError:
        return "stopped"
    if registry.process(name).status().running:
        return None
    engine = exclusive()
    if engine:
        return f"{engine}_active"
    return "down" if read_service_intent(store, spec) == "running" else "stopped"


@dataclass(frozen=True)
class SidecarPlan:
    ok: bool
    reasons: list[str]
    needs_confirm: bool
    stop_first: SidecarSpec | None
    mlx_gb: float | None
    sidecar_gb: float
    total_gb: float | None
    limit_gb: float
    lines: list[str] = field(default_factory=list)


def budget_limit(ram_gb: float, headroom_gb: float, wired_limit_gb: float | None) -> tuple[float, str]:
    """Physical RAM less the headroom, or the GPU wired limit when one is set and lower."""
    limit = ram_gb - headroom_gb
    if wired_limit_gb:
        limit = min(limit, wired_limit_gb)
    wired_binds = bool(wired_limit_gb) and limit < ram_gb - headroom_gb
    note = "(GPU wired limit)" if wired_binds else f"(RAM {ram_gb:.0f} - headroom {headroom_gb:.0f})"
    return limit, f"limit {limit:.1f} GB {note}"


def plan_sidecar_start(
    *,
    spec: SidecarSpec,
    other: SidecarSpec | None,
    confirm: bool,
    comfy_running: bool,
    mlx_running: bool,
    mlx_name: str | None,
    mlx_gb: float | None,
    headroom_gb: float,
    ram_gb: float,
    wired_limit_gb: float | None,
    pressure: str | None,
    services: Sequence[SidecarSpec] = (),
    strata_running: bool = False,
    exclusive_engines: Sequence[str] = (),
) -> SidecarPlan:
    """Whether ``spec`` may start now, and every reason it may not (D-S2, D-S4).

    Another running sidecar needs ``confirm``; a confirmed swap stops it first, so only
    ``spec``'s footprint is counted. The limit is physical RAM less the headroom, or the
    GPU wired limit when one is set. Unknown pressure refuses. Running always-on services
    (M8) are counted too.
    """
    reasons: list[str] = []
    needs_confirm = other is not None and not confirm
    if needs_confirm:
        assert other is not None
        reasons.append(
            f"the {other.label} sidecar is running; confirm to stop it and start the"
            f" {spec.label} sidecar"
        )
    labels = [*exclusive_engines, *(["ComfyUI"] if comfy_running else []),
              *(["Strata"] if strata_running else [])]
    reasons += [f"{label} is running; the sidecar never runs next to it" for label in labels]
    if mlx_running and not spec.allows(mlx_name):
        listed = ", ".join(sorted(spec.allowed_mlx)) or "none"
        reasons.append(
            f"MLX model {mlx_name or '(unknown)'} is not allowed with the sidecar"
            f" (allowed: {listed})"
        )
    if mlx_running and mlx_gb is None:
        reasons.append("cannot estimate the MLX model's memory (config.json unreadable)")
    if pressure != "normal":
        reasons.append(f"memory pressure is {pressure or 'unavailable'}, not normal")
    limit, limit_line = budget_limit(ram_gb, headroom_gb, wired_limit_gb)
    used = (mlx_gb or 0.0) if mlx_running else 0.0
    services_gb = sum(s.footprint_gb for s in services)
    total = None if (mlx_running and mlx_gb is None) else used + services_gb + spec.footprint_gb
    if total is not None and total > limit:
        reasons.append(f"estimated {total:.1f} GB exceeds the {limit:.1f} GB limit")
    mlx_line = f"{used:.1f} GB" if total is not None else "unknown"
    lines = [
        f"MLX {mlx_line} ({mlx_name})" if mlx_running else "MLX stopped",
        *(f"{s.label} {s.footprint_gb:.1f} GB (running)" for s in services),
        f"{spec.label} {spec.footprint_gb:.1f} GB",
        limit_line,
    ]
    stop_first = other if other is not None and confirm else None
    if stop_first is not None:
        lines.insert(0, f"stops the {stop_first.label} sidecar first")
    return SidecarPlan(
        not reasons,
        reasons,
        needs_confirm,
        stop_first,
        used if total is not None else None,
        spec.footprint_gb,
        total,
        limit,
        lines,
    )


def plan_service_start(
    *,
    spec: SidecarSpec,
    sidecar: SidecarSpec | None,
    comfy_running: bool,
    mlx_running: bool,
    mlx_name: str | None,
    mlx_gb: float | None,
    headroom_gb: float,
    ram_gb: float,
    wired_limit_gb: float | None,
    pressure: str | None,
    strata_running: bool = False,
    exclusive_engines: Sequence[str] = (),
) -> SidecarPlan:
    """Whether the always-on service ``spec`` may start now (M8, D-E7). It counts MLX, the
    slot sidecar that is running, and itself. With the engine off it may run (MLX = 0 GB)."""
    reasons: list[str] = []
    labels = [*exclusive_engines, *(["ComfyUI"] if comfy_running else []),
              *(["Strata"] if strata_running else [])]
    reasons += [f"{label} is running; {spec.noun} never runs next to it" for label in labels]
    if mlx_running and not spec.allows(mlx_name):
        listed = ", ".join(sorted(spec.allowed_mlx)) or "none"
        reasons.append(
            f"MLX model {mlx_name or '(unknown)'} is not allowed with {spec.noun}"
            f" (allowed: {listed})"
        )
    if mlx_running and mlx_gb is None:
        reasons.append("cannot estimate the MLX model's memory (config.json unreadable)")
    if pressure != "normal":
        reasons.append(f"memory pressure is {pressure or 'unavailable'}, not normal")
    limit, limit_line = budget_limit(ram_gb, headroom_gb, wired_limit_gb)
    used = (mlx_gb or 0.0) if mlx_running else 0.0
    side = sidecar.footprint_gb if sidecar else 0.0
    total = None if (mlx_running and mlx_gb is None) else used + side + spec.footprint_gb
    if total is not None and total > limit:
        reasons.append(f"estimated {total:.1f} GB exceeds the {limit:.1f} GB limit")
    mlx_line = f"{used:.1f} GB" if total is not None else "unknown"
    lines = [
        f"MLX {mlx_line} ({mlx_name})" if mlx_running else "MLX stopped",
        *([f"{sidecar.label} {side:.1f} GB (running)"] if sidecar else []),
        f"{spec.label} {spec.footprint_gb:.1f} GB",
        limit_line,
    ]
    return SidecarPlan(
        not reasons,
        reasons,
        False,
        None,
        used if total is not None else None,
        spec.footprint_gb,
        total,
        limit,
        lines,
    )


class SidecarMonitor:
    """Watchdog tick listener. Alert-only: raises the intended sidecar's own down alert
    while it is meant to run and is not, outside jobs; it never restarts anything. With a
    history it samples every enabled sidecar's availability once per interval and prunes
    old rows once a day (``prune_extra`` adds other sidecars' tables)."""

    def __init__(
        self,
        settings: Settings,
        registry: SidecarRegistry,
        store: Any,
        alerts: Any,
        *,
        history: Any = None,
        backends: BackendLookup | None = None,
        prune_extra: Sequence[Callable[[float], int]] = (),
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self.alerts = alerts
        self.history = history
        self.backends = backends
        self.prune_extra = list(prune_extra)
        self._last_sample: float | None = None
        self._last_prune: float | None = None
        self._sampling = False

    async def __call__(self, snap: Any, _observed: Any) -> None:
        specs = self.registry.specs()
        services = self.registry.services()
        if not specs and not services:
            return
        intent = read_intent(self.store) or {}
        running = {s.name for s in await asyncio.to_thread(self.registry.running)}
        svc_up = {s.name for s in await asyncio.to_thread(self.registry.services_running)}
        svc_meant = {s.name for s in services if read_service_intent(self.store, s) == "running"}
        # ComfyUI and Strata stop the services on purpose (D-E4): not an outage, never an alert
        engine = getattr(snap, "engine", None)
        paused = engine in ("comfyui", "strata")  # compatibility for standalone monitor callers
        if self.backends is not None:
            paused = isinstance(engine, str) and engine not in ("none", "unknown", "conflict") and self.backends.get(engine).spec.exclusive
        if self.history is not None:
            await self._sample(snap, intent, specs, running)
            await self._sample_services(snap, services, svc_up, svc_meant, paused)
        if snap.job_running:
            return
        for spec in specs:
            meant = intent.get("name") == spec.name and intent.get("value") == "running"
            if meant and spec.name not in running:
                self.alerts.raise_condition(
                    spec.down_alert_key,
                    "warning",
                    f"{spec.title} is down",
                    "It was started from the admin and is no longer running. It is not"
                    " restarted automatically; start it again from the Models page.",
                    snap.wall,
                )
            else:
                self.alerts.resolve_condition(spec.down_alert_key, snap.wall)
        for spec in services:
            if spec.name in svc_meant and spec.name not in svc_up and not paused:
                self.alerts.raise_condition(
                    spec.down_alert_key,
                    "warning",
                    f"{spec.title} is down",
                    "It is meant to run and is no longer running. It is not restarted"
                    " automatically; start it again from the Models page (Embeddings card).",
                    snap.wall,
                )
            else:
                self.alerts.resolve_condition(spec.down_alert_key, snap.wall)

    def _due(self, ts: float) -> bool:
        last = self._last_sample
        # a clock that went back must not silence sampling until it catches up
        return last is None or ts - last >= self.settings.availability_interval_s or ts < last

    async def _sample(
        self, snap: Any, intent: dict[str, Any], specs: list[SidecarSpec], running: set[str]
    ) -> None:
        ts = snap.wall
        self._sampling = self._due(ts)
        if self._sampling:
            self._last_sample = ts
            for spec in specs:
                meant = intent.get("name") == spec.name and intent.get("value") == "running"
                if spec.name in running:
                    ready = await asyncio.to_thread(self.registry.ready_sync, spec.name)
                    state = "ready" if ready else "loading"
                elif snap.job_running and meant:
                    state = "loading"  # a start or stop in progress is never an outage
                else:
                    state = "down" if meant else "stopped"
                await asyncio.to_thread(self.history.add_sample, ts, state, spec.name)
        if self._last_prune is None or not 0 <= ts - self._last_prune < 86400:
            self._last_prune = ts
            before = ts - self.settings.observability_retention_days * 86400
            await asyncio.to_thread(self.history.prune, before)
            for prune in self.prune_extra:
                await asyncio.to_thread(prune, before)

    async def _sample_services(
        self,
        snap: Any,
        services: list[SidecarSpec],
        up: set[str],
        meant: set[str],
        paused: bool,
    ) -> None:
        if not self._sampling:  # same interval as the slot samples, decided in _sample
            return
        for spec in services:
            if spec.name in up:
                ready = await asyncio.to_thread(self.registry.ready_sync, spec.name)
                state = "ready" if ready else "loading"
            elif paused:
                state = "stopped"  # stopped for ComfyUI on purpose: excluded from the ratio
            elif snap.job_running and spec.name in meant:
                state = "loading"
            else:
                state = "down" if spec.name in meant else "stopped"
            await asyncio.to_thread(self.history.add_sample, snap.wall, state, spec.name)
