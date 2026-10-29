from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_llm_admin.backend_types import EngineObservation
from local_llm_admin.backends import BackendRegistry, ComfyBackend, MlxBackend, StrataBackend
from local_llm_admin.config import Settings
from local_llm_admin.engines import Target, TransitionRefused, plan_transition
from local_llm_admin.sidecars import SidecarSpec

SPEC = SidecarSpec(name="openjev", label="decision", title="Decision sidecar", port=8081)
TTS = SidecarSpec(name="qwen-tts", label="TTS", title="TTS sidecar", port=8083)


class Ops:
    stop_timeout_s, ready_timeout_s, job_timeout_s = 30.0, 120.0, 300.0
    strata_ready_timeout_s = 600.0

    def mlx_argv(self, action, name):
        return ["mlx-model", action] + ([name] if name else [])

    def comfy_argv(self, action):
        return ["comfyui", action]

    def mlx_down(self):
        return True

    def comfy_down(self):
        return True

    def comfy_ready(self):
        return True

    def comfy_cancel_all(self):
        return None

    def strata_argv(self, action, pack=None):
        return ["strata", action] + ([pack] if pack else [])

    def strata_down(self):
        return True

    def strata_ready(self):
        return True

    def sidecar_argv(self, name, action):
        return [name, action]

    def sidecar_down(self, name):
        return True

    def sidecar_ready(self, name):
        return True

    def sidecar_warmup(self, name):
        return (lambda: True) if name == "qwen-embed" else None


def registry(ops):
    settings = Settings(mlx_model_bin=Path("mlx-model"), strata_enabled=True,
                        admin_job_timeout_s=ops.job_timeout_s,
                        engine_stop_timeout_s=ops.stop_timeout_s,
                        comfyui_ready_timeout_s=ops.ready_timeout_s,
                        strata_ready_timeout_s=ops.strata_ready_timeout_s)
    return BackendRegistry((
        ComfyBackend(settings, SimpleNamespace(argv=ops.comfy_argv, down_sync=ops.comfy_down,
                     ready_sync=ops.comfy_ready, cancel_all_sync=ops.comfy_cancel_all)),
        StrataBackend(settings, SimpleNamespace(argv=ops.strata_argv, down_sync=ops.strata_down,
                      ready_sync=ops.strata_ready)),
        MlxBackend(settings, SimpleNamespace(engine_running=lambda: not ops.mlx_down())),
    ))


def observations(mlx=False, name=None, comfy=False, strata=False, pack=None):
    return [EngineObservation(k, ref, state="ready" if up else "absent",
                              ownership="verified")
            for k, up, ref in (("comfyui", comfy, None), ("strata", strata, pack),
                               ("mlx", mlx, name))]


def plan(
    target,
    mlx=False,
    name="qwen",
    comfy=False,
    queue=0,
    force=False,
    dj=False,
    ok=True,
    spec=SPEC,
    strata=False,
    services=(),
    restore=(),
    pack=None,
):
    allowed = frozenset({"qwen", "qwen2", "big"}) if ok else frozenset()
    return plan_transition(
        target,
        observations=observations(mlx, name, comfy, strata, pack),
        registry=registry(Ops()),
        queues={"comfyui": queue},
        force=force,
        ops=Ops(),
        sidecar=replace(spec, allowed_mlx=allowed) if dj else None,
        services=services,
        restore=restore,
    )


def labels(steps):
    return [s.label for s in steps]


def test_mlx_to_comfyui():
    t = plan(Target("comfyui"), mlx=True)
    assert labels(t.steps) == [
        "mlx-model stop",
        "wait for MLX to exit",
        "comfyui start",
        "wait for ComfyUI to be ready",
    ]
    assert t.steps[0].argv == ["mlx-model", "stop"] and t.steps[2].argv == ["comfyui", "start"]
    assert t.steps[3].timeout_s == 120.0 and t.steps[1].timeout_s == 30.0
    assert labels(t.rollback) == ["comfyui stop", "wait for :8188 to close", "mlx-model start qwen"]
    # the stop command may fail harmlessly, but the old engine must not start
    # until the target is confirmed down
    assert [s.optional for s in t.rollback] == [True, False, False]
    assert t.rollback_after == 2 and (t.from_label, t.to_label) == ("qwen", "comfyui")


