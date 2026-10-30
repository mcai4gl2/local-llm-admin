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
import math
import os
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
class ServiceModel:
    id: str
    capability: str
    revision: str | None = None
    precision: str | None = None


@dataclass(frozen=True)
class ServiceComponent:
    """Owned dependency; lifecycle belongs to the logical service's fixed CLI."""
    name: str
    port: int | None
    pid_file: Path
    cmdline_marker: str
    requires: tuple[str, ...] = ()


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

    models: tuple[ServiceModel, ...] = ()
    ui_path: str = ""
    log_sources: tuple[str, ...] = ()
    log_paths: tuple[Path, ...] = ()
    components: tuple[ServiceComponent, ...] = ()
    generation_checked: bool = False

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
    owned: bool = True  # a listener alone never proves process ownership


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
        models=(ServiceModel(s.tts_model_id, "speech"),),
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
        models=(ServiceModel(s.embed_model_id, "embeddings"), ServiceModel(s.rerank_model_id, "rerank")),
        ui_path="/embed",
        log_sources=("embed",), log_paths=(s.embed_root / "logs/qwen-embed.log",),
    )


def ocr_spec(s: Settings) -> SidecarSpec:
    return SidecarSpec(
        name="paddleocr-vl", label="OCR", title="OCR service", port=s.ocr_port,
        allowed_mlx=_names(s.ocr_allowed_mlx), enabled=s.ocr_enabled,
        cli_path=s.ocr_path, pid_file=s.ocr_root / "run/paddleocr-vl.pid",
        cmdline_marker=str(s.ocr_root / "app/server.py"), api_base=s.ocr_api_base,
        ready_path="/ready", ready_timeout_s=s.ocr_ready_timeout_s,
        footprint_gb=s.ocr_footprint_gb, down_alert_key="ocr_down",
        weights_hint="Provision the pinned OCR pipeline and VLM runtime under ocr/.",
        kind="service", intent_key="ocr_intent",
        models=(ServiceModel(s.ocr_model_id, "ocr", s.ocr_model_revision, s.ocr_precision),), ui_path="/ocr",
        log_sources=("ocr", "ocr-vlm"),
        log_paths=(s.ocr_root / "logs/ocr.log", s.ocr_root / "logs/vlm.log"),
        generation_checked=True,
        components=(
            ServiceComponent("vlm", s.ocr_vlm_port, s.ocr_root / "run/vlm.pid", "mlx_vlm.server"),
            ServiceComponent("pipeline", None, s.ocr_root / "run/pipeline.pid",
                             str(s.ocr_root / "app/worker.py"), requires=("vlm",)),
        ),
    )


# Always-on services (M8): never slot occupants; they run next to whichever sidecar holds
# the slot, stop for ComfyUI, and keep their own intent ({"value", "job_id", "ts"}).
SERVICE_BUILDERS: list[Callable[[Settings], SidecarSpec]] = [embed_spec, ocr_spec]


def _validate_components(items: Sequence[ServiceComponent]) -> None:
    components = {c.name: c for c in items}
    if len(components) != len(items):
        raise ValueError("duplicate resident component")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name not in components or name in visiting:
            raise ValueError("malformed resident dependency graph")
        if name in visited:
            return
        visiting.add(name)
        component = components[name]
        if not component.cmdline_marker or not component.pid_file.is_absolute():
            raise ValueError("invalid resident component ownership")
        for dependency in component.requires:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for component in items:
        visit(component.name)


