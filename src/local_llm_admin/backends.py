"""Static per-application adapters over the existing owning clients.

Adapters describe their own operations; the coordinator owns reservations, intent,
sidecars and execution. Properties read the current Settings rather than freezing flags.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import psutil

from .backend_types import (
    EngineBackend,
    EngineObservation,
    EngineSpec,
    EngineTarget,
    InferenceDescriptor,
    resolve_observations,
)
from .chat import ACTIVE_MODEL_ID, STRATA_CHAT, ChatTarget
from .config import Settings
from .engines import STRATA_PACKS, ComfyClient, StrataClient, TransitionRefused
from .jobs import Step
from .mlx import MlxClient, build_command, is_serving, validate_name


def process_generation(pid: int | None) -> dict[str, Any] | None:
    """A PID alone never identifies a generation."""
    if pid is None:
        return None
    try:
        return {"pid": pid, "started": psutil.Process(pid).create_time()}
    except (psutil.Error, ValueError):
        return None


def _observation(
    backend: str, running: bool, pid: int | None, pidfile: Path, reference: str | None = None
) -> EngineObservation:
    generation = process_generation(pid) if running else None
    owned = False
    if generation is not None:
        try:
            # A stale PID file cannot claim a process created after it was written.
            owned = generation["started"] <= pidfile.stat().st_mtime
        except OSError:
            pass
    return EngineObservation(
        backend,
        reference,
        generation,
        "starting" if owned else "unknown" if running else "absent",
        ownership="verified" if owned else "unknown",
        identity="verified" if owned else "unknown",
    )


class MlxBackend:
    restart_on_change = False

    def __init__(self, settings: Settings, client: MlxClient) -> None:
        self.settings, self.client = settings, client

    @property
    def spec(self) -> EngineSpec:
        s = self.settings
        return EngineSpec(
            "mlx",
            "MLX",
            True,
            frozenset({"chat", "quality", "multi-model", "switch", "logs"}),
            s.engine_stop_timeout_s,
            s.admin_job_timeout_s,
            s.admin_job_timeout_s,
            exclusive=False,
            model_sidecars=True,
            auto_recover=True,
            ports=(s.mlx_port, s.chat_ui_port),
        )

    def validate(self, target: EngineTarget) -> None:
        if target.kind != self.spec.id or target.name is None:
            raise ValueError("MLX requires a registered model name")
        validate_name(target.name)

    def intent(self, target: EngineTarget) -> str:
        if target.name is None:
            return "unknown"
        self.validate(target)
        return target.serialize()

    def targets(self) -> Sequence[EngineTarget]:
        return [EngineTarget("mlx", str(m["name"])) for m in self.client.list_models()]

    def observe(self) -> EngineObservation:
        observation = _observation(
            "mlx",
            self.client.engine_running(),
            self.client.pid_from_file(),
            self.settings.run_dir / "mlx-server.pid",
            self.client.active_env().get("MLX_ACTIVE_NAME"),
        )
        if observation.running and observation.generation is None:
            # Chat UI owns a loaded model even with the API down and no API PID file.
            pid = self.client.owned_listener_pid(
                self.settings.chat_ui_port
            ) or self.client.owned_listener_pid(self.settings.mlx_port)
            generation = process_generation(pid)
            if generation:
                observation = replace(
                    observation,
                    generation=generation,
                    state="starting",
                    ownership="verified",
                    identity="verified",
                )
        return observation

    async def details(self, *, job_running: bool = False) -> Mapping[str, Any]:
        status = await self.client.status(job_running=job_running)
        return {**status, "ready": status["state"] == "serving", "reference": status["selected"]}

    def start_steps(self, target: EngineTarget) -> list[Step]:
        # A missing reference is allowed only for restoration of the CLI-selected model.
        if target.kind != self.spec.id:
            raise ValueError(f"invalid MLX target: {target.kind}")
        if target.name is not None:
            self.validate(target)
        label = f"mlx-model start {target.name}" if target.name else "mlx-model start"
        return [
            Step(
                label,
                argv=build_command(self.settings.mlx_model_path, "start", target.name),
                timeout_s=self.spec.job_timeout_s,
            )
        ]

    def switch_steps(self, target: EngineTarget) -> list[Step]:
        self.validate(target)
        return [
            Step(
                f"mlx-model switch {target.name}",
                argv=build_command(self.settings.mlx_model_path, "switch", target.name),
                timeout_s=self.spec.job_timeout_s,
            )
        ]

    def stop_steps(self, optional: bool = False) -> list[Step]:
        return [
            Step(
                "mlx-model stop",
                argv=build_command(self.settings.mlx_model_path, "stop", None),
                timeout_s=self.spec.job_timeout_s,
                optional=optional,
            ),
            Step(
                "wait for MLX to exit",
                wait=lambda: not self.client.engine_running(),
                timeout_s=self.spec.stop_timeout_s,
            ),
        ]

    def inference(self, model: str | None = None) -> InferenceDescriptor:
        return InferenceDescriptor(ChatTarget(self.settings.mlx_api_base, ACTIVE_MODEL_ID))

    def inference_ready(self, health: Any) -> bool:
        return isinstance(health, dict) and is_serving(health)

    def compatibility_info(self, health: Any, model_info: Mapping[str, Any]) -> dict[str, Any]:
        health = health if isinstance(health, dict) else {}
        return {"tool_parser": health.get("loaded_tool_parser"), "vision": model_info.get("vision")}

    async def serving_inference(self) -> InferenceDescriptor:
        if not self.inference_ready(await self.client.health()):
            raise BackendUnavailable("model server is not serving")
        return self.inference()

    async def queue_count(self) -> int | None:
        return None

    def status_observation(self, details: Mapping[str, Any]) -> EngineObservation:
        return self.observe()

    def stop_preparation(self, queue: int | None, force: bool) -> list[Step]:
        return []


class ComfyBackend:
    restart_on_change = True

    def __init__(self, settings: Settings, client: ComfyClient) -> None:
        self.settings, self.client = settings, client

    @property
    def spec(self) -> EngineSpec:
        s = self.settings
        return EngineSpec(
            "comfyui",
            "ComfyUI",
            True,
            frozenset({"queue", "logs"}),
            s.engine_stop_timeout_s,
            s.comfyui_ready_timeout_s,
            s.admin_job_timeout_s,
            ports=(s.comfyui_port,),
        )

    def validate(self, target: EngineTarget) -> None:
        if target.kind != self.spec.id or target.name is not None:
            raise ValueError("ComfyUI has no target reference")

    def intent(self, target: EngineTarget) -> str:
        self.validate(target)
        return target.serialize()

    def targets(self) -> Sequence[EngineTarget]:
        return [EngineTarget("comfyui")]

    def observe(self) -> EngineObservation:
        proc = self.client.running()
        return _observation(
            "comfyui", proc.running, proc.pid, self.settings.comfyui_root / "run" / "comfyui.pid"
        )

    async def details(self, *, job_running: bool = False) -> Mapping[str, Any]:
        return await self.client.status()

    def start_steps(self, target: EngineTarget) -> list[Step]:
        self.validate(target)
        return [
            Step(
                "comfyui start", argv=self.client.argv("start"), timeout_s=self.spec.job_timeout_s
            ),
            Step(
                "wait for ComfyUI to be ready",
                wait=self.client.ready_sync,
                timeout_s=self.spec.ready_timeout_s,
            ),
        ]

    def switch_steps(self, target: EngineTarget) -> list[Step]:
        raise ValueError(f"{self.spec.label} does not support an in-engine switch")

    def stop_steps(self, optional: bool = False) -> list[Step]:
        return [
            Step(
                "comfyui stop",
                argv=self.client.argv("stop"),
                timeout_s=self.spec.job_timeout_s,
                optional=optional,
            ),
            Step(
                "wait for :8188 to close",
                wait=self.client.down_sync,
                timeout_s=self.spec.stop_timeout_s,
            ),
        ]

    def inference(self, model: str | None = None) -> None:
        return None

    def inference_ready(self, health: Any) -> bool:
        return False

    def compatibility_info(self, health: Any, model_info: Mapping[str, Any]) -> dict[str, Any]:
        raise ValueError("ComfyUI does not support quality inference")

    async def serving_inference(self) -> None:
        return None

    async def queue_count(self) -> int | None:
        queue = await self.client.queue()
        return sum(queue) if queue is not None else None

    def status_observation(self, details: Mapping[str, Any]) -> EngineObservation:
        return _observation(
            "comfyui",
            bool(details["running"]),
            details["pid"],
            self.settings.comfyui_root / "run" / "comfyui.pid",
        )

    def stop_preparation(self, queue: int | None, force: bool) -> list[Step]:
        if not force and (queue is None or queue > 0):
            message = (
                "ComfyUI queue state is unknown (its API is not answering); confirm to stop it"
                " and cancel any queued or running generation"
                if queue is None
                else f"ComfyUI has {queue} queued or running generation(s); confirm to cancel them"
            )
            raise TransitionRefused(409, message)
        if queue is None:
            return [
                Step(
                    "cancel ComfyUI queue (queue state was unknown)",
                    call=self.client.cancel_all_sync,
                    optional=True,
                )
            ]
        if queue > 0:
            return [Step("cancel ComfyUI queue", call=self.client.cancel_all_sync)]
        return []


class StrataBackend:
    restart_on_change = True

    def __init__(self, settings: Settings, client: StrataClient) -> None:
        self.settings, self.client = settings, client

    @property
    def spec(self) -> EngineSpec:
        s = self.settings
        return EngineSpec(
            "strata",
            "Strata",
            s.strata_enabled,
            frozenset({"chat", "quality", "logs", "profiles"}),
            s.engine_stop_timeout_s,
            s.strata_ready_timeout_s,
            s.admin_job_timeout_s,
            ports=(s.strata_port,),
        )

    def validate(self, target: EngineTarget) -> None:
        if (
            target.kind != self.spec.id
            or target.name is not None
            and target.name not in STRATA_PACKS
        ):
            raise ValueError(f"unsupported strata pack: {target.name!r}")

    def intent(self, target: EngineTarget) -> str:
        self.validate(target)
        return self.spec.id  # retain the existing intentionally lossy pack intent

    def targets(self) -> Sequence[EngineTarget]:
        return [EngineTarget("strata"), *(EngineTarget("strata", p) for p in STRATA_PACKS)]

    def observe(self) -> EngineObservation:
        proc = self.client.running()
        return _observation(
            "strata", proc.running, proc.pid, self.settings.strata_root / "run" / "strata.pid"
        )

    async def details(self, *, job_running: bool = False) -> Mapping[str, Any]:
        status = await self.client.status()
        return {**status, "reference": status["pack"]}

    def start_steps(self, target: EngineTarget) -> list[Step]:
        self.validate(target)
        label = f"strata start {target.name}" if target.name else "strata start"
        return [
            Step(
                label,
                argv=self.client.argv("start", target.name),
                timeout_s=self.spec.job_timeout_s,
            ),
            Step(
                "wait for Strata to be ready",
                wait=self.client.ready_sync,
                timeout_s=self.spec.ready_timeout_s,
            ),
        ]

    def switch_steps(self, target: EngineTarget) -> list[Step]:
        raise ValueError(f"{self.spec.label} does not support an in-engine switch")

    def stop_steps(self, optional: bool = False) -> list[Step]:
        return [
            Step(
                "strata stop",
                argv=self.client.argv("stop"),
                timeout_s=self.spec.job_timeout_s,
                optional=optional,
            ),
            Step(
                "wait for Strata to exit",
                wait=self.client.down_sync,
                timeout_s=self.spec.stop_timeout_s,
            ),
        ]

    def inference(self, model: str | None = None) -> InferenceDescriptor:
        return InferenceDescriptor(
            ChatTarget(
                self.settings.strata_api_base,
                model or "",
                self.settings.strata_api_key.get_secret_value(),
            ),
            defaults=MappingProxyType(STRATA_CHAT),
            context_field="max_context",
            current_only=True,
            model_field="model",
        )

    def inference_ready(self, health: Any) -> bool:
        return (
            isinstance(health, dict)
            and health.get("service") == "strata"
            and health.get("loaded") is True
        )

    def compatibility_info(self, health: Any, model_info: Mapping[str, Any]) -> dict[str, Any]:
        health = health if isinstance(health, dict) else {}
        return {
            "tool_parser": "strata",
            "vision": health.get("images") is True,
            "model_id": self.inference().served_model(health),
            "engine": self.spec.id,
            "template_kwargs": True,
        }

    async def serving_inference(self) -> InferenceDescriptor:
        details = await self.client.status()
        if not details["ready"] or not details["model"]:
            raise BackendUnavailable("Strata is still loading the model")
        return self.inference(details["model"])

    async def queue_count(self) -> int | None:
        return None

    def status_observation(self, details: Mapping[str, Any]) -> EngineObservation:
        return _observation(
            "strata",
            bool(details["running"]),
            details["pid"],
            self.settings.strata_root / "run" / "strata.pid",
            details.get("pack"),
        )

    def stop_preparation(self, queue: int | None, force: bool) -> list[Step]:
        return []


class BackendUnavailable(ValueError):
    """Inference requires an unambiguous, owned and serving LLM backend."""


class BackendDisabled(ValueError):
    """A recognized engine is unavailable for activation."""


class BackendRegistry:
    """Immutable membership, live adapter configuration. No unknown-ID fallback."""

    def __init__(self, backends: Iterable[EngineBackend]) -> None:
        entries: dict[str, EngineBackend] = {}
        for backend in backends:
            key = backend.spec.id
            if key in entries:
                raise ValueError(f"duplicate backend ID: {key}")
            if not key or ":" in key or key in {"off", "none", "unknown", "conflict"}:
                raise ValueError(f"invalid backend ID: {key!r}")
            entries[key] = backend
        self._entries: Mapping[str, EngineBackend] = MappingProxyType(entries)

    def __iter__(self) -> Iterator[EngineBackend]:
        return iter(self._entries.values())

    def get(self, backend: str) -> EngineBackend:
        try:
            return self._entries[backend]
        except KeyError:
            raise ValueError(f"unknown backend ID: {backend!r}") from None

    def validate(self, target: EngineTarget) -> None:
        if target.kind == "off":
            if target.name is not None:
                raise ValueError("off has no target reference")
            return
        backend = self.get(target.kind)
        backend.validate(target)
        if not backend.spec.enabled:
            raise BackendDisabled(f"the {backend.spec.label} engine is not enabled")

    def parse(self, raw: str) -> EngineTarget:
        kind, sep, name = raw.partition(":")
        target = EngineTarget(kind, name if sep else None)
        self.validate(target)
        return target

    def observe(self, *, include_disabled: bool = False) -> list[EngineObservation]:
        observations = [b.observe() for b in self if include_disabled or b.spec.enabled]
        return self._check_listeners(observations)

    def _check_listeners(
        self, observations: Sequence[EngineObservation]
    ) -> list[EngineObservation]:
        """A valid PID file does not claim an unrelated listener on the same port.

        This belongs to the control observation path, not selector status refreshes.
        Shared ports may belong to another verified registered owner. On systems that
        restrict the global socket table, inspect the known processes' own sockets and
        refuse any remaining listening resource whose owner cannot be established.
        """
        known: dict[int, set[int]] = {}
        for observation in observations:
            generation = observation.generation
            if observation.ownership != "verified" or generation is None:
                continue
            for port in self.get(observation.backend).spec.ports:
                known.setdefault(port, set()).add(int(generation["pid"]))
        if not known:
            return list(observations)
        ports = {port for b in self for port in b.spec.ports}
        try:
            unknown = {
                c.laddr.port
                for c in psutil.net_connections(kind="tcp")
                if c.status == psutil.CONN_LISTEN
                and c.laddr
                and c.laddr.port in ports
                and c.pid not in known.get(c.laddr.port, set())
            }
        except (psutil.Error, OSError):
            owned: set[int] = set()
            for observation in observations:
                if observation.ownership != "verified" or observation.generation is None:
                    continue
                try:
                    connections = psutil.Process(
                        int(observation.generation["pid"])
                    ).net_connections(kind="tcp")
                except (psutil.Error, OSError):
                    continue
                owned.update(
                    c.laddr.port
                    for c in connections
                    if c.status == psutil.CONN_LISTEN
                    and c.laddr
                    and c.laddr.port in self.get(observation.backend).spec.ports
                )
            unknown = {port for port in ports - owned if MlxClient.port_listening(port)}
        return [
            replace(o, state="unknown", ownership="unknown")
            if unknown.intersection(self.get(o.backend).spec.ports)
            else o
            for o in observations
        ]

    async def status_details(self, *, job_running: bool) -> dict[str, Mapping[str, Any]]:
        return {b.spec.id: await b.details(job_running=job_running) for b in self if b.spec.enabled}

    def from_status(self, details: Mapping[str, Mapping[str, Any]]) -> list[EngineObservation]:
        observations = []
        for kind, status in details.items():
            backend = self.get(kind)
            if not backend.spec.enabled:
                continue
            observation = backend.status_observation(status)
            ready = status.get("ready")
            if observation.ownership == "verified":
                observation = replace(
                    observation, ready=bool(ready), state="ready" if ready else "starting"
                )
            observations.append(observation)
        return observations

    async def resolve_inference(
        self, profile_defaults: Callable[[str | None], Mapping[str, Any]] | None = None
    ) -> InferenceDescriptor:
        observations = await asyncio.to_thread(self.observe, include_disabled=True)
        active = resolve_observations(observations)
        if active in {"none", "unknown", "conflict"}:
            raise BackendUnavailable(
                "model server is not serving" if active == "none" else f"engine is {active}"
            )
        backend = self.get(active)
        if not backend.spec.enabled:
            raise BackendUnavailable(f"the {backend.spec.label} engine is not enabled")
        if "chat" not in backend.spec.operations:
            raise BackendUnavailable(f"{backend.spec.label} owns the GPU; the LLM is stopped")
        descriptor = await backend.serving_inference()
        if descriptor is None:
            raise BackendUnavailable(f"{backend.spec.label} does not support LLM chat")
        if profile_defaults and "multi-model" in backend.spec.operations:
            observation = next(o for o in observations if o.backend == active)
            defaults = await asyncio.to_thread(profile_defaults, observation.reference)
            descriptor = replace(descriptor, defaults=defaults)
        return descriptor

    def exclusive_labels(self, observations: Sequence[EngineObservation]) -> list[str]:
        return [
            self.get(o.backend).spec.label
            for o in observations
            if o.running and self.get(o.backend).spec.exclusive
        ]

    def recovery_eligible(self, target: str) -> bool:
        try:
            parsed = self.parse(target)
            return parsed.kind != "off" and self.get(parsed.kind).spec.auto_recover
        except ValueError:
            return False

    async def observed_details(
        self, observations: Sequence[EngineObservation], *, lifecycle: bool = False
    ) -> list[EngineObservation]:
        """Only running, enabled engines need bounded readiness/details probes."""
        result = []
        for observation in observations:
            backend = self.get(observation.backend)
            if (
                not observation.running
                or observation.ownership != "verified"
                or not backend.spec.enabled
                or lifecycle
                and "profiles" not in backend.spec.operations
            ):
                result.append(observation)
                continue
            details = await backend.details()
            ready = details.get("ready")
            result.append(
                replace(
                    observation,
                    reference=details.get("reference", observation.reference),
                    ready=bool(ready),
                    state="ready" if ready else "starting",
                )
            )
        return result


def build_registry(
    settings: Settings, mlx: MlxClient, comfy: ComfyClient, strata: StrataClient
) -> BackendRegistry:
    # Stop order in a verified conflict remains ComfyUI, Strata, then MLX.
    return BackendRegistry(
        (ComfyBackend(settings, comfy), StrataBackend(settings, strata), MlxBackend(settings, mlx))
    )
