"""Shared backend contracts, independent of coordinator engine flags."""

from dataclasses import replace

import pytest

from local_llm_admin.backend_types import EngineObservation, EngineTarget, resolve_observations
from local_llm_admin.backends import BackendRegistry, ComfyBackend, MlxBackend, StrataBackend
from local_llm_admin.engines import ComfyClient, StrataClient, Target
from local_llm_admin.mlx import MlxClient


@pytest.fixture
def registry(fake_strata, fake_comfy):
    s = fake_strata.settings
    return BackendRegistry(
        (
            MlxBackend(s, MlxClient(s)),
            ComfyBackend(s, ComfyClient(s, fake_comfy.transport)),
            StrataBackend(s, StrataClient(s, fake_strata.transport)),
        )
    )


def test_registry_lookup_and_target_roundtrip(registry):
    for raw in ("off", "comfyui", "strata", "strata:IQ3_XXS", "mlx:qwen"):
        assert registry.parse(raw).serialize() == raw
    assert Target is EngineTarget
    assert registry.get("mlx").spec.id == "mlx"
    with pytest.raises(ValueError, match="duplicate"):
        BackendRegistry((registry.get("mlx"), registry.get("mlx")))


@pytest.mark.parametrize(
    "raw",
    (
        "unknown",
        "none",
        "conflict",
        "mlx:",
        "strata:bad",
        "comfyui:foo",
        "off:foo",
        "mlx:qwen;stop",
    ),
)
def test_bad_target_is_explicit_error(registry, raw):
    with pytest.raises(ValueError):
        registry.parse(raw)


def test_direct_targets_and_unknown_dispatch(registry):
    for target in (
        Target("other"),
        Target("mlx"),
        Target("strata", "bad"),
        Target("comfyui", "bad"),
        Target("off", "bad"),
    ):
        with pytest.raises(ValueError):
            registry.validate(target)
    with pytest.raises(ValueError, match="unknown"):
        registry.get("other")


def test_settings_are_live(registry, fake_strata):
    backend = registry.get("strata")
    fake_strata.settings.strata_enabled = False
    assert not backend.spec.enabled
    with pytest.raises(ValueError, match="not enabled"):
        registry.validate(Target("strata"))
    fake_strata.settings.strata_enabled = True
    fake_strata.settings.strata_ready_timeout_s = 41
    fake_strata.settings.engine_stop_timeout_s = 17
    assert backend.start_steps(Target("strata"))[-1].timeout_s == 41
    assert backend.stop_steps()[-1].timeout_s == 17


def test_adapters_keep_step_contracts(registry):
    mlx = registry.get("mlx")
    assert mlx.start_steps(Target("mlx", "qwen"))[0].argv[-2:] == ["start", "qwen"]
    assert mlx.switch_steps(Target("mlx", "gemma"))[0].argv[-2:] == ["switch", "gemma"]
    assert mlx.restart_on_change is False
    assert mlx.spec.ready_timeout_s == mlx.spec.job_timeout_s
    strata = registry.get("strata")
    assert strata.restart_on_change
    assert strata.start_steps(Target("strata", "IQ3_S"))[0].argv[-2:] == ["start", "IQ3_S"]
    assert strata.stop_steps(optional=True)[0].optional
    assert not strata.stop_steps(optional=True)[1].optional
    assert registry.get("comfyui").inference() is None


def test_inference_and_policy_metadata(registry):
    mlx = registry.get("mlx").inference()
    strata = registry.get("strata").inference("served-model")
    assert mlx.target.base_url == "http://mlx.test"
    assert mlx.context({"loaded_context_size": 8192}) == 8192
    assert strata.target.base_url == "http://strata.test"
    assert strata.target.model == "served-model"
    assert strata.defaults["max_tokens"] == 8192
    assert strata.context({"max_context": 32768}) == 32768
    assert strata.context({"max_context": True}) is None
    assert strata.context({}) is None
    assert registry.get("mlx").spec.auto_recover
    assert not registry.get("strata").spec.auto_recover
    assert registry.get("comfyui").spec.exclusive


async def test_observation_does_not_probe_http(registry, fake_strata, monkeypatch):
    backend = registry.get("strata")
    monkeypatch.setattr(backend.client, "get_json", lambda path: pytest.fail("HTTP probe"))
    assert backend.observe().state == "absent"
    assert registry.observe(include_disabled=True)
    fake_strata.settings.strata_enabled = False
    monkeypatch.setattr(backend.client, "running", lambda: pytest.fail("disabled PID probe"))
    assert all(o.backend != "strata" for o in registry.observe())