def validate_services(specs: Sequence[SidecarSpec]) -> None:
    """Reject ambiguous routing/ownership, including disabled services' configured ports."""
    seen: dict[str, set[str | int]] = {key: set() for key in ("id", "model", "intent", "port")}
    def claim(kind: str, value: str | int) -> None:
        if value in seen[kind]:
            raise ValueError(f"duplicate resident {kind}: {value}")
        seen[kind].add(value)

    for spec in specs:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", spec.name) or spec.kind != "service":
            raise ValueError("invalid resident service identity")
        claim("id", spec.name)
        if not spec.intent_key or not spec.models:
            raise ValueError("resident service needs intent and public models")
        claim("intent", spec.intent_key)
        for model in spec.models:
            if not model.id or not model.capability:
                raise ValueError("invalid resident model metadata")
            claim("model", model.id)
        _validate_components(spec.components)
        for port in [spec.port, *(c.port for c in spec.components)]:
            if port is None:
                continue
            if not 0 <= port <= 65535 or (spec.enabled and port == 0):
                raise ValueError("configure resident ports before enabling the service")
            if port:
                claim("port", port)


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
                    return SidecarStatus(True, pid, owned=self._generation_matches(pid))
            except (psutil.Error, OSError):
                pass
        # Dependencies still own resources when the pipeline process has died.
        for component in spec.components:
            try:
                child = int(component.pid_file.read_text().strip())
                if component.cmdline_marker in " ".join(psutil.Process(child).cmdline()):
                    return SidecarStatus(True, None, owned=False)
            except (OSError, ValueError, psutil.Error):
                pass
        ports = [spec.port, *(c.port for c in spec.components)]
        return SidecarStatus(
            any(p is not None and p > 0 and MlxClient.port_listening(p) for p in ports), None, owned=False,
        )

    def _generation_matches(self, pid: int) -> bool:
        if not self.spec.generation_checked:
            return True
        try:
            record = json.loads(self.spec.pid_file.with_suffix(".json").read_text())
            proc = psutil.Process(pid)
            started = time.mktime(time.strptime(record["started"], "%a %b %d %H:%M:%S %Y"))
            return (record["pid"] == pid and record["pgid"] == os.getpgid(pid)
                    and abs(proc.create_time() - started) < 1
                    and record["command"] == " ".join(proc.cmdline()))
        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
            return False

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
        self._activity: dict[str, Callable[[], bool]] = {}
        self.services(enabled_only=False)  # validate configuration before lifecycle use

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
        validate_services(out)
        slots = [build(self.settings) for build in BUILDERS]
        reserved_ids = {slot.name for slot in slots}
        reserved_models = {model.id for slot in slots for model in slot.models}
        reserved_ports = {
            self.settings.mlx_port, self.settings.chat_ui_port, self.settings.comfyui_port,
            self.settings.strata_port, self.settings.splash_port, self.settings.decision_gateway_port,
            *(slot.port for slot in slots),
        }
        if self.settings.inference_api_key.get_secret_value():
            reserved_ports.add(self.settings.inference_port)
        for service in out:
            if service.name in reserved_ids or any(model.id in reserved_models for model in service.models):
                raise ValueError("duplicate service and sidecar identity")
            if any(port and port in reserved_ports for port in [
                service.port, *(c.port for c in service.components),
            ]):
                raise ValueError("resident port conflicts with another listener")
        return [s for s in out if s.enabled or not enabled_only]

    def get_service(self, name: str, enabled_only: bool = True) -> SidecarSpec:
        for spec in self.services(enabled_only=enabled_only):
            if spec.name == name:
                return spec
        raise KeyError(name)

    def services_running(self) -> list[SidecarSpec]:
        return [s for s in self.services(enabled_only=False) if self.process(s.name).status().running]

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

    def ownership_conflicts(self) -> list[str]:
        result = []
        for spec in [*self.specs(enabled_only=False), *self.services(enabled_only=False)]:
            status = self.process(spec.name).status()
            if status.running and (not status.owned or spec.kind == "sidecar" and not spec.enabled):
                result.append(spec.name)
        return result

    def catalog_error(self) -> str | None:
        from .resident_manifest import build_manifest

        path = self.settings.mlx_root / "etc/resident-services.json"
        installed = (self.settings.ocr_root / "bin/paddleocr-vl").exists()
        if not path.exists() and not installed:
            return None  # legacy embeddings before catalog provisioning
        try:
            if path.is_symlink() or path.stat().st_mode & 0o022:
                return "Resident host catalog has unsafe permissions."
            if json.loads(path.read_text()) != build_manifest(self.settings, self):
                return "Resident host catalog differs from the admin configuration."
        except (OSError, ValueError):
            return "Resident host catalog is missing or unreadable."
        return None

    def running(self) -> list[SidecarSpec]:
        return [s for s in self.specs() if self.process(s.name).status().running]

    def argv(self, name: str, action: str) -> list[str]:
        return self.process(name).argv(action)

    def down_sync(self, name: str) -> bool:
        return self.process(name).down_sync()

    def ready_sync(self, name: str) -> bool:
        return self.process(name).ready_sync()

    def owned_ready_sync(self, name: str) -> bool:
        status = self.process(name).status()
        return status.running and status.owned and self.ready_sync(name)

    def set_activity(self, name: str, busy: Callable[[], bool]) -> None:
        self._any(name)
        self._activity[name] = busy

    def busy(self, name: str) -> bool:
        hook = self._activity.get(name)
        return hook() if hook else False

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
    status = registry.process(name).status()
    if status.running:
        return None if status.owned else "ownership_unknown"
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
    if (not math.isfinite(ram_gb) or ram_gb <= 0
            or not math.isfinite(headroom_gb) or headroom_gb < 0
            or wired_limit_gb is not None and (
                not math.isfinite(wired_limit_gb) or wired_limit_gb < 0
            )):
        return 0.0, "memory limit unavailable"
    limit = ram_gb - headroom_gb
    if wired_limit_gb:
        limit = min(limit, wired_limit_gb)
    wired_binds = bool(wired_limit_gb) and limit < ram_gb - headroom_gb
    note = "(GPU wired limit)" if wired_binds else f"(RAM {ram_gb:.0f} - headroom {headroom_gb:.0f})"
    return limit, f"limit {limit:.1f} GB {note}"


