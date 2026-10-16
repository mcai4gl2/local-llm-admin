"""M7: whether a sidecar may start now, swap included (pure)."""

import pytest

from local_llm_admin.sidecars import SidecarSpec, plan_sidecar_start

OPENJEV = SidecarSpec(
    name="openjev",
    label="decision",
    title="Decision sidecar",
    port=8081,
    allowed_mlx=frozenset({"qwen"}),
    footprint_gb=20.0,
)
TTS = SidecarSpec(
    name="qwen-tts",
    label="TTS",
    title="TTS sidecar",
    port=8083,
    allowed_mlx=frozenset({"qwen", "small"}),
    footprint_gb=6.0,
)


def budget(spec=OPENJEV, **over):
    args = dict(
        spec=spec,
        other=None,
        confirm=False,
        comfy_running=False,
        mlx_running=True,
        mlx_name="qwen",
        mlx_gb=24.0,
        headroom_gb=6.0,
        ram_gb=64.0,
        wired_limit_gb=None,
        pressure="normal",
    )
    args.update(over)
    return plan_sidecar_start(**args)


def test_budget_passes_for_measured_pair():
    b = budget()
    assert b.ok and b.total_gb == 44.0 and b.limit_gb == 58.0 and b.stop_first is None
    assert b.lines == [
        "MLX 24.0 GB (qwen)",
        "decision 20.0 GB",
        "limit 58.0 GB (RAM 64 - headroom 6)",
    ]


def test_other_running_needs_confirm():
    b = budget(TTS, other=OPENJEV)
    assert not b.ok and b.needs_confirm and b.stop_first is None
    assert "the decision sidecar is running; confirm to stop it and start the TTS sidecar" in b.reasons


def test_confirmed_swap_counts_only_the_new_footprint():
    b = budget(TTS, other=OPENJEV, confirm=True)
    assert b.ok and not b.needs_confirm and b.stop_first == OPENJEV
    assert b.total_gb == 30.0
    assert b.lines[0] == "stops the decision sidecar first" and "TTS 6.0 GB" in b.lines


def test_confirmed_swap_still_checks_the_new_allow_list():
    b = budget(OPENJEV, other=TTS, confirm=True, mlx_name="small")
    assert not b.ok and any("not allowed" in r for r in b.reasons)


def test_comfyui_refuses_every_sidecar():
    for spec in (OPENJEV, TTS):
        assert "ComfyUI is running; the sidecar never runs next to it" in budget(
            spec, comfy_running=True
        ).reasons


def test_budget_with_mlx_stopped_counts_only_sidecar():
    b = budget(mlx_running=False, mlx_name=None, mlx_gb=None)
    assert b.ok and b.total_gb == 20.0 and b.lines[0] == "MLX stopped"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"comfy_running": True}, "ComfyUI"),
        ({"mlx_name": "qwen-8bit"}, "not allowed"),
        ({"mlx_name": None}, "(unknown) is not allowed"),
        ({"mlx_gb": None}, "cannot estimate"),
        ({"pressure": "warn"}, "pressure is warn"),
        ({"pressure": None}, "pressure is unavailable"),
        ({"mlx_gb": 40.0}, "exceeds the 58.0 GB limit"),
        ({"wired_limit_gb": 40.0}, "exceeds the 40.0 GB limit"),
    ],
)
def test_budget_refusals(over, reason):
    b = budget(**over)
    assert not b.ok and any(reason in r for r in b.reasons), b.reasons


def test_budget_wired_limit_line_and_unknown_total():
    assert budget(wired_limit_gb=50.0).lines[2] == "limit 50.0 GB (GPU wired limit)"
    assert budget(wired_limit_gb=60.0).lines[2] == "limit 58.0 GB (RAM 64 - headroom 6)"
    b = budget(mlx_gb=None)
    assert b.total_gb is None and b.mlx_gb is None and b.lines[0] == "MLX unknown (qwen)"


def test_budget_reports_every_reason():
    b = budget(comfy_running=True, pressure="critical", mlx_gb=50.0)
    assert len(b.reasons) == 3


# --- M8: the always-on service in every budget ---------------------------------------

from local_llm_admin.sidecars import plan_service_start  # noqa: E402

EMBED = SidecarSpec(
    name="qwen-embed",
    label="embeddings",
    title="Embeddings service",
    port=8084,
    allowed_mlx=frozenset({"qwen", "small"}),
    footprint_gb=7.0,
    kind="service",
    intent_key="embed_intent",
)


def service_budget(**over):
    args = dict(
        spec=EMBED,
        sidecar=None,
        comfy_running=False,
        mlx_running=True,
        mlx_name="qwen",
        mlx_gb=24.0,
        headroom_gb=6.0,
        ram_gb=64.0,
        wired_limit_gb=None,
        pressure="normal",
    )
    args.update(over)
    return plan_service_start(**args)


def test_sidecar_budget_counts_running_service():
    b = budget(OPENJEV, services=[EMBED])
    assert b.ok and b.total_gb == 51.0
    assert "embeddings 7.0 GB (running)" in b.lines
    over = budget(OPENJEV, services=[EMBED], mlx_gb=32.0)
    assert not over.ok and "estimated 59.0 GB exceeds the 58.0 GB limit" in over.reasons


def test_service_budget_counts_slot_sidecar():
    b = service_budget(sidecar=OPENJEV)
    assert b.ok and b.total_gb == 51.0 and not b.needs_confirm and b.stop_first is None
    assert b.lines == [
        "MLX 24.0 GB (qwen)",
        "decision 20.0 GB (running)",
        "embeddings 7.0 GB",
        "limit 58.0 GB (RAM 64 - headroom 6)",
    ]
    assert not service_budget(sidecar=OPENJEV, mlx_gb=32.0).ok


def test_service_budget_refusals():
    b = service_budget(comfy_running=True, mlx_name="big", pressure="warn")
    assert not b.ok
    assert "ComfyUI is running; the embeddings service never runs next to it" in b.reasons
    assert any("not allowed with the embeddings service" in r for r in b.reasons)
    assert "memory pressure is warn, not normal" in b.reasons
    assert "cannot estimate" in " ".join(service_budget(mlx_gb=None).reasons)


def test_service_may_run_with_the_engine_off():
    b = service_budget(mlx_running=False, mlx_name=None, mlx_gb=None)
    assert b.ok and b.total_gb == 7.0 and b.lines[0] == "MLX stopped"


def test_service_budget_respects_wired_limit():
    b = service_budget(wired_limit_gb=30.0)
    assert not b.ok and b.limit_gb == 30.0 and b.lines[-1] == "limit 30.0 GB (GPU wired limit)"


def test_sidecar_never_starts_beside_strata():
    b = budget(strata_running=True, mlx_running=False, mlx_name=None, mlx_gb=None)
    assert not b.ok and any("Strata is running" in r for r in b.reasons)


def test_service_never_starts_beside_strata():
    b = service_budget(strata_running=True, mlx_running=False, mlx_name=None, mlx_gb=None)
    assert not b.ok and any("Strata is running" in r for r in b.reasons)