def test_resolve_observations():
    absent = EngineObservation("mlx", state="absent")
    owned = EngineObservation("mlx", state="starting", ownership="verified")
    other = replace(owned, backend="strata")
    unknown = EngineObservation("mlx", state="unknown", ownership="unknown")
    assert resolve_observations([absent]) == "none"
    assert resolve_observations([owned]) == "mlx"
    assert resolve_observations([owned, other]) == "conflict"
    assert resolve_observations([unknown]) == "unknown"
    assert resolve_observations([owned, unknown]) == "unknown"


class FourthBackend(MlxBackend):
    """Test-only adapter: registration is the only production extension point."""

    @property
    def spec(self):
        return replace(
            super().spec,
            id="fourth",
            label="Fourth",
            exclusive=True,
            model_sidecars=False,
            auto_recover=False,
        )

    def validate(self, target):
        if target.kind != "fourth" or target.name not in ("alpha", "beta"):
            raise ValueError("invalid fourth profile")

    def start_steps(self, target):
        from local_llm_admin.jobs import Step

        return [Step("fourth start " + target.name, argv=["fourth", "start", target.name])]

    def stop_steps(self, optional=False):
        from local_llm_admin.jobs import Step

        return [
            Step("fourth stop", argv=["fourth", "stop"], optional=optional),
            Step("wait for Fourth to exit", wait=lambda: True),
        ]

    def switch_steps(self, target):
        return self.start_steps(target)


def test_fourth_backend_parsing_and_planning(registry):
    from local_llm_admin.engines import plan_transition

    registry = BackendRegistry(
        (*registry, FourthBackend(registry.get("mlx").settings, registry.get("mlx").client))
    )
    target = registry.parse("fourth:alpha")
    result = plan_transition(
        target, observations=[], registry=registry, force=False, ops=None, queues={}
    )
    assert result.steps[0].argv == ["fourth", "start", "alpha"]
    result = plan_transition(
        Target("mlx", "qwen"),
        observations=[EngineObservation("fourth", "alpha", state="ready", ownership="verified")],
        registry=registry,
        force=False,
        ops=None,
        queues={},
    )
    assert [s.label for s in result.steps][:2] == ["fourth stop", "wait for Fourth to exit"]
    assert result.rollback[-1].argv == ["fourth", "start", "alpha"]


@pytest.mark.parametrize(
    "observations",
    [
        [EngineObservation("mlx", state="unknown")],
        [EngineObservation("other", state="ready", ownership="verified")],
        [
            EngineObservation("mlx", state="ready", ownership="verified"),
            EngineObservation("mlx", state="ready", ownership="verified"),
        ],
    ],
)
def test_ambiguous_or_unknown_observations_never_start(registry, observations):
    from local_llm_admin.engines import TransitionRefused, plan_transition

    with pytest.raises((TransitionRefused, ValueError)):
        plan_transition(
            Target("comfyui"),
            observations=observations,
            registry=registry,
            force=True,
            ops=None,
            queues={},
        )


async def test_chat_resolver_requires_unambiguous_owned_backend(registry, monkeypatch):
    from local_llm_admin.backends import BackendUnavailable

    monkeypatch.setattr(
        registry, "observe", lambda **kw: [EngineObservation("mlx", state="unknown")]
    )
    with pytest.raises(BackendUnavailable, match="unknown"):
        await registry.resolve_inference()
    monkeypatch.setattr(
        registry,
        "observe",
        lambda **kw: [
            EngineObservation("mlx", state="ready", ownership="verified"),
            EngineObservation("strata", state="ready", ownership="verified"),
        ],
    )
    with pytest.raises(BackendUnavailable, match="conflict"):
        await registry.resolve_inference()
    monkeypatch.setattr(
        registry,
        "observe",
        lambda **kw: [EngineObservation("comfyui", state="ready", ownership="verified")],
    )
    with pytest.raises(BackendUnavailable, match="ComfyUI"):
        await registry.resolve_inference()


async def test_chat_metadata_is_resolved_once_with_live_profiles(registry, monkeypatch):
    from local_llm_admin.mlx import is_serving

    calls = []

    async def health():
        calls.append("/health")
        return {"status": "healthy", "loaded_model": "qwen"}

    backend = registry.get("mlx")
    monkeypatch.setattr(backend.client, "health", health)
    monkeypatch.setattr(
        registry,
        "observe",
        lambda **kw: [EngineObservation("mlx", "qwen", state="starting", ownership="verified")],
    )
    result = await registry.resolve_inference(lambda name: {"top_k": 7})
    assert result.defaults == {"top_k": 7}
    assert result.identity["engine"] == "mlx"
    assert result.identity["reference"] == "qwen"
    assert not any(key in result.identity for key in ("base_url", "api_key"))
    assert result.target.model == "/Volumes/Models/models/active"
    assert calls == ["/health"]
    assert is_serving(await health())