def reservation_inventory(
    specs: Sequence[SidecarSpec],
) -> tuple[list[SidecarSpec], list[str]]:
    """Count each owner once; disagreement or an unknown reservation refuses admission."""
    inventory: dict[str, SidecarSpec] = {}
    reasons: list[str] = []
    for spec in specs:
        previous = inventory.get(spec.name)
        if previous is not None and previous != spec:
            reasons.append(f"conflicting memory reservation for {spec.name}")
        inventory[spec.name] = spec
        if not math.isfinite(spec.footprint_gb) or spec.footprint_gb <= 0:
            reasons.append(f"cannot estimate {spec.name} memory reservation")
    return list(inventory.values()), list(dict.fromkeys(reasons))


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
    if mlx_gb is not None and (not math.isfinite(mlx_gb) or mlx_gb < 0):
        mlx_gb = None
    if mlx_running and mlx_gb is None:
        reasons.append("cannot estimate the MLX model's memory (config.json unreadable)")
    if pressure != "normal":
        reasons.append(f"memory pressure is {pressure or 'unavailable'}, not normal")
    limit, limit_line = budget_limit(ram_gb, headroom_gb, wired_limit_gb)
    used = (mlx_gb or 0.0) if mlx_running else 0.0
    inventory, invalid = reservation_inventory([*services, spec])
    reasons += invalid
    services = [s for s in inventory if s.name != spec.name]
    if mlx_running:
        reasons += [f"MLX model {mlx_name or '(unknown)'} is not allowed with {s.noun}"
                    for s in services if not s.allows(mlx_name)]
    services_gb = sum(s.footprint_gb for s in services)
    total = None if invalid or (mlx_running and mlx_gb is None) else used + services_gb + spec.footprint_gb
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
    services: Sequence[SidecarSpec] = (),
    strata_running: bool = False,
    exclusive_engines: Sequence[str] = (),
) -> SidecarPlan:
    """Whether the always-on service ``spec`` may start now (M8, D-E7). It counts MLX, the
    slot sidecar, every sibling service, and itself exactly once. With the engine off
    it may run (MLX = 0 GB)."""
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
    if mlx_gb is not None and (not math.isfinite(mlx_gb) or mlx_gb < 0):
        mlx_gb = None
    if mlx_running and mlx_gb is None:
        reasons.append("cannot estimate the MLX model's memory (config.json unreadable)")
    if pressure != "normal":
        reasons.append(f"memory pressure is {pressure or 'unavailable'}, not normal")
    limit, limit_line = budget_limit(ram_gb, headroom_gb, wired_limit_gb)
    used = (mlx_gb or 0.0) if mlx_running else 0.0
    side = sidecar.footprint_gb if sidecar else 0.0
    inventory, invalid = reservation_inventory([*services, *([sidecar] if sidecar else []), spec])
    reasons += invalid
    siblings = [s for s in inventory if s.name not in {spec.name, sidecar.name if sidecar else ""}]
    if mlx_running:
        reasons += [f"MLX model {mlx_name or '(unknown)'} is not allowed with {s.noun}"
                    for s in inventory if s.name != spec.name and not s.allows(mlx_name)]
    total = None if invalid or (mlx_running and mlx_gb is None) else used + sum(s.footprint_gb for s in inventory)
    if total is not None and total > limit:
        reasons.append(f"estimated {total:.1f} GB exceeds the {limit:.1f} GB limit")
    mlx_line = f"{used:.1f} GB" if total is not None else "unknown"
    lines = [
        f"MLX {mlx_line} ({mlx_name})" if mlx_running else "MLX stopped",
        *([f"{sidecar.label} {side:.1f} GB (running)"] if sidecar else []),
        *(f"{s.label} {s.footprint_gb:.1f} GB (running)" for s in siblings),
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
            ready = (spec.name in svc_up and await asyncio.to_thread(self.registry.owned_ready_sync, spec.name))
            if spec.name in svc_meant and not ready and not paused:
                self.alerts.raise_condition(
                    spec.down_alert_key,
                    "warning",
                    f"{spec.title} is down",
                    "It is meant to run and is no longer ready. It is not restarted"
                    f" automatically; start it again from the Models page ({spec.title}).",
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
                ready = await asyncio.to_thread(self.registry.owned_ready_sync, spec.name)
                state = "ready" if ready else "loading" if snap.job_running else "down"
            elif paused:
                state = "stopped"  # stopped for ComfyUI on purpose: excluded from the ratio
            elif snap.job_running and spec.name in meant:
                state = "loading"
            else:
                state = "down" if spec.name in meant else "stopped"
            await asyncio.to_thread(self.history.add_sample, snap.wall, state, spec.name)
