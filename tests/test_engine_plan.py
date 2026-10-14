from dataclasses import replace

import pytest

from local_llm_admin.engines import Target, TransitionRefused, plan_transition
from local_llm_admin.sidecars import SidecarSpec

SPEC = SidecarSpec(name="openjev", label="decision", title="Decision sidecar", port=8081)
TTS = SidecarSpec(name="qwen-tts", label="TTS", title="TTS sidecar", port=8083)


class Ops:
    stop_timeout_s, ready_timeout_s, job_timeout_s = 30.0, 120.0, 300.0

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

    def sidecar_argv(self, name, action):
        return [name, action]

    def sidecar_down(self, name):
        return True

    def sidecar_ready(self, name):
        return True

    def sidecar_warmup(self, name):
        return (lambda: True) if name == "qwen-embed" else None


def plan(target, mlx=False, name="qwen", comfy=False, queue=0, force=False, dj=False, ok=True, spec=SPEC):
    allowed = frozenset({"qwen", "qwen2", "big"}) if ok else frozenset()
    return plan_transition(
        target,
        mlx_running=mlx,
        mlx_name=name,
        comfy_running=comfy,
        comfy_queue=queue,
        force=force,
        ops=Ops(),
        sidecar=replace(spec, allowed_mlx=allowed) if dj else None,
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
        mlx_running=True,
        mlx_name="qwen",
        comfy_running=False,
        comfy_queue=0,
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
        mlx_running=mlx,
        mlx_name="qwen" if mlx else None,
        comfy_running=comfy,
        comfy_queue=0,
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
