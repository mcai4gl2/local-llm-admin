"""Complete process bundles participate in listener and execution ownership."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from local_llm_admin.backend_types import (
    EngineObservation,
    EngineTarget,
    OwnedProcess,
    generation_record,
    owned_pids,
)
from local_llm_admin.backends import BackendRegistry, MlxBackend
from local_llm_admin.control import ControlService
from local_llm_admin.engines import Transition
from local_llm_admin.mlx import MlxClient

from .test_backends import FourthBackend


def observation() -> EngineObservation:
    members = (
        OwnedProcess(100, 1.0, "/runtime/python", "controller"),
        OwnedProcess(101, 2.0, "/runtime/engine/splash", "native"),
    )
    return EngineObservation(
        "fourth",
        "alpha",
        {"pid": 100, "started": 1.0},
        "ready",
        True,
        "verified",
        "verified",
        members,
    )


def test_bundle_generation_preserves_legacy_primary_and_detects_child_replacement() -> None:
    obs = observation()
    record = generation_record(obs)
    assert record["pid"] == 100
    assert {p["pid"] for p in record["processes"]} == {100, 101}
    assert owned_pids(obs) == (100, 101)
    assert generation_record(replace(obs, processes=obs.processes[:1])) != record
    assert owned_pids(replace(obs, ownership="unknown")) == ()
    assert generation_record(EngineObservation("fourth")) is None
    assert owned_pids(EngineObservation("fourth")) == ()
    legacy = replace(obs, processes=())
    assert generation_record(legacy) == {"pid": 100, "started": 1.0}
    assert owned_pids(legacy) == (100,)


@pytest.mark.parametrize(
    ("pid", "started"), [(0, 1.0), (-1, 1.0), (True, 1.0), (1, False), (1, float("nan")), (1, -1)]
)
def test_bundle_members_reject_invalid_generation(pid: int, started: float) -> None:
    with pytest.raises(ValueError):
        OwnedProcess(pid, started, "/runtime/engine/splash", "native")


@pytest.mark.parametrize("restricted", [False, True])
def test_native_child_listener_is_owned_and_foreign_listener_is_refused(
    fake_mlx: Any, monkeypatch: pytest.MonkeyPatch, restricted: bool
) -> None:
    settings = fake_mlx.settings
    registry = BackendRegistry((MlxBackend(settings, MlxClient(settings)),))
    obs = replace(observation(), backend="mlx")
    conn = SimpleNamespace(
        laddr=SimpleNamespace(port=settings.mlx_port), status=psutil.CONN_LISTEN, pid=101
    )
    if restricted:

        def blocked(**kwargs: Any) -> None:
            raise psutil.AccessDenied()

        monkeypatch.setattr(psutil, "net_connections", blocked)
        monkeypatch.setattr(
            psutil,
            "Process",
            lambda pid: SimpleNamespace(
                net_connections=lambda **kwargs: [conn] if pid == 101 else []
            ),
        )
        monkeypatch.setattr(
            MlxClient, "port_listening", staticmethod(lambda port: port == settings.mlx_port)
        )
    else:
        monkeypatch.setattr(psutil, "net_connections", lambda **kwargs: [conn])
    assert registry._check_listeners([obs])[0].ownership == "verified"
    if not restricted:
        conn.pid = 999
        assert registry._check_listeners([obs])[0].ownership == "unknown"


def test_execution_guard_refuses_native_replacement_under_same_parent(
    fake_mlx: Any, fake_comfy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_llm_admin.engines import ComfyClient
    from local_llm_admin.jobs import JobRunner, JobStore

    s = fake_mlx.settings
    mlx = MlxClient(s)
    backend = FourthBackend(s, mlx)
    registry = BackendRegistry((backend,))
    store = JobStore(s.admin_data_dir / "bundle.sqlite")
    runner = JobRunner(s, store, current_name=lambda: None)
    control = ControlService(s, store, runner, mlx, ComfyClient(s), backends=registry)
    previous = observation()
    current = [previous]
    monkeypatch.setattr(registry, "observe", lambda **kwargs: current)
    plan = Transition(
        steps=backend.stop_steps(),
        rollback=[],
        rollback_after=0,
        from_label="alpha",
        to_label="off",
    )
    guarded = control._guard_transition(plan, [previous], EngineTarget("off"))
    assert guarded.steps[0].label == "verify engine ownership"
    guarded.steps[0].call()
    current[:] = [
        replace(
            previous,
            processes=(
                previous.processes[0],
                OwnedProcess(102, 3.0, "/runtime/engine/splash", "native"),
            ),
        )
    ]
    with pytest.raises(RuntimeError, match="generation changed"):
        guarded.steps[0].call()
    monkeypatch.setattr(mlx, "drive_mounted", lambda: True)
    snap, _ = control._snapshot()
    assert snap["generation"] == generation_record(current[0])


@pytest.mark.parametrize(
    ("executable", "role"), [("relative", "native"), ("", "native"), ("/runtime/python", "other")]
)
def test_bundle_members_require_executable_identity_and_role(executable: str, role: str) -> None:
    with pytest.raises(ValueError):
        OwnedProcess(100, 1.0, executable, role)


def test_restricted_socket_visibility_refuses_when_native_socket_is_unknown(
    fake_mlx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = BackendRegistry((MlxBackend(fake_mlx.settings, MlxClient(fake_mlx.settings)),))
    obs = replace(observation(), backend="mlx")

    def denied(*args: Any, **kwargs: Any) -> None:
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "net_connections", denied)
    monkeypatch.setattr(psutil, "Process", denied)
    monkeypatch.setattr(
        MlxClient, "port_listening", staticmethod(lambda port: port == fake_mlx.settings.mlx_port)
    )
    assert registry._check_listeners([obs])[0].ownership == "unknown"


def test_mutating_engine_command_without_guard_is_refused(
    fake_mlx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_llm_admin.engines import ComfyClient
    from local_llm_admin.jobs import JobRunner, JobStore, Step

    s = fake_mlx.settings
    mlx = MlxClient(s)
    backend = FourthBackend(s, mlx)
    registry = BackendRegistry((backend,))
    store = JobStore(s.admin_data_dir / "guard.sqlite")
    control = ControlService(
        s,
        store,
        JobRunner(s, store, current_name=lambda: None),
        mlx,
        ComfyClient(s),
        backends=registry,
    )
    executable = backend.stop_steps()[0].argv[0]
    plan = Transition(
        [Step("unrecognized mutation", argv=[executable, "unrecognized"])], [], 0, None, None
    )
    with pytest.raises(RuntimeError, match="no ownership guard"):
        control._guard_transition(plan, [observation()], EngineTarget("off"))