def test_comfyui_to_mlx():
    t = plan(Target("mlx", "gemma"), comfy=True)
    assert labels(t.steps) == ["comfyui stop", "wait for :8188 to close", "mlx-model start gemma"]
    assert labels(t.rollback) == [
        "mlx-model stop",
        "wait for MLX to exit",
        "comfyui start",
        "wait for ComfyUI to be ready",
    ]
    assert t.rollback_after == 2 and (t.from_label, t.to_label) == ("comfyui", "gemma")


def test_comfyui_with_queue_refused_without_force():
    with pytest.raises(TransitionRefused) as ei:
        plan(Target("off"), comfy=True, queue=3)
    assert ei.value.code == 409 and "3 queued or running" in ei.value.message


def test_comfyui_with_queue_forced_cancels_first():
    t = plan(Target("mlx", "qwen"), comfy=True, queue=2, force=True)
    assert labels(t.steps)[0] == "cancel ComfyUI queue" and t.steps[0].call is not None
    assert t.rollback_after == 3


def test_none_to_either_has_no_rollback():
    t = plan(Target("comfyui"))
    assert labels(t.steps) == ["comfyui start", "wait for ComfyUI to be ready"] and t.rollback == []
    t = plan(Target("mlx", "qwen"))
    assert labels(t.steps) == ["mlx-model start qwen"] and t.from_label is None


def test_mlx_to_other_mlx_model_is_a_switch():
    t = plan(Target("mlx", "gemma"), mlx=True, name="qwen")
    assert labels(t.steps) == ["mlx-model switch gemma"] and t.rollback == []


def test_off_stops_whatever_runs():
    assert labels(plan(Target("off"), mlx=True).steps) == ["mlx-model stop", "wait for MLX to exit"]
    assert labels(plan(Target("off"), comfy=True).steps) == [
        "comfyui stop",
        "wait for :8188 to close",
    ]
    assert plan(Target("off"), mlx=True).to_label is None


def test_conflict_keep_mlx_only_stops_comfyui():
    t = plan(Target("mlx", "qwen"), mlx=True, comfy=True)
    assert labels(t.steps) == ["comfyui stop", "wait for :8188 to close"]
    assert t.rollback == [] and t.from_label == "conflict"


def test_conflict_keep_comfyui_only_stops_mlx():
    assert labels(plan(Target("comfyui"), mlx=True, comfy=True).steps) == [
        "mlx-model stop",
        "wait for MLX to exit",
    ]


def test_conflict_off_stops_both():
    assert labels(plan(Target("off"), mlx=True, comfy=True).steps) == [
        "comfyui stop",
        "wait for :8188 to close",
        "mlx-model stop",
        "wait for MLX to exit",
    ]


@pytest.mark.parametrize(
    ("target", "kw"),
    [
        (Target("comfyui"), {"comfy": True}),
        (Target("mlx", "qwen"), {"mlx": True}),
        (Target("off"), {}),
    ],
)
def test_already_active_is_refused(target, kw):
    with pytest.raises(TransitionRefused) as ei:
        plan(target, **kw)
    assert ei.value.code == 409 and "already" in ei.value.message


def test_rollback_to_mlx_without_known_name_starts_selected():
    t = plan(Target("comfyui"), mlx=True, name=None)
    assert t.rollback[-1].argv == ["mlx-model", "start"]


def test_unknown_queue_refused_without_force():
    with pytest.raises(TransitionRefused) as ei:
        plan(Target("off"), comfy=True, queue=None)
    assert ei.value.code == 409 and "unknown" in ei.value.message


def test_unknown_queue_forced_rechecks_optionally():
    t = plan(Target("mlx", "qwen"), comfy=True, queue=None, force=True)
    first = t.steps[0]
    assert first.label == "cancel ComfyUI queue (queue state was unknown)"
    assert first.optional and first.call is not None
    assert labels(t.steps)[1] == "comfyui stop"


def test_unknown_queue_irrelevant_when_comfy_kept():
    t = plan(Target("comfyui"), mlx=True, comfy=False, queue=None)
    assert "cancel" not in " ".join(labels(t.steps))


# --- M6: the decision sidecar --------------------------------------------------------

DJ_STOP = ["openjev stop", "wait for :8081 to close"]


def test_to_comfyui_stops_the_sidecar_first():
    t = plan(Target("comfyui"), mlx=True, dj=True)
    assert labels(t.steps)[:2] == DJ_STOP
    assert t.steps[0].argv == ["openjev", "stop"] and not t.steps[1].optional
    assert labels(t.steps)[2:] == [
        "mlx-model stop",
        "wait for MLX to exit",
        "comfyui start",
        "wait for ComfyUI to be ready",
    ]
    # rollback brings MLX back but leaves the sidecar stopped
    assert "openjev start" not in labels(t.rollback)
    assert t.rollback_after == 4


