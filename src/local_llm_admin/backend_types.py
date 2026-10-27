"""Neutral engine contracts. Runtime imports deliberately use only the standard library."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .chat import ChatTarget
    from .jobs import Step


@dataclass(frozen=True)
class EngineTarget:
    kind: str
    name: str | None = None

    def serialize(self) -> str:
        return f"{self.kind}:{self.name}" if self.name is not None else self.kind


@dataclass(frozen=True)
class EngineSpec:
    id: str
    label: str
    enabled: bool
    operations: frozenset[
        Literal["chat", "quality", "multi-model", "switch", "logs", "queue", "profiles"]
    ]
    stop_timeout_s: float
    ready_timeout_s: float
    job_timeout_s: float
    exclusive: bool = True
    model_sidecars: bool = False
    auto_recover: bool = False
    ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class EngineObservation:
    backend: str
    reference: str | None = None
    generation: Mapping[str, Any] | None = None
    state: Literal["absent", "starting", "ready", "released", "recovering", "unknown"] = "unknown"
    ready: bool | None = None
    ownership: Literal["verified", "unknown"] = "unknown"
    # Identity and serving readiness are independent; HTTP alone cannot grant ownership.
    identity: Literal["verified", "unknown"] = "unknown"

    @property
    def running(self) -> bool:
        return self.state != "absent"


@dataclass(frozen=True)
class InferenceDescriptor:
    target: ChatTarget = field(repr=False)
    defaults: Mapping[str, Any] = field(default_factory=dict)
    context_field: str = "loaded_context_size"
    current_only: bool = False
    model_field: str = "loaded_model"

    def context(self, health: Any) -> int | None:
        value = health.get(self.context_field) if isinstance(health, dict) else None
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        )

    def served_model(self, health: Any) -> str | None:
        value = health.get(self.model_field) if isinstance(health, dict) else None
        return value if isinstance(value, str) and value else None


class EngineBackend(Protocol):
    @property
    def spec(self) -> EngineSpec: ...
    @property
    def restart_on_change(self) -> bool: ...
    def validate(self, target: EngineTarget) -> None: ...
    def targets(self) -> Sequence[EngineTarget]: ...
    def intent(self, target: EngineTarget) -> str: ...
    def observe(self) -> EngineObservation: ...
    async def details(self, *, job_running: bool = False) -> Mapping[str, Any]: ...
    def start_steps(self, target: EngineTarget) -> list[Step]: ...
    def switch_steps(self, target: EngineTarget) -> list[Step]: ...
    def stop_steps(self, optional: bool = False) -> list[Step]: ...
    def inference(self, model: str | None = None) -> InferenceDescriptor | None: ...
    def inference_ready(self, health: Any) -> bool: ...
    def compatibility_info(self, health: Any, model_info: Mapping[str, Any]) -> dict[str, Any]: ...
    async def serving_inference(self) -> InferenceDescriptor | None: ...
    async def queue_count(self) -> int | None: ...
    def status_observation(self, details: Mapping[str, Any]) -> EngineObservation: ...
    def stop_preparation(self, queue: int | None, force: bool) -> list[Step]: ...


class BackendLookup(Protocol):
    def get(self, backend: str) -> EngineBackend: ...
    def validate(self, target: EngineTarget) -> None: ...


def resolve_observations(observations: Sequence[EngineObservation]) -> str:
    running = [o for o in observations if o.running]
    if any(o.ownership != "verified" or o.state == "unknown" for o in running):
        return "unknown"
    if len(running) > 1:
        return "conflict"
    return running[0].backend if running else "none"