def test_bad_builder_dispatch_cannot_emit_mlx_command(registry):
    with pytest.raises(ValueError):
        registry.get("mlx").start_steps(Target("unknown", "qwen"))


def test_chat_ui_ownership_without_api_pid(registry, monkeypatch):
    backend = registry.get("mlx")
    monkeypatch.setattr(backend.client, "engine_running", lambda: True)
    monkeypatch.setattr(backend.client, "pid_from_file", lambda: None)
    monkeypatch.setattr(backend.client, "owned_listener_pid", lambda port: 42)
    monkeypatch.setattr(
        "local_llm_admin.backends.process_generation",
        lambda pid: {"pid": pid, "started": 100.0} if pid else None,
    )
    obs = backend.observe()
    assert obs.running and obs.ownership == "verified"
    assert obs.generation == {"pid": 42, "started": 100.0}


def test_unknown_port_is_never_attributed_to_mlx(registry, monkeypatch):
    backend = registry.get("mlx")
    monkeypatch.setattr(backend.client, "engine_running", lambda: True)
    monkeypatch.setattr(backend.client, "pid_from_file", lambda: None)
    monkeypatch.setattr(backend.client, "owned_listener_pid", lambda port: None)
    obs = backend.observe()
    assert obs.state == "unknown" and obs.ownership == "unknown"


def test_reused_pidfile_is_unknown(registry, fake_strata, monkeypatch):
    backend = registry.get("strata")
    from local_llm_admin.mlx import ProcessStatus

    (fake_strata.root / "run/strata.pid").write_text("42")
    monkeypatch.setattr(backend.client, "running", lambda: ProcessStatus(True, 42))
    monkeypatch.setattr(
        "local_llm_admin.backends.process_generation",
        lambda pid: {"pid": pid, "started": 9999999999.0},
    )
    assert backend.observe().ownership == "unknown"


async def test_fourth_status_is_collected_by_registry(registry, monkeypatch):
    fourth = FourthBackend(registry.get("mlx").settings, registry.get("mlx").client)

    async def details(*, job_running=False):
        return {"running": True, "ready": True, "profile": "alpha"}

    monkeypatch.setattr(fourth, "details", details)
    monkeypatch.setattr(
        fourth,
        "status_observation",
        lambda status: EngineObservation("fourth", "alpha", state="starting", ownership="verified"),
    )
    registry = BackendRegistry((fourth,))
    details = await registry.status_details(job_running=False)
    observations = registry.from_status(details)
    assert resolve_observations(observations) == "fourth"
    assert observations[0].ready is True


def test_fourth_intent_uses_adapter_serialization(registry):
    from local_llm_admin.control import intent_after

    registry = BackendRegistry(
        (*registry, FourthBackend(registry.get("mlx").settings, registry.get("mlx").client))
    )
    assert (
        intent_after({"action": "engine", "name": "alpha", "to_name": "fourth"}, registry)
        == "fourth:alpha"
    )
    assert (
        intent_after({"action": "engine", "name": "IQ3_S", "to_name": "strata"}, registry)
        == "strata"
    )
    assert (
        intent_after({"action": "engine", "name": "strata", "to_name": "strata"}, registry)
        == "mlx:strata"
    )


@pytest.mark.parametrize("backend_id", ("", "off", "none", "unknown", "conflict", "bad:id"))
def test_reserved_registry_ids(registry, backend_id):
    class InvalidBackend(MlxBackend):
        @property
        def spec(self):
            return replace(super().spec, id=backend_id)

    with pytest.raises(ValueError, match="invalid backend ID"):
        BackendRegistry((InvalidBackend(registry.get("mlx").settings, registry.get("mlx").client),))


def test_inventory_and_recovery_policy(registry, fake_strata):
    assert Target("mlx", "qwen") in registry.get("mlx").targets()
    assert registry.get("comfyui").targets() == [Target("comfyui")]
    assert Target("strata", "IQ3_S") in registry.get("strata").targets()
    assert registry.recovery_eligible("mlx:qwen")
    for target in ("off", "none", "strata", "comfyui", "unknown:profile"):
        assert not registry.recovery_eligible(target)
    fake_strata.settings.strata_enabled = False
    assert not registry.recovery_eligible("strata")