def test_off_to_comfyui_with_only_the_sidecar():
    t = plan(Target("comfyui"), dj=True)
    assert labels(t.steps) == [*DJ_STOP, "comfyui start", "wait for ComfyUI to be ready"]


@pytest.mark.parametrize("mlx", [True, False])
def test_mlx_switch_to_disallowed_model_is_refused(mlx):
    with pytest.raises(TransitionRefused) as e:
        plan(Target("mlx", "big"), mlx=mlx, dj=True, ok=False)
    assert e.value.code == 409 and "decision sidecar" in e.value.message


def test_forced_switch_to_disallowed_model_stops_the_sidecar():
    t = plan(Target("mlx", "big"), mlx=True, dj=True, ok=False, force=True)
    assert labels(t.steps) == [*DJ_STOP, "mlx-model switch big"]


def test_switch_to_allowed_model_keeps_the_sidecar():
    t = plan(Target("mlx", "qwen2"), mlx=True, dj=True, ok=True)
    assert labels(t.steps) == ["mlx-model switch qwen2"]


def test_off_leaves_the_sidecar_running():
    t = plan(Target("off"), mlx=True, dj=True)
    assert labels(t.steps) == ["mlx-model stop", "wait for MLX to exit"]


def test_sidecar_alone_is_not_an_active_engine():
    with pytest.raises(TransitionRefused, match="already active"):
        plan(Target("off"), dj=True)


def test_tts_sidecar_is_stopped_the_same_way():
    t = plan(Target("comfyui"), dj=True, spec=TTS)
    assert labels(t.steps)[:2] == ["qwen-tts stop", "wait for :8083 to close"]
    with pytest.raises(TransitionRefused, match="the TTS sidecar is running"):
        plan(Target("mlx", "big"), mlx=True, dj=True, ok=False, spec=TTS)


def test_defaults_keep_old_behaviour():
    t = plan_transition(
        Target("comfyui"),
        observations=observations(mlx=True, name="qwen"),
        registry=registry(Ops()),
        queues={"comfyui": 0},
        force=False,
        ops=Ops(),
    )
    assert labels(t.steps)[0] == "mlx-model stop"


# --- M8: the always-on embedding service ---------------------------------------------

EMBED = SidecarSpec(
    name="qwen-embed",
    label="embeddings",
    title="Embeddings service",
    port=8084,
    allowed_mlx=frozenset({"qwen", "qwen2"}),
    kind="service",
    intent_key="embed_intent",
)


def plan_svc(target, *, mlx=False, comfy=False, force=False, running=(EMBED,), restore=()):
    return plan_transition(
        target,
        observations=observations(mlx, "qwen" if mlx else None, comfy),
        registry=registry(Ops()),
        queues={"comfyui": 0},
        force=force,
        ops=Ops(),
        services=list(running),
        restore=list(restore),
    )


def test_to_comfyui_stops_service_first():
    t = plan_svc(Target("comfyui"), mlx=True)
    assert labels(t.steps)[:2] == ["qwen-embed stop", "wait for :8084 to close"]
    assert labels(t.steps).index("wait for :8084 to close") < labels(t.steps).index("comfyui start")
    assert not t.steps[0].optional and not t.steps[1].optional
    assert t.forced_stops == ()  # intent stays running: it comes back after ComfyUI


def test_comfy_round_trip_steps():
    """Leaving ComfyUI restores a service whose intent is running, after the engine starts."""
    t = plan_svc(Target("mlx", "qwen"), comfy=True, running=(), restore=(EMBED,))
    names = labels(t.steps)
    assert names[-3:] == ["qwen-embed start", "wait for :8084 to be ready", "warm up qwen-embed"]
    assert names.index("qwen-embed start") > names.index("mlx-model start qwen")
    t_off = plan_svc(Target("off"), comfy=True, running=(), restore=(EMBED,))
    assert "qwen-embed start" in labels(t_off.steps)  # the service may run with the engine off


def test_restore_failure_is_optional():
    t = plan_svc(Target("mlx", "qwen"), comfy=True, running=(), restore=(EMBED,))
    assert all(s.optional for s in t.steps if "qwen-embed" in s.label or ":8084" in s.label)


def test_no_restore_without_leaving_comfyui():
    t = plan_svc(Target("mlx", "qwen2"), mlx=True, running=(), restore=(EMBED,))
    assert "qwen-embed start" not in labels(t.steps)  # a crash is never restarted (D-E5)


