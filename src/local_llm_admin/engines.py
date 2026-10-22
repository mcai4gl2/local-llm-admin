"""Engines that own the Mac's GPU and memory: the MLX LLM server, ComfyUI or Strata, one
at a time.

ComfyUI and Strata are driven only through their host wrappers (``comfyui/bin/comfyui``,
``strata/bin/strata``) and their HTTP APIs; the admin never imports them or uses their venvs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
import psutil

from .config import Settings
from .jobs import Step
from .mlx import _RECENT_WRITE_S, MlxClient, ProcessStatus, validate_name

if TYPE_CHECKING:
    from .sidecars import SidecarSpec

EngineKind = Literal["mlx", "comfyui", "strata", "off"]
EXCLUSIVE: tuple[EngineKind, ...] = ("comfyui", "strata")  # no sidecar or service beside them
_COMFY_STATUS_RE = re.compile(r"^comfyui:\s*(running|stopped)(?:\s+pid\s+(\d+))?", re.M)
_COMFY_ACTIONS = ("start", "stop", "status")
_STRATA_ACTIONS = ("start", "stop", "status")
# Strata's packs, in the order the engine control lists them (bin/strata accepts the same)
STRATA_PACKS = ("Q2_0", "IQ2_XS", "IQ3_XXS", "IQ3_S")
STRATA_MODEL_PREFIX = "qwen3.8-flash-next-"


@dataclass(frozen=True)
class Target:
    kind: EngineKind
    name: str | None = None


def parse_target(raw: str) -> Target:
    """``off`` | ``comfyui`` | ``strata`` | ``strata:<pack>`` | ``mlx:<registered name>``."""
    if raw in ("off", "comfyui", "strata"):
        return Target(raw)  # type: ignore[arg-type]
    if raw.startswith("mlx:"):
        return Target("mlx", validate_name(raw[4:]))
    if raw.startswith("strata:") and raw[7:] in STRATA_PACKS:
        return Target("strata", raw[7:])
    raise ValueError(f"unknown engine target: {raw!r}")


def strata_pack_of(model: str | None) -> str | None:
    """The pack of a model id Strata serves (``qwen3.8-flash-next-iq3_xxs`` → ``IQ3_XXS``)."""
    if not model or not model.startswith(STRATA_MODEL_PREFIX):
        return None
    pack = model[len(STRATA_MODEL_PREFIX) :].upper()
    return pack if pack in STRATA_PACKS else None


def parse_comfy_status(text: str) -> ProcessStatus:
    m = _COMFY_STATUS_RE.search(text)
    if not m or m.group(1) != "running":
        return ProcessStatus(False, None)
    return ProcessStatus(True, int(m.group(2)) if m.group(2) else None)


def parse_system_stats(d: dict[str, Any]) -> dict[str, Any]:
    raw_system, raw_devices = d.get("system"), d.get("devices")
    system: dict[str, Any] = raw_system if isinstance(raw_system, dict) else {}
    devices: list[Any] = raw_devices if isinstance(raw_devices, list) else []
    dev: dict[str, Any] = devices[0] if devices and isinstance(devices[0], dict) else {}
    return {
        "version": system.get("comfyui_version"),
        "pytorch_version": system.get("pytorch_version"),
        "device": dev.get("name"),
        "device_type": dev.get("type"),
        "vram_total": dev.get("vram_total"),
        "vram_free": dev.get("vram_free"),
        "ram_total": system.get("ram_total"),
        "ram_free": system.get("ram_free"),
    }


def queue_counts(d: dict[str, Any]) -> tuple[int, int]:
    running, pending = d.get("queue_running"), d.get("queue_pending")
    return (
        len(running) if isinstance(running, list) else 0,
        len(pending) if isinstance(pending, list) else 0,
    )


def resolve_active(mlx_running: bool, comfy_running: bool, strata_running: bool = False) -> str:
    """``mlx`` | ``comfyui`` | ``strata`` | ``none``, or ``conflict`` when more than one runs."""
    running = [
        k for k, up in (("mlx", mlx_running), ("comfyui", comfy_running), ("strata", strata_running)) if up
    ]
    if len(running) > 1:
        return "conflict"
    return running[0] if running else "none"


def badge_state(mlx_state: str, active: str, comfy_ready: bool, strata_ready: bool = False) -> str:
    """Header badge: MLX states, plus comfyui / strata (and their -starting) / conflict."""
    if mlx_state in ("switching", "drive-missing"):
        return mlx_state
    if active == "conflict":
        return "conflict"
    if active == "comfyui":
        return "comfyui" if comfy_ready else "comfyui-starting"
    if active == "strata":
        return "strata" if strata_ready else "strata-starting"
    return mlx_state


def comfy_weights(models_root: Path, hf_hub: Path, now: float) -> list[dict[str, Any]]:
    """Weight files under ``models/<category>/`` plus in-flight Hugging Face downloads.

    ComfyUI weights are single files, so "downloading" means written in the last 120 s.
    ``hf download`` writes ``blobs/<sha>.incomplete`` until a file is finished.
    """
    rows: list[dict[str, Any]] = []
    try:
        categories = sorted(p for p in models_root.iterdir() if p.is_dir())
    except OSError:
        categories = []
    for cat in categories:
        for dirpath, dirnames, files in os.walk(cat):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                p = Path(dirpath) / f
                try:
                    st = p.stat()
                except OSError:
                    continue
                ago = max(0.0, now - st.st_mtime)
                rows.append(
                    {
                        "category": cat.name,
                        "file": str(p.relative_to(cat)),
                        "size_bytes": st.st_size,
                        "last_write_ago_s": ago,
                        "state": "downloading" if ago < _RECENT_WRITE_S else "complete",
                    }
                )
    try:
        repos = sorted(p for p in hf_hub.iterdir() if p.is_dir() and p.name.startswith("models--"))
    except OSError:
        repos = []
    for repo in repos:
        for blob in sorted((repo / "blobs").glob("*.incomplete")):
            try:
                st = blob.stat()
            except OSError:
                continue
            rows.append(
                {
                    "category": "hf-cache",
                    "file": repo.name[8:].replace("--", "/"),
                    "size_bytes": st.st_size,
                    "last_write_ago_s": max(0.0, now - st.st_mtime),
                    "state": "downloading",
                }
            )
    return rows


def output_summary(output_dir: Path) -> dict[str, int]:
    files = size = 0
    for dirpath, _dirs, names in os.walk(output_dir):
        for n in names:
            try:
                size += (Path(dirpath) / n).stat().st_size
                files += 1
            except OSError:
                continue
    return {"files": files, "bytes": size}


class ComfyClient:
    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport

    def argv(self, action: str) -> list[str]:
        if action not in _COMFY_ACTIONS:
            raise ValueError(f"unsupported comfyui action: {action}")
        return [str(self.settings.comfyui_path), action]

    def pid_from_file(self) -> int | None:
        try:
            return int((self.settings.comfyui_root / "run" / "comfyui.pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def running(self) -> ProcessStatus:
        pid = self.pid_from_file()
        if pid is not None and psutil.pid_exists(pid):
            return ProcessStatus(True, pid)
        return ProcessStatus(MlxClient.port_listening(self.settings.comfyui_port), None)

    def down_sync(self) -> bool:
        return not self.running().running

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.comfyui_api_base, timeout=2.0, transport=self._transport
        )

    def _aclient(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.comfyui_api_base, timeout=2.0, transport=self._transport
        )

    def ready_sync(self) -> bool:
        try:
            with self._client() as http:
                return http.get("/system_stats").status_code == 200
        except httpx.HTTPError:
            return False

    async def system_stats(self) -> dict[str, Any] | None:
        try:
            async with self._aclient() as http:
                resp = await http.get("/system_stats")
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return parse_system_stats(data) if isinstance(data, dict) else None

    async def queue_raw(self) -> dict[str, Any] | None:
        """``GET /queue`` as returned by ComfyUI, or None when it cannot be read."""
        try:
            async with self._aclient() as http:
                resp = await http.get("/queue")
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def queue(self) -> tuple[int, int] | None:
        data = await self.queue_raw()
        return queue_counts(data) if data is not None else None

    async def per_job_cancel(self) -> bool | None:
        """Whether ``POST /api/jobs/{id}/cancel`` exists (ComfyUI 0.37+); None if unknown."""
        try:
            async with self._aclient() as http:
                resp = await http.get("/api/jobs", params={"limit": 1, "status": "in_progress"})
        except httpx.HTTPError:
            return None
        if resp.status_code == 200:
            return True
        return False if resp.status_code in (404, 405) else None

    async def post(self, path: str, body: dict[str, Any], timeout_s: float = 5.0) -> httpx.Response:
        """One mutating call; raises ``httpx.HTTPError`` on transport errors or non-2xx."""
        async with httpx.AsyncClient(
            base_url=self.settings.comfyui_api_base, timeout=timeout_s, transport=self._transport
        ) as http:
            resp = await http.post(path, json=body)
        resp.raise_for_status()
        return resp

    async def history(self, max_items: int, timeout_s: float = 5.0) -> dict[str, Any] | None:
        """``GET /history?max_items=N`` (newest entries), or None when it cannot be read."""
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.comfyui_api_base,
                timeout=timeout_s,
                transport=self._transport,
            ) as http:
                resp = await http.get("/history", params={"max_items": max_items})
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def cancel_all_sync(self) -> None:
        """Drop pending work, then stop the running generation (D-C4 forced switch)."""
        with self._client() as http:
            http.post("/queue", json={"clear": True}).raise_for_status()
            http.post("/interrupt", json={}).raise_for_status()

    async def status(self) -> dict[str, Any]:
        proc = self.running()
        stats = await self.system_stats() if proc.running else None
        q = await self.queue() if stats else None
        return {
            "running": proc.running,
            "pid": proc.pid,
            "ready": stats is not None,
            **(stats or parse_system_stats({})),
            "queue_running": q[0] if q else None,
            "queue_pending": q[1] if q else None,
            "public_url": self.settings.comfyui_public_url,
            "checked_at": time.time(),
        }

    def weights(self, now: float | None = None) -> list[dict[str, Any]]:
        root = self.settings.comfyui_root
        return comfy_weights(root / "models", root / "cache" / "hf" / "hub", now or time.time())

    def output(self) -> dict[str, int]:
        return output_summary(self.settings.comfyui_root / "output")


def _num(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_strata_metrics(d: dict[str, Any]) -> dict[str, Any]:
    """Strata's ``GET /metrics`` JSON, reduced to what the admin shows. Every field is read
    with ``dict.get``: a field a Strata version drops hides its tile instead of failing."""
    raw_live, raw_totals, raw_engine = d.get("live"), d.get("totals"), d.get("engine")
    live: dict[str, Any] = raw_live if isinstance(raw_live, dict) else {}
    totals: dict[str, Any] = raw_totals if isinstance(raw_totals, dict) else {}
    engine: dict[str, Any] = raw_engine if isinstance(raw_engine, dict) else {}
    prompt, reused = _num(totals, "prompt_tokens"), _num(totals, "reused") or 0.0
    read = prompt - reused if prompt is not None else None  # what was read, as llama.cpp counts it
    out = _num(totals, "output_tokens")
    prompt_ms, decode_ms = _num(totals, "prompt_ms"), _num(totals, "decode_ms")
    requests = _num(totals, "requests")
    state = live.get("state")
    return {
        "model": engine.get("model"),
        "max_context": _num(engine, "max_context"),
        "state": state if isinstance(state, str) else None,
        "queued": int(_num(live, "queued") or 0),
        "tok_s": _num(live, "tok_s"),
        "prefill_tok_s": _num(live, "prefill_tok_s_mean"),
        "requests": int(requests) if requests is not None else None,
        "prompt_tokens": int(read) if read is not None else None,
        "output_tokens": int(out) if out is not None else None,
        "prompt_tok_s": round(read / prompt_ms * 1000, 1) if read and prompt_ms else None,
        "decode_tok_s": round(out / decode_ms * 1000, 1) if out and decode_ms else None,
    }


class StrataClient:
    """The Strata engine through ``strata/bin/strata`` and its HTTP API (MLX's ``:8080``)."""

    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport

    def argv(self, action: str, pack: str | None = None) -> list[str]:
        if action not in _STRATA_ACTIONS:
            raise ValueError(f"unsupported strata action: {action}")
        if pack is None:
            return [str(self.settings.strata_path), action]
        if action != "start" or pack not in STRATA_PACKS:
            raise ValueError(f"unsupported strata pack for {action}: {pack!r}")
        return [str(self.settings.strata_path), action, pack]

    def packs(self) -> list[dict[str, Any]]:
        """Installed packs: the ``app/strata-<pack>.json`` run configs Strata's setup wrote,
        with the context each was set up for (read only)."""
        out: list[dict[str, Any]] = []
        for pack in STRATA_PACKS:
            path = self.settings.strata_root / "app" / f"strata-{pack.lower()}.json"
            if not path.is_file():
                continue
            ctx: int | None = None
            try:
                args = json.loads(path.read_text()).get("args")
                if isinstance(args, list) and "--max-context" in args:
                    ctx = int(args[args.index("--max-context") + 1])
            except (OSError, ValueError, AttributeError, IndexError, TypeError):
                ctx = None
            out.append({"pack": pack, "max_context": ctx})
        return out

    def pid_from_file(self) -> int | None:
        try:
            return int((self.settings.strata_root / "run" / "strata.pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def running(self) -> ProcessStatus:
        pid = self.pid_from_file()
        if pid is not None and psutil.pid_exists(pid):
            return ProcessStatus(True, pid)
        if self.settings.strata_port == self.settings.mlx_port:
            return ProcessStatus(False, None)  # a listener on the shared port may be MLX
        return ProcessStatus(MlxClient.port_listening(self.settings.strata_port), None)

    def down_sync(self) -> bool:
        return not self.running().running

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.strata_api_base, timeout=2.0, transport=self._transport
        )

    def ready_sync(self) -> bool:
        """``GET /health`` from Strata itself (not another server on the port) with the model loaded."""
        try:
            with self._client() as http:
                resp = http.get("/health")
            d = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return False
        return isinstance(d, dict) and d.get("service") == "strata" and d.get("loaded") is True

    async def get_json(self, path: str) -> dict[str, Any] | None:
        headers = {}
        key = self.settings.strata_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.strata_api_base,
                timeout=2.0,
                transport=self._transport,
                headers=headers,
            ) as http:
                resp = await http.get(path)
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def status(self) -> dict[str, Any]:
        proc = self.running()
        health = await self.get_json("/health") if proc.running else None
        ok = health is not None and health.get("service") == "strata"
        metrics = await self.get_json("/metrics") if ok else None
        return {
            "running": proc.running,
            "pid": proc.pid,
            "ready": ok and health is not None and health.get("loaded") is True,
            "model": health.get("model") if ok and health else None,
            "pack": strata_pack_of(health.get("model")) if ok and health else None,
            "packs": await asyncio.to_thread(self.packs),
            "max_context": health.get("max_context") if ok and health else None,
            "metrics": parse_strata_metrics(metrics) if metrics is not None else None,
            "checked_at": time.time(),
        }


class EngineOps(Protocol):
    stop_timeout_s: float
    ready_timeout_s: float
    strata_ready_timeout_s: float
    job_timeout_s: float

    def mlx_argv(self, action: str, name: str | None) -> list[str]: ...
    def comfy_argv(self, action: str) -> list[str]: ...
    def mlx_down(self) -> bool: ...
    def comfy_down(self) -> bool: ...
    def comfy_ready(self) -> bool: ...
    def comfy_cancel_all(self) -> None: ...
    def strata_argv(self, action: str, pack: str | None = None) -> list[str]: ...
    def strata_down(self) -> bool: ...
    def strata_ready(self) -> bool: ...
    def sidecar_argv(self, name: str, action: str) -> list[str]: ...
    def sidecar_down(self, name: str) -> bool: ...
    def sidecar_ready(self, name: str) -> bool: ...
    def sidecar_warmup(self, name: str) -> Callable[[], bool] | None: ...


@dataclass(frozen=True)
class Transition:
    steps: list[Step]
    rollback: list[Step]
    rollback_after: int  # rollback only if the step that failed is at or after this index
    from_label: str | None
    to_label: str | None
    forced_stops: tuple[str, ...] = ()  # services stopped by a confirmed switch: intent -> stopped
    unrestored: tuple[str, ...] = ()  # services meant to run that the target model excludes


class TransitionRefused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _stop_comfy(ops: EngineOps, optional: bool = False) -> list[Step]:
    return [
        Step(
            "comfyui stop",
            argv=ops.comfy_argv("stop"),
            timeout_s=ops.job_timeout_s,
            optional=optional,
        ),
        # never optional: nothing may start until the engine is confirmed down
        Step("wait for :8188 to close", wait=ops.comfy_down, timeout_s=ops.stop_timeout_s),
    ]


def _stop_mlx(ops: EngineOps, optional: bool = False) -> list[Step]:
    return [
        Step(
            "mlx-model stop",
            argv=ops.mlx_argv("stop", None),
            timeout_s=ops.job_timeout_s,
            optional=optional,
        ),
        # never optional: nothing may start until the engine is confirmed down
        Step("wait for MLX to exit", wait=ops.mlx_down, timeout_s=ops.stop_timeout_s),
    ]


def _stop_strata(ops: EngineOps, optional: bool = False) -> list[Step]:
    return [
        Step(
            "strata stop",
            argv=ops.strata_argv("stop"),
            timeout_s=ops.job_timeout_s,
            optional=optional,
        ),
        # never optional: nothing may start until the engine is confirmed down
        Step("wait for Strata to exit", wait=ops.strata_down, timeout_s=ops.stop_timeout_s),
    ]


def _start_strata(ops: EngineOps, pack: str | None = None) -> list[Step]:
    label = f"strata start {pack}" if pack else "strata start"
    return [
        Step(label, argv=ops.strata_argv("start", pack), timeout_s=ops.job_timeout_s),
        Step(
            "wait for Strata to be ready",
            wait=ops.strata_ready,
            timeout_s=ops.strata_ready_timeout_s,
        ),
    ]


def _stop_engine(ops: EngineOps, kind: str, optional: bool = False) -> list[Step]:
    if kind == "comfyui":
        return _stop_comfy(ops, optional)
    if kind == "strata":
        return _stop_strata(ops, optional)
    return _stop_mlx(ops, optional)


def _stop_sidecar(ops: EngineOps, spec: SidecarSpec) -> list[Step]:
    return [
        Step(
            f"{spec.name} stop",
            argv=ops.sidecar_argv(spec.name, "stop"),
            timeout_s=ops.job_timeout_s,
        ),
        # never optional: ComfyUI or another MLX model must not start next to it
        Step(
            f"wait for :{spec.port} to close",
            wait=lambda: ops.sidecar_down(spec.name),
            timeout_s=ops.stop_timeout_s,
        ),
    ]


def _start_comfy(ops: EngineOps) -> list[Step]:
    return [
        Step("comfyui start", argv=ops.comfy_argv("start"), timeout_s=ops.job_timeout_s),
        Step("wait for ComfyUI to be ready", wait=ops.comfy_ready, timeout_s=ops.ready_timeout_s),
    ]


def _start_mlx(ops: EngineOps, name: str | None) -> list[Step]:
    label = f"mlx-model start {name}" if name else "mlx-model start"
    return [Step(label, argv=ops.mlx_argv("start", name), timeout_s=ops.job_timeout_s)]


def _start_service(ops: EngineOps, spec: SidecarSpec) -> list[Step]:
    """Restore an always-on service (M8, D-E4). Optional: a failed restore never fails the
    engine switch; the service's down alert reports it instead."""
    steps = [
        Step(
            f"{spec.name} start",
            argv=ops.sidecar_argv(spec.name, "start"),
            timeout_s=60.0,
            optional=True,
        ),
        Step(
            f"wait for :{spec.port} to be ready",
            wait=lambda: ops.sidecar_ready(spec.name),
            timeout_s=spec.ready_timeout_s,
            optional=True,
        ),
    ]
    warm = ops.sidecar_warmup(spec.name)
    if warm is not None:
        steps.append(
            Step(f"warm up {spec.name}", wait=warm, timeout_s=spec.ready_timeout_s, optional=True)
        )
    return steps


def plan_transition(
    target: Target,
    *,
    mlx_running: bool,
    mlx_name: str | None,
    comfy_running: bool,
    comfy_queue: int | None,
    force: bool,
    ops: EngineOps,
    sidecar: SidecarSpec | None = None,
    services: Sequence[SidecarSpec] = (),
    restore: Sequence[SidecarSpec] = (),
    strata_running: bool = False,
    strata_pack: str | None = None,
) -> Transition:
    """Ordered steps from the detected state to ``target``, plus rollback (D-C2).

    ``comfy_queue`` is None when ComfyUI runs but its queue cannot be read: unknown is
    never treated as empty, so stopping it then needs the same confirmation as queued work.

    Rolling back to MLX without a known name runs ``mlx-model start``, which starts
    ``MLX_ACTIVE_NAME``, the last active model (D-C5).

    The running sidecar (M6/M7) is not an engine: it may run next to MLX models it allows,
    never next to ComfyUI or Strata. Going to either stops it first; switching to a model it
    does not allow needs ``force`` and stops it first. Rollback never restarts it.

    Always-on services (M8) stop for ComfyUI and Strata (``EXCLUSIVE``) and for a forced
    switch to a model they do not allow; leaving an exclusive engine for MLX or off restores
    those meant to run (optional steps).

    ``strata:<pack>`` while another (or an unknown) pack runs restarts Strata with it;
    plain ``strata`` takes whichever pack runs, or the one ``bin/strata`` remembers.
    """
    active = resolve_active(mlx_running, comfy_running, strata_running)
    stop_comfy = comfy_running and target.kind != "comfyui"
    repack = (
        strata_running and target.kind == "strata" and target.name not in (None, strata_pack)
    )
    stop_strata = strata_running and (target.kind != "strata" or repack)
    stop_mlx = mlx_running and target.kind != "mlx"
    exclusive = target.kind in EXCLUSIVE
    if stop_comfy and comfy_queue is None and not force:
        raise TransitionRefused(
            409,
            "ComfyUI queue state is unknown (its API is not answering); confirm to stop it"
            " and cancel any queued or running generation",
        )
    if stop_comfy and comfy_queue and not force:
        raise TransitionRefused(
            409,
            f"ComfyUI has {comfy_queue} queued or running generation(s); confirm to cancel them",
        )
    stop_sidecar = sidecar is not None and (
        exclusive or (target.kind == "mlx" and not sidecar.allows(target.name))
    )
    if sidecar is not None and target.kind == "mlx" and not sidecar.allows(target.name) and not force:
        raise TransitionRefused(
            409,
            f"the {sidecar.label} sidecar is running and {target.name} is not allowed next to"
            " it; confirm to stop the sidecar first",
        )
    blocked = [s for s in services if target.kind == "mlx" and not s.allows(target.name)]
    if blocked and not force:
        raise TransitionRefused(
            409,
            f"{blocked[0].noun} is running and {target.name} is not allowed next to it;"
            " confirm to stop it first",
        )
    for_exclusive = list(services) if exclusive else []
    steps: list[Step] = []
    if stop_sidecar:
        assert sidecar is not None
        steps += _stop_sidecar(ops, sidecar)
    for spec in [*for_exclusive, *blocked]:
        steps += _stop_sidecar(ops, spec)
    if stop_comfy:
        if comfy_queue is None:
            # the forced stop records that the queue was unknown and rechecks it first;
            # an API that still does not answer must not block stopping the engine
            steps.append(
                Step(
                    "cancel ComfyUI queue (queue state was unknown)",
                    call=ops.comfy_cancel_all,
                    optional=True,
                )
            )
        elif comfy_queue > 0:
            steps.append(Step("cancel ComfyUI queue", call=ops.comfy_cancel_all))
        steps += _stop_comfy(ops)
    if stop_strata:
        steps += _stop_strata(ops)
    if stop_mlx:
        steps += _stop_mlx(ops)
    source_stopped_at = len(steps)
    if target.kind == "mlx":
        if not mlx_running:
            steps += _start_mlx(ops, target.name)
        elif mlx_name != target.name:
            steps.append(
                Step(
                    f"mlx-model switch {target.name}",
                    argv=ops.mlx_argv("switch", target.name),
                    timeout_s=ops.job_timeout_s,
                )
            )
    if target.kind == "comfyui" and not comfy_running:
        steps += _start_comfy(ops)
    if target.kind == "strata" and (not strata_running or repack):
        steps += _start_strata(ops, target.name)
    unrestored: list[str] = []
    if (stop_comfy or stop_strata) and not exclusive:
        for spec in restore:
            if target.kind == "mlx" and not spec.allows(target.name):
                unrestored.append(spec.name)
                continue
            steps += _start_service(ops, spec)
    if not steps:
        raise TransitionRefused(
            409, f"{target.kind if target.kind != 'mlx' else target.name} is already active"
        )
    rollback: list[Step] = []
    if active in ("mlx", *EXCLUSIVE) and (target.kind not in (active, "off") or repack):
        rollback = _stop_engine(ops, target.kind, optional=True)
        if active == "mlx":
            rollback += _start_mlx(ops, mlx_name)
            for spec in for_exclusive:
                rollback += _start_service(ops, spec)
        elif active == "comfyui":
            rollback += _start_comfy(ops)
        else:
            rollback += _start_strata(ops, strata_pack)
    from_label = {"mlx": mlx_name, "comfyui": "comfyui", "strata": "strata", "conflict": "conflict"}.get(
        active
    )
    to_label = target.name if target.kind == "mlx" else None if target.kind == "off" else target.kind
    return Transition(
        steps,
        rollback,
        source_stopped_at,
        from_label,
        to_label,
        forced_stops=tuple(s.name for s in blocked),
        unrestored=tuple(unrestored),
    )