async def test_disabled_status_has_no_process_or_http_probes(registry, fake_strata, monkeypatch):
    fake_strata.settings.strata_enabled = False
    backend = registry.get("strata")
    monkeypatch.setattr(backend, "status_observation", lambda status: pytest.fail("PID probe"))
    monkeypatch.setattr(backend, "details", lambda **kw: pytest.fail("HTTP probe"))
    assert registry.from_status({"strata": {}}) == []
    assert "strata" not in await registry.status_details(job_running=False)


async def test_unknown_ownership_cannot_become_ready_from_http(registry, monkeypatch):
    backend = registry.get("mlx")
    monkeypatch.setattr(backend, "details", lambda: pytest.fail("unowned HTTP probe"))
    observation = EngineObservation("mlx", state="unknown")
    assert await registry.observed_details([observation]) == [observation]


async def test_inference_rejects_disabled_and_missing_capability(
    registry, fake_strata, monkeypatch
):
    from local_llm_admin.backends import BackendUnavailable

    monkeypatch.setattr(
        registry,
        "observe",
        lambda **kw: [EngineObservation("strata", state="starting", ownership="verified")],
    )
    fake_strata.settings.strata_enabled = False
    with pytest.raises(BackendUnavailable, match="not enabled"):
        await registry.resolve_inference()
    monkeypatch.setattr(
        registry,
        "observe",
        lambda **kw: [EngineObservation("mlx", state="starting", ownership="verified")],
    )

    async def unsupported():
        return None

    monkeypatch.setattr(registry.get("mlx"), "serving_inference", unsupported)
    with pytest.raises(BackendUnavailable, match="does not support"):
        await registry.resolve_inference()


def test_missing_pidfile_does_not_verify_owner(registry, monkeypatch):
    from local_llm_admin.mlx import ProcessStatus

    backend = registry.get("strata")
    monkeypatch.setattr(backend.client, "running", lambda: ProcessStatus(True, 42))
    monkeypatch.setattr(
        "local_llm_admin.backends.process_generation", lambda pid: {"pid": pid, "started": 1.0}
    )
    assert backend.observe().ownership == "unknown"


async def test_adapters_refuse_unsupported_operations_and_unready_inference(registry, monkeypatch):
    from local_llm_admin.backends import BackendUnavailable

    for kind in ("comfyui", "strata"):
        with pytest.raises(ValueError, match="in-engine switch"):
            registry.get(kind).switch_steps(Target(kind))
    assert await registry.get("comfyui").serving_inference() is None
    assert await registry.get("mlx").queue_count() is None
    assert await registry.get("strata").queue_count() is None

    async def not_loaded():
        return {"status": "healthy", "loaded_model": None}

    monkeypatch.setattr(registry.get("mlx").client, "health", not_loaded)
    with pytest.raises(BackendUnavailable, match="not serving"):
        await registry.get("mlx").serving_inference()


def test_old_engine_job_labels_roundtrip_without_new_storage(registry):
    from local_llm_admin.control import intent_after

    assert (
        intent_after({"action": "engine", "name": None, "to_name": "qwen"}, registry) == "mlx:qwen"
    )


def test_live_pid_does_not_claim_unowned_shared_listener(registry, monkeypatch):
    from types import SimpleNamespace

    import psutil

    backend = registry.get("mlx")
    owned = EngineObservation(
        "mlx",
        "qwen",
        generation={"pid": 42, "started": 1.0},
        state="starting",
        ownership="verified",
    )
    monkeypatch.setattr(backend, "observe", lambda: owned)
    monkeypatch.setattr(
        psutil,
        "net_connections",
        lambda kind: [
            SimpleNamespace(
                pid=99,
                laddr=SimpleNamespace(port=backend.settings.mlx_port),
                status=psutil.CONN_LISTEN,
            )
        ],
    )
    observations = registry.observe(include_disabled=True)
    assert next(o for o in observations if o.backend == "mlx").ownership == "unknown"
    assert resolve_observations(observations) == "unknown"