def test_restore_skipped_for_disallowed_model():
    t = plan_svc(Target("mlx", "big"), comfy=True, running=(), restore=(EMBED,))
    assert "qwen-embed start" not in labels(t.steps) and t.unrestored == ("qwen-embed",)


def test_disallowed_mlx_refused_without_force():
    with pytest.raises(TransitionRefused, match="the embeddings service is running and big"):
        plan_svc(Target("mlx", "big"), mlx=True)
    t = plan_svc(Target("mlx", "big"), mlx=True, force=True)
    assert labels(t.steps)[:2] == ["qwen-embed stop", "wait for :8084 to close"]
    assert t.forced_stops == ("qwen-embed",)


def test_allowed_switch_and_off_leave_service_running():
    assert "qwen-embed stop" not in labels(plan_svc(Target("mlx", "qwen2"), mlx=True).steps)
    assert "qwen-embed stop" not in labels(plan_svc(Target("off"), mlx=True).steps)


def test_failed_comfy_switch_rollback_restores_service():
    t = plan_svc(Target("comfyui"), mlx=True)
    rb = labels(t.rollback)
    assert rb[-3:] == ["qwen-embed start", "wait for :8084 to be ready", "warm up qwen-embed"]
    assert all(s.optional for s in t.rollback if "qwen-embed" in s.label or ":8084" in s.label)


def test_restore_without_warmup_has_two_steps():
    spec = replace(EMBED, name="other-svc", port=8085)
    t = plan_svc(Target("off"), comfy=True, running=(), restore=(spec,))
    assert labels(t.steps)[-2:] == ["other-svc start", "wait for :8085 to be ready"]
    assert "warm up other-svc" not in labels(t.steps)


# --- Strata (third exclusive engine) -------------------------------------------------
EMB = SidecarSpec(
    name="qwen-embed",
    label="embeddings",
    title="Embeddings",
    port=8084,
    kind="service",
    allowed_mlx=frozenset({"qwen", "gemma"}),
)
STRATA_UP = ["strata start", "wait for Strata to be ready"]
STRATA_DOWN = ["strata stop", "wait for Strata to exit"]
MLX_DOWN = ["mlx-model stop", "wait for MLX to exit"]
COMFY_DOWN = ["comfyui stop", "wait for :8188 to close"]
COMFY_UP = ["comfyui start", "wait for ComfyUI to be ready"]


def test_parse_target_strata():
    from local_llm_admin.engines import parse_target

    assert parse_target("strata") == Target("strata")
    assert parse_target("strata:IQ3_XXS") == Target("strata", "IQ3_XXS")
    for bad in ("strata:", "strata:iq3_xxs", "strata:Q9_9", "strata:Q2_0;rm"):
        with pytest.raises(ValueError):
            parse_target(bad)


def test_off_to_a_strata_pack():
    t = plan(Target("strata", "IQ3_XXS"))
    assert labels(t.steps) == ["strata start IQ3_XXS", "wait for Strata to be ready"]
    assert t.steps[0].argv == ["strata", "start", "IQ3_XXS"]
    assert (t.from_label, t.to_label) == (None, "strata")


def test_switching_strata_packs_restarts_it_and_rollback_brings_the_old_pack_back():
    t = plan(Target("strata", "IQ3_XXS"), strata=True, pack="Q2_0")
    assert labels(t.steps) == [*STRATA_DOWN, "strata start IQ3_XXS", "wait for Strata to be ready"]
    assert labels(t.rollback) == [*STRATA_DOWN, "strata start Q2_0", "wait for Strata to be ready"]
    assert t.rollback[0].optional and t.rollback_after == 2
    assert (t.from_label, t.to_label) == ("strata", "strata")


def test_the_running_strata_pack_is_already_active():
    with pytest.raises(TransitionRefused) as ei:
        plan(Target("strata", "Q2_0"), strata=True, pack="Q2_0")
    assert ei.value.code == 409 and "already active" in ei.value.message
    with pytest.raises(TransitionRefused):  # no pack named: whichever runs is fine
        plan(Target("strata"), strata=True, pack="IQ3_XXS")


def test_strata_pack_of_unknown_state_is_restarted():
    t = plan(Target("strata", "IQ3_XXS"), strata=True, pack=None)
    assert labels(t.steps)[:2] == STRATA_DOWN


def test_leaving_strata_rolls_back_to_the_pack_that_ran():
    t = plan(Target("mlx", "gemma"), strata=True, pack="IQ3_XXS")
    assert labels(t.rollback) == [*MLX_DOWN, "strata start IQ3_XXS", "wait for Strata to be ready"]


