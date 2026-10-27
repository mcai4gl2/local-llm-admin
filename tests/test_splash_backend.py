"""Splash client and adapter use private profiles, owning records and pinned HTTP."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import psutil
import pytest

from local_llm_admin.backend_types import EngineTarget, generation_record
from local_llm_admin.backends import BackendRegistry, SplashBackend
from local_llm_admin.config import Settings
from local_llm_admin.splash import SplashClient

from .test_splash import catalog, write


class Processes:
    def __init__(self, root: Path, prepared: Path) -> None:
        self.table = {
            100: {
                "started": 1.0,
                "exe": str(root / "runtime/python/bin/python3"),
                "args": [
                    str(root / "runtime/python/bin/python3"),
                    "-P",
                    "-m",
                    "server.server",
                    str(prepared),
                ],
                "ppid": 1,
            },
            101: {
                "started": 2.0,
                "exe": str(root / "runtime/engine/splash"),
                "args": [str(root / "runtime/engine/splash"), "serve-native", str(prepared)],
                "ppid": 100,
            },
        }

    def process(self, pid: int) -> Any:
        if pid not in self.table:
            raise psutil.NoSuchProcess(pid)
        table = self

        class Proc:
            def __init__(self) -> None:
                self.pid = pid

            def create_time(self) -> float:
                return table.table[pid]["started"]

            def exe(self) -> str:
                return table.table[pid]["exe"]

            def cmdline(self) -> list[str]:
                return table.table[pid]["args"]

            def ppid(self) -> int:
                return table.table[pid]["ppid"]

            def status(self) -> str:
                return psutil.STATUS_RUNNING

            def children(self, recursive: bool = False) -> list[Any]:
                return [table.process(p) for p, item in table.table.items() if item["ppid"] == pid]

        return Proc()


@pytest.fixture
def splash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "splash"
    path, doc = catalog(root)
    s = Settings(splash_enabled=True, splash_root=root, splash_api_key="k" * 40)
    prepared = root / "prepared/stock-8bit-32k"
    processes = Processes(root, prepared)
    monkeypatch.setattr(psutil, "Process", processes.process)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: [])
    profile = doc["profiles"]["stock-8bit-32k"]
    from local_llm_admin.splash_profiles import load_profiles

    fp = load_profiles(path, root)["stock-8bit-32k"].fingerprint
    record = {
        "version": 1,
        "profile": "stock-8bit-32k",
        "fingerprint": fp,
        "model": profile["model"],
        "prepared": str(prepared),
        "controller": {
            "pid": 100,
            "started": 1.0,
            "executable": processes.table[100]["exe"],
            "role": "controller",
        },
        "native": [
            {
                "pid": 101,
                "started": 2.0,
                "executable": processes.table[101]["exe"],
                "role": "native",
            }
        ],
    }
    (root / "run").mkdir()
    write(root / "run/splash.json", record)
    calls = []
    bodies = {
        "/ready": {"status": "ready"},
        "/status": {
            "ready": True,
            "maximum_context_tokens": 32768,
            "instance": {"pid": 100, "model": profile["model"]},
            "kv": {"format": "bf16"},
            "state_format": "fp32",
            "weights": {},
        },
        "/v1/models": {
            "data": [
                {"id": name, "max_model_len": 32768}
                for name in [profile["model"], *profile["aliases"]]
            ]
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        assert req.headers["Authorization"] == "Bearer " + "k" * 40
        return httpx.Response(200, json=bodies[req.url.path])

    client = SplashClient(s, transport=httpx.MockTransport(handler))
    return SimpleNamespace(
        root=root,
        settings=s,
        client=client,
        backend=SplashBackend(s, client),
        processes=processes,
        record=record,
        bodies=bodies,
        calls=calls,
        path=path,
        doc=doc,
    )


def test_default_disable_and_explicit_profile_targets(splash: Any) -> None:
    assert Settings().splash_enabled is False
    registry = BackendRegistry((splash.backend,))
    assert registry.parse("splash:stock-8bit-32k").name == "stock-8bit-32k"
    for target in ("splash", "splash:unknown", "splash:../stock", "splash:"):
        with pytest.raises(ValueError):
            registry.parse(target)
    splash.settings.splash_enabled = False
    with pytest.raises(ValueError):
        registry.parse("splash:stock-8bit-32k")
    assert (
        splash.backend.intent(EngineTarget("splash", "stock-8bit-32k")) == "splash:stock-8bit-32k"
    )


def test_complete_bundle_observation_and_wrapper_steps(splash: Any) -> None:
    obs = splash.client.observe()
    assert obs.ownership == "verified" and obs.reference == "stock-8bit-32k"
    assert {p.pid for p in obs.processes} == {100, 101}
    assert obs.generation["pid"] == 100
    backend = splash.backend
    assert backend.spec.exclusive and not backend.spec.model_sidecars
    assert not backend.spec.auto_recover
    assert backend.inference().current_only
    assert "multi-model" not in backend.spec.operations
    target = EngineTarget("splash", "stock-8bit-32k")
    assert backend.start_steps(target)[0].argv == [
        str(splash.root / "bin/splash"),
        "start",
        "stock-8bit-32k",
    ]
    assert backend.stop_steps(True)[0].optional and not backend.stop_steps(True)[1].optional
    with pytest.raises(ValueError):
        backend.switch_steps(target)


@pytest.mark.parametrize(
    "mutation", ["native-exe", "native-reused", "parent-reused", "profile-change", "bad-record"]
)
def test_unknown_or_changed_ownership_is_refused(splash: Any, mutation: str) -> None:
    if mutation == "native-exe":
        splash.processes.table[101]["exe"] = "/usr/bin/sleep"
    if mutation == "native-reused":
        splash.processes.table[101]["started"] = 5.0
    if mutation == "parent-reused":
        splash.processes.table[100]["started"] = 5.0
    if mutation == "profile-change":
        splash.doc["profiles"]["stock-8bit-32k"]["memory_gib"] = 39
        write(splash.path, splash.doc)
    if mutation == "bad-record":
        (splash.root / "run/splash.json").write_text("{}")
    assert splash.client.observe().state == "unknown"


def test_parent_death_preserves_recorded_native_owner(splash: Any) -> None:
    del splash.processes.table[100]
    obs = splash.client.observe()
    assert obs.running and obs.ownership == "verified"
    assert {p.pid for p in obs.processes} == {101}
    assert obs.generation["pid"] == 101


def test_verified_recovered_child_changes_generation(splash: Any) -> None:
    before = generation_record(splash.client.observe())
    splash.processes.table[102] = {**splash.processes.table.pop(101), "started": 3.0}
    after = splash.client.observe()
    assert after.ownership == "verified" and {p.pid for p in after.processes} == {100, 102}
    assert generation_record(after) != before


async def test_ready_status_checks_identity_aliases_context_and_auth_once(splash: Any) -> None:
    status = await splash.client.status()
    assert status["ready"] is True and status["state"] == "ready"
    assert status["max_context"] == 32768
    assert splash.calls == ["/ready", "/status", "/v1/models"]
    descriptor = await splash.backend.serving_inference()
    assert descriptor.defaults["enable_thinking"] is True
    assert descriptor.health_path == "/status"
    assert descriptor.served_model(splash.bodies["/status"]) == "example/stock-8bit"
    assert descriptor.context(splash.bodies["/status"]) == 32768
    assert "k" * 40 not in repr(descriptor)


@pytest.mark.parametrize(
    ("field", "value"),
    [("model", "example/other"), ("pid", 999), ("context", 65536), ("aliases", [])],
)
async def test_http_liveness_does_not_establish_loaded_identity(
    splash: Any, field: str, value: Any
) -> None:
    if field in ("model", "pid"):
        splash.bodies["/status"]["instance"][field] = value
    if field == "context":
        splash.bodies["/status"]["maximum_context_tokens"] = value
    if field == "aliases":
        splash.bodies["/v1/models"]["data"] = value
    assert not (await splash.client.status())["ready"]
    with pytest.raises(ValueError):
        await splash.backend.serving_inference()


@pytest.mark.parametrize(
    ("weights", "expected"), [({"released": True}, "released"), ({"restoring": True}, "recovering")]
)
async def test_idle_and_recovery_keep_resource_owner_without_claiming_readiness(
    splash: Any, weights: dict[str, Any], expected: str
) -> None:
    splash.bodies["/status"]["weights"] = weights
    doc = await splash.client.status()
    assert doc["state"] == expected and not doc["ready"] and doc["running"]
    registry = BackendRegistry((splash.backend,))
    detailed = await registry.observed_details([splash.client.observe()], lifecycle=True)
    assert detailed[0].state == expected


async def test_disabled_status_never_reads_pid_or_http_but_lifecycle_still_observes_owner(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    splash.settings.splash_enabled = False
    assert splash.client.observe().running
    monkeypatch.setattr(
        splash.client,
        "observe",
        lambda: (_ for _ in ()).throw(AssertionError("disabled status probed")),
    )
    doc = await splash.client.status()
    assert doc["enabled"] is False and splash.calls == []


def test_disabled_activation_does_not_read_private_profile_inventory(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    splash.settings.splash_enabled = False
    monkeypatch.setattr(
        splash.client,
        "profiles",
        lambda **kw: (_ for _ in ()).throw(AssertionError("disabled profile read")),
    )
    with pytest.raises(ValueError, match="not enabled"):
        BackendRegistry((splash.backend,)).parse("splash:stock-8bit-32k")


async def test_shared_port_is_attributed_to_splash_without_phantom_mlx(
    splash: Any, fake_mlx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_llm_admin.backend_types import resolve_observations
    from local_llm_admin.backends import MlxBackend
    from local_llm_admin.mlx import MlxClient

    s = fake_mlx.settings
    s.mlx_port = splash.settings.splash_port
    mlx = MlxClient(s)
    monkeypatch.setattr(mlx, "engine_running", lambda: True)
    monkeypatch.setattr(mlx, "pid_from_file", lambda: None)
    monkeypatch.setattr(mlx, "owned_listener_pid", lambda port: None)
    monkeypatch.setattr(MlxClient, "port_listening", staticmethod(lambda port: port == s.mlx_port))
    monkeypatch.setattr(
        psutil,
        "net_connections",
        lambda **kwargs: [
            SimpleNamespace(
                laddr=SimpleNamespace(port=s.mlx_port), status=psutil.CONN_LISTEN, pid=100
            )
        ],
    )
    registry = BackendRegistry((MlxBackend(s, mlx), splash.backend))
    assert resolve_observations(registry.observe(include_disabled=True)) == "splash"
    details = {"mlx": {"ready": False}, "splash": await splash.client.status()}
    assert resolve_observations(registry.from_status(details)) == "splash"


@pytest.mark.parametrize(
    "bad",
    ["context-float", "pid-float", "kv-null", "model-id-object", "model-context", "weights-type"],
)
async def test_malformed_status_is_not_loaded(splash: Any, bad: str) -> None:
    if bad == "context-float":
        splash.bodies["/status"]["maximum_context_tokens"] = 32768.0
    if bad == "pid-float":
        splash.bodies["/status"]["instance"]["pid"] = 100.0
    if bad == "kv-null":
        splash.bodies["/status"]["kv"] = None
    if bad == "model-id-object":
        splash.bodies["/v1/models"]["data"][0]["id"] = {}
    if bad == "model-context":
        splash.bodies["/v1/models"]["data"][0]["max_model_len"] = 65536
    if bad == "weights-type":
        splash.bodies["/status"]["weights"] = []
    assert not (await splash.client.status())["ready"]


async def test_status_rejects_oversized_bounded_probe_body(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_llm_admin import splash as module

    monkeypatch.setattr(module, "MAX_STATUS_BYTES", 1000, raising=False)
    splash.bodies["/status"]["padding"] = "x" * 2000
    assert not (await splash.client.status())["ready"]


async def test_failed_http_probe_does_not_claim_readiness(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("unavailable")

    monkeypatch.setattr(splash.client, "transport", httpx.MockTransport(failed))
    assert not (await splash.client.status())["ready"]
    assert not splash.client.ready_sync()


@pytest.mark.parametrize(
    "field,value",
    [
        ("kv", None),
        ("kv", {"format": "fp8"}),
        ("state_format", "fp16"),
        ("weights", []),
        ("maximum_context_tokens", True),
    ],
)
def test_inference_readiness_requires_pinned_formats(splash: Any, field: str, value: Any) -> None:
    health = dict(splash.bodies["/status"])
    assert splash.backend.inference_ready(health)
    health[field] = value
    assert not splash.backend.inference_ready(health)


@pytest.mark.parametrize("mode", ["http", "json", "oversize", "generation", "missing"])
async def test_bounded_probes_refuse_http_and_generation_failures(splash: Any, mode: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if mode == "http":
            return httpx.Response(401)
        if mode == "json":
            return httpx.Response(200, content="not json")
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * 1048577)
        if mode == "generation":
            splash.processes.table[101]["started"] = 3.0
        return httpx.Response(200, json=splash.bodies[req.url.path])

    if mode == "missing":
        splash.processes.table.clear()
    splash.client.transport = httpx.MockTransport(handler)
    assert not (await splash.client.status())["ready"]
    assert not splash.client.ready_sync()


def test_possible_processes_include_managed_orphans_but_not_foreign_commands(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Hidden:
        @property
        def info(self) -> Any:
            raise psutil.AccessDenied()

    entries = [
        Hidden(),
        SimpleNamespace(
            pid=102,
            info={"exe": str(splash.root / "runtime/engine/splash"), "cmdline": ["serve-native"]},
        ),
        SimpleNamespace(pid=103, info={"exe": "/usr/bin/python3", "cmdline": ["server.server"]}),
        SimpleNamespace(
            pid=104,
            info={"exe": str(splash.root / "runtime/python/bin/python3"), "cmdline": ["unrelated"]},
        ),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *args: entries)
    assert splash.client._possible() == {102}
    assert splash.client.observe().state == "unknown"
    (splash.root / "run/splash.json").unlink()
    assert splash.client.observe().state == "unknown"
    splash.settings.splash_root = splash.root / "absent"
    assert splash.client._possible() == set()


def test_zombie_and_disappearing_children_are_not_owned(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        psutil, "Process", lambda pid: SimpleNamespace(status=lambda: psutil.STATUS_ZOMBIE)
    )
    assert splash.client._member(splash.record["controller"], splash.record["prepared"]) is None
    assert splash.client.observe().state == "absent"
    monkeypatch.setattr(psutil, "Process", splash.processes.process)
    original = splash.client._member

    def disappeared(item: Any, prepared: str) -> Any:
        if item["role"] == "native":
            return None
        return original(item, prepared)

    monkeypatch.setattr(splash.client, "_member", disappeared)
    assert {p.pid for p in splash.client.observe().processes} == {100}


def test_private_profile_read_failure_during_normalization_is_unknown(splash: Any) -> None:
    observed = splash.client.observe()
    splash.path.unlink()
    result = splash.client._result(
        observed, [splash.bodies[path] for path in ("/ready", "/status", "/v1/models")]
    )
    assert result["state"] == "unknown" and not result["ready"]
    assert not splash.backend.inference_ready(splash.bodies["/status"])


def test_sync_probe_deadline_and_disabled_owner_never_report_ready(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import itertools

    from local_llm_admin import splash as module

    clock = itertools.count(step=10)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    assert not splash.client.ready_sync()
    splash.settings.splash_enabled = False
    assert not splash.client.ready_sync()
    splash.settings.splash_api_key = ""
    assert splash.client._headers() == {}
    with pytest.raises(ValueError):
        splash.client.argv("restart")


def test_resource_release_requires_both_process_and_listener_exit(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    from local_llm_admin import splash as module
    from local_llm_admin.backend_types import EngineObservation

    assert not splash.client.down_sync()
    monkeypatch.setattr(
        splash.client, "observe", lambda: EngineObservation("splash", state="absent")
    )
    monkeypatch.setattr(
        module.socket, "create_connection", lambda *a, **kw: contextlib.nullcontext()
    )
    assert not splash.client.down_sync()

    def refused(*args: Any, **kwargs: Any) -> None:
        raise OSError()

    monkeypatch.setattr(module.socket, "create_connection", refused)
    assert splash.client.down_sync()


@pytest.mark.parametrize(
    "source,destination",
    [
        ("mlx", "splash"),
        ("strata", "splash"),
        ("comfyui", "splash"),
        ("off", "splash"),
        ("splash", "mlx"),
        ("splash", "strata"),
        ("splash", "comfyui"),
        ("splash", "off"),
        ("splash", "splash"),
    ],
)
def test_splash_transitions_preserve_exit_waits_and_original_profile(
    splash: Any, source: str, destination: str
) -> None:
    from local_llm_admin.backend_types import EngineObservation
    from local_llm_admin.engines import plan_transition

    from .test_engine_plan import Ops, registry

    registry_ = BackendRegistry((*registry(Ops()), splash.backend))
    references = {"mlx": "qwen", "strata": "IQ3_XXS", "comfyui": None, "splash": "stock-8bit-32k"}
    observations = [
        EngineObservation(
            kind,
            references[kind],
            state="ready" if source == kind else "absent",
            ownership="verified",
        )
        for kind in references
    ]
    target = EngineTarget(
        destination, "modified-4bit-32k" if destination == "splash" else references.get(destination)
    )
    plan = plan_transition(
        target,
        observations=observations,
        registry=registry_,
        queues={"comfyui": 0},
        ops=Ops(),
        force=False,
    )
    commands = [step.argv for step in plan.steps if step.argv]
    assert commands[-1][-2:] == ["start", target.name] if destination == "splash" else True
    if source != "off":
        assert "stop" in commands[0]
        assert plan.steps[1].wait is not None
        assert not plan.steps[1].optional
    if source == "splash" and destination != "off":
        restores = [step for step in plan.rollback if step.argv and "start" in step.argv]
        assert restores[-1].argv[-1] == "stock-8bit-32k"
        assert any(
            step.wait is not None and not step.optional
            for step in plan.rollback[: plan.rollback.index(restores[-1])]
        )


async def test_absent_and_not_loaded_splash_never_reports_ready(splash: Any) -> None:
    (splash.root / "run/splash.json").unlink()
    assert splash.client.observe().state == "absent"
    assert not (await splash.client.status())["ready"]
    write(splash.root / "run/splash.json", splash.record)
    splash.bodies["/ready"] = {"status": "loading"}
    splash.bodies["/status"]["ready"] = False
    assert (await splash.client.status())["state"] == "starting"


def test_splash_inference_requires_reported_usage(splash: Any) -> None:
    assert splash.backend.inference().estimate_usage is False


async def test_splash_stream_disconnect_closes_upstream(splash: Any) -> None:
    from local_llm_admin.chat import ChatRequest, stream_chat

    from .fake_openai import FakeOpenAI, sse_chunks

    fake = FakeOpenAI(sse_chunks(["one", "two"]), delay=0.01)
    descriptor = splash.backend.inference()
    request = ChatRequest(messages=[{"role": "user", "content": "hi"}])
    stream = stream_chat(
        splash.settings,
        request,
        transport=fake.transport,
        defaults=dict(descriptor.defaults),
        target=descriptor.target,
    )
    await stream.__anext__()
    await stream.aclose()
    assert fake.stream.closed
    assert fake.stream.sent < 2


async def test_adapter_inventory_failures_and_unavailable_inference_are_explicit(
    splash: Any,
) -> None:
    from local_llm_admin.backends import BackendUnavailable

    assert len(splash.backend.targets()) == 2
    with pytest.raises(ValueError):
        splash.backend.intent(EngineTarget("mlx", "model-a"))
    assert await splash.backend.queue_count() is None
    splash.bodies["/ready"] = {"status": "loading"}
    with pytest.raises(BackendUnavailable):
        await splash.backend.serving_inference()
    observed = splash.client.observe()
    assert splash.backend.status_observation({"state": "starting"}) == observed
    splash.settings.splash_enabled = False
    assert (await splash.backend.details())["target_options"] == []
    splash.settings.splash_enabled = True
    splash.path.unlink()
    assert splash.backend.targets() == []
    assert (await splash.backend.details())["target_options"] == []
    assert not splash.backend.inference_ready(None)
    import contextlib

    from local_llm_admin.backend_types import EngineObservation

    with contextlib.ExitStack() as stack:
        from unittest.mock import patch

        stack.enter_context(
            patch.object(
                splash.client, "observe", return_value=EngineObservation("splash", state="unknown")
            )
        )
        stack.enter_context(patch.object(splash.client, "profiles", return_value={}))
        with pytest.raises(BackendUnavailable):
            splash.backend.inference()


def test_splash_does_not_claim_mlx_availability_or_recovery(splash: Any) -> None:
    from dataclasses import replace

    from local_llm_admin.availability import classify

    from .test_availability import SNAP

    snapshot = replace(SNAP, engine="splash", intent="splash:stock-8bit-32k", mlx_serving=None)
    assert classify(snapshot, None, load_grace_s=300) == ("unknown", None)
    assert not BackendRegistry((splash.backend,)).recovery_eligible("splash:stock-8bit-32k")


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_splash_forwards_route_identity_and_surfaces_runtime_refusals(
    splash: Any, status: int
) -> None:
    from dataclasses import replace

    from local_llm_admin.chat import ChatRequest, stream_chat

    descriptor = splash.backend.inference()
    target = replace(descriptor.target, model="unknown-route-model")

    def refused(request: httpx.Request) -> httpx.Response:
        import json

        assert request.headers["Authorization"] == "Bearer " + "k" * 40
        assert request.headers["Host"] == "127.0.0.1:8080"
        assert json.loads(request.content)["model"] == "unknown-route-model"
        return httpx.Response(status, json={"error": "refused"})

    events = [
        event
        async for event in stream_chat(
            splash.settings,
            ChatRequest(messages=[{"role": "user", "content": "hi"}]),
            transport=httpx.MockTransport(refused),
            target=target,
            defaults=dict(descriptor.defaults),
            estimate_usage=descriptor.estimate_usage,
        )
    ]
    import json

    assert [json.loads(event.removeprefix("data: ")) for event in events] == [
        {"type": "error", "message": "model server returned HTTP " + str(status)}
    ]


def test_inference_readiness_refuses_catalog_race(
    splash: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = splash.client.observe()
    monkeypatch.setattr(splash.client, "observe", lambda: observed)

    def unavailable(**kwargs: Any) -> Any:
        raise ValueError("private inventory unavailable")

    monkeypatch.setattr(splash.client, "profiles", unavailable)
    assert not splash.backend.inference_ready(splash.bodies["/status"])


def test_port_placeholder_without_another_owner_is_preserved(splash: Any) -> None:
    from dataclasses import replace

    observed = replace(splash.client.observe(), ownership="unknown", listener_ports=(8080,))
    result = BackendRegistry((splash.backend,))._settle_listener_placeholders([observed], {8080})
    assert result == [observed]


@pytest.mark.parametrize("state", ["released", "recovering"])
def test_splash_resource_states_reach_root_badge(splash: Any, state: Any) -> None:
    from dataclasses import replace

    from local_llm_admin.engines import observation_badge

    observed = replace(splash.client.observe(), state=state)
    assert observation_badge("stopped", [observed]) == "splash-" + state


@pytest.mark.parametrize("target", ["unknown:profile", "splash:../invalid"])
def test_invalid_profile_history_never_becomes_mlx_intent(splash: Any, target: str) -> None:
    from local_llm_admin.control import UNKNOWN, intent_after

    job = {"action": "engine", "name": "profile", "to_name": target}
    assert intent_after(job, BackendRegistry((splash.backend,))) == UNKNOWN