@pytest.mark.parametrize(("socket_owner", "expected"), [(42, "verified"), (None, "unknown")])
def test_listener_resource_owner_must_be_verified(registry, monkeypatch, socket_owner, expected):
    from types import SimpleNamespace

    import psutil

    backend = registry.get("mlx")
    observation = EngineObservation(
        "mlx",
        "qwen",
        generation={"pid": 42, "started": 1.0},
        state="starting",
        ownership="verified",
    )
    monkeypatch.setattr(backend, "observe", lambda: observation)
    monkeypatch.setattr(
        psutil,
        "net_connections",
        lambda kind: [
            SimpleNamespace(
                pid=socket_owner,
                laddr=SimpleNamespace(port=backend.settings.mlx_port),
                status=psutil.CONN_LISTEN,
            )
        ],
    )
    observed = registry.observe(include_disabled=True)
    assert next(o for o in observed if o.backend == "mlx").ownership == expected


@pytest.mark.parametrize(
    ("owned_socket", "process_denied", "listening", "expected"),
    [
        (True, False, True, "verified"),
        (False, False, True, "unknown"),
        (False, True, True, "unknown"),
        (False, True, False, "verified"),
    ],
)
def test_restricted_socket_visibility_is_conservative(
    registry, monkeypatch, owned_socket, process_denied, listening, expected
):
    from types import SimpleNamespace

    import psutil

    backend = registry.get("mlx")
    observation = EngineObservation(
        "mlx",
        "qwen",
        generation={"pid": 42, "started": 1.0},
        state="starting",
        ownership="verified",
    )
    monkeypatch.setattr(backend, "observe", lambda: observation)

    def denied(kind):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "net_connections", denied)
    connection = SimpleNamespace(
        laddr=SimpleNamespace(port=backend.settings.mlx_port), status=psutil.CONN_LISTEN
    )

    def connections(kind):
        if process_denied:
            raise psutil.AccessDenied()
        return [connection] if owned_socket else []

    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(net_connections=connections))
    monkeypatch.setattr(MlxClient, "port_listening", lambda port: listening)
    # Keep the other backend observations fixed; only listener attribution is under test.
    for kind in ("comfyui", "strata"):
        monkeypatch.setattr(
            registry.get(kind), "observe", lambda k=kind: EngineObservation(k, state="absent")
        )
    observed = registry.observe(include_disabled=True)
    assert next(o for o in observed if o.backend == "mlx").ownership == expected


def test_inference_readiness_and_model_metadata_are_bounded_by_shape(registry):
    for health in (None, [1], {}, {"status": "healthy"}):
        assert registry.get("mlx").inference_ready(health) is False
        assert registry.get("strata").inference_ready(health) is False
    assert registry.get("strata").inference_ready({"service": "strata", "loaded": True})
    assert not registry.get("strata").inference_ready({"service": "other", "loaded": True})
    assert registry.get("comfyui").inference_ready({}) is False
    with pytest.raises(ValueError, match="quality"):
        registry.get("comfyui").compatibility_info({}, {})
    assert registry.get("strata").inference().served_model({"model": "served"}) == "served"
    assert registry.get("strata").inference().served_model({"model": 42}) is None
    assert registry.get("strata").inference().served_model(None) is None


async def test_strata_status_and_chat_keep_existing_probe_budget(
    registry, fake_strata, monkeypatch
):
    import httpx

    calls = []
    backend = registry.get("strata")
    transport = backend.client._transport

    def counted(request):
        calls.append(request.url.path)
        return transport.handle_request(request)

    monkeypatch.setattr(backend.client, "_transport", httpx.MockTransport(counted))
    fake_strata.start()

    async def no_mlx_health():
        return None

    monkeypatch.setattr(registry.get("mlx").client, "health", no_mlx_health)
    details = await registry.status_details(job_running=False)
    registry.from_status(details)
    assert calls == ["/health", "/metrics"]
    calls.clear()
    descriptor = await registry.resolve_inference()
    assert descriptor.target.model == "qwen3.8-flash-next-q2_0"
    assert calls == ["/health", "/metrics"]


@pytest.mark.parametrize(("state", "observations", "badge"), [
    ("switching", [EngineObservation("mlx", state="unknown")], "switching"),
    ("drive-missing", [], "drive-missing"),
    ("off", [], "off"),
    ("serving", [EngineObservation("mlx", state="ready", ready=True,
                                  ownership="verified")], "serving"),
    ("off", [EngineObservation("fourth", state="starting", ready=False,
                              ownership="verified")], "fourth-starting"),
    ("off", [EngineObservation("fourth", state="ready", ready=True,
                              ownership="verified")], "fourth"),
])
def test_badges_use_normalized_observations(state, observations, badge):
    from local_llm_admin.engines import observation_badge
    assert observation_badge(state, observations) == badge