def test_off_to_strata():
    t = plan(Target("strata"))
    assert labels(t.steps) == STRATA_UP and t.rollback == []
    assert t.steps[0].argv == ["strata", "start"] and t.steps[1].timeout_s == 600.0
    assert (t.from_label, t.to_label) == (None, "strata")


def test_mlx_to_strata_stops_services_and_rollback_restores_them():
    t = plan(Target("strata"), mlx=True, services=[EMB])
    assert labels(t.steps) == ["qwen-embed stop", "wait for :8084 to close", *MLX_DOWN, *STRATA_UP]
    assert labels(t.rollback) == [
        *STRATA_DOWN,
        "mlx-model start qwen",
        "qwen-embed start",
        "wait for :8084 to be ready",
        "warm up qwen-embed",
    ]
    assert [s.optional for s in t.rollback[:2]] == [True, False]
    assert t.rollback_after == 4 and (t.from_label, t.to_label) == ("qwen", "strata")


def test_strata_stops_running_sidecar_even_if_allowed():
    t = plan(Target("strata"), mlx=True, dj=True)
    assert labels(t.steps)[:2] == ["openjev stop", "wait for :8081 to close"]


def test_strata_to_mlx_restores_services():
    t = plan(Target("mlx", "gemma"), strata=True, restore=[EMB])
    assert labels(t.steps) == [
        *STRATA_DOWN,
        "mlx-model start gemma",
        "qwen-embed start",
        "wait for :8084 to be ready",
        "warm up qwen-embed",
    ]
    assert labels(t.rollback) == [*MLX_DOWN, *STRATA_UP]
    assert t.rollback_after == 2 and (t.from_label, t.to_label) == ("strata", "gemma")


def test_strata_to_mlx_leaves_excluded_service_off():
    excl = replace(EMB, allowed_mlx=frozenset({"qwen"}))
    t = plan(Target("mlx", "gemma"), strata=True, restore=[excl])
    assert "qwen-embed start" not in labels(t.steps) and t.unrestored == ("qwen-embed",)


def test_strata_to_off_restores_services():
    t = plan(Target("off"), strata=True, restore=[EMB])
    assert labels(t.steps)[:2] == STRATA_DOWN and "qwen-embed start" in labels(t.steps)
    assert t.rollback == [] and (t.from_label, t.to_label) == ("strata", None)


def test_comfyui_to_strata_does_not_restore_services():
    t = plan(Target("strata"), comfy=True, restore=[EMB])
    assert labels(t.steps) == [*COMFY_DOWN, *STRATA_UP]
    assert labels(t.rollback) == [*STRATA_DOWN, *COMFY_UP]


def test_strata_to_comfyui():
    t = plan(Target("comfyui"), strata=True, restore=[EMB])
    assert labels(t.steps) == [*STRATA_DOWN, *COMFY_UP]
    assert labels(t.rollback) == [*COMFY_DOWN, *STRATA_UP]
    assert (t.from_label, t.to_label) == ("strata", "comfyui")


def test_strata_already_active_refused():
    with pytest.raises(TransitionRefused) as ei:
        plan(Target("strata"), strata=True)
    assert ei.value.code == 409 and "strata is already active" in ei.value.message


def test_comfyui_queue_guard_applies_on_the_way_to_strata():
    with pytest.raises(TransitionRefused):
        plan(Target("strata"), comfy=True, queue=1)


def test_conflict_with_strata_stops_every_other_engine():
    t = plan(Target("strata"), mlx=True, comfy=True, strata=True)
    assert labels(t.steps) == [*COMFY_DOWN, *MLX_DOWN]
    assert t.from_label == "conflict"


@pytest.mark.parametrize(("source", "target"), [
    ("mlx", "comfyui"), ("mlx", "strata"),
    ("comfyui", "mlx"), ("comfyui", "strata"),
    ("strata", "mlx"), ("strata", "comfyui"),
])
def test_pair_rollback_stops_target_before_restoring_source(source, target):
    t = plan(Target(target, "gemma" if target == "mlx" else None),
             **{source if source != "comfyui" else "comfy": True})
    assert t.rollback_after == 2
    assert t.rollback[0].optional
    assert t.rollback[1].wait is not None and not t.rollback[1].optional
    assert t.rollback[1].timeout_s == 30
    assert t.rollback[2].argv is not None
    assert all(s.timeout_s == 300 for s in t.steps if s.argv is not None)
