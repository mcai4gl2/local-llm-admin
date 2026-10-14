"""M7: the monitor raises the intended sidecar's own down alert, and only that one."""

import time
from types import SimpleNamespace

from local_llm_admin.alerts import AlertStore
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.jobs import JobStore
from local_llm_admin.sidecars import SidecarMonitor, SidecarRegistry, write_intent


async def test_intent_for_another_sidecar_never_alerts_openjev(fake_decision):
    s = fake_decision.settings
    db = s.admin_data_dir / "admin.sqlite"
    store, alerts = JobStore(db), AlertStore(db)
    mon = SidecarMonitor(s, SidecarRegistry(s), store, alerts)
    write_intent(store, "qwen-tts", "running")  # not enabled here: nothing to alert on
    await mon(SimpleNamespace(job_running=False, wall=time.time()), None)
    assert alerts.open_condition_keys("decision_down") == []


async def test_prune_extra_runs_daily(fake_decision):
    s = fake_decision.settings
    db = s.admin_data_dir / "admin.sqlite"
    calls = []
    mon = SidecarMonitor(
        s,
        SidecarRegistry(s),
        JobStore(db),
        AlertStore(db),
        history=DecisionStore(db),
        prune_extra=[lambda before: calls.append(before) or 0],
    )
    now = time.time()
    await mon(SimpleNamespace(job_running=False, wall=now), None)
    await mon(SimpleNamespace(job_running=False, wall=now + 60), None)
    assert len(calls) == 1


# --- M8: the always-on service -------------------------------------------------------

from local_llm_admin.sidecars import embed_spec, write_service_intent  # noqa: E402


def _mon(fake_embed, history=False):
    s = fake_embed.settings
    db = s.admin_data_dir / "admin.sqlite"
    store, alerts = JobStore(db), AlertStore(db)
    hist = DecisionStore(db) if history else None
    reg = SidecarRegistry(s, {"qwen-embed": fake_embed.transport})
    return SidecarMonitor(s, reg, store, alerts, history=hist), store, alerts, hist


def _snap(engine="mlx", job=False, wall=None):
    return SimpleNamespace(job_running=job, wall=wall or time.time(), engine=engine)


async def test_embed_down_alert_when_meant_and_stopped(fake_embed):
    mon, store, alerts, _ = _mon(fake_embed)
    write_service_intent(store, embed_spec(fake_embed.settings), "running")
    await mon(_snap(), None)
    assert alerts.open_condition_keys("embed_down") == ["embed_down"]
    fake_embed.start()
    await mon(_snap(), None)
    assert alerts.open_condition_keys("embed_down") == []


async def test_no_alert_while_comfyui_active(fake_embed):
    mon, store, alerts, _ = _mon(fake_embed)
    write_service_intent(store, embed_spec(fake_embed.settings), "running")
    await mon(_snap(engine="comfyui"), None)
    assert alerts.open_condition_keys("embed_down") == []


async def test_no_alert_during_job_or_when_meant_stopped(fake_embed):
    mon, store, alerts, _ = _mon(fake_embed)
    spec = embed_spec(fake_embed.settings)
    write_service_intent(store, spec, "running")
    await mon(_snap(job=True), None)
    assert alerts.open_condition_keys("embed_down") == []
    write_service_intent(store, spec, "stopped")
    await mon(_snap(), None)
    assert alerts.open_condition_keys("embed_down") == []


async def test_embed_availability_samples(fake_embed):
    mon, store, _, hist = _mon(fake_embed, history=True)
    write_service_intent(store, embed_spec(fake_embed.settings), "running")
    now = time.time()
    await mon(_snap(engine="comfyui", wall=now), None)  # paused: not an outage
    fake_embed.start()
    await mon(_snap(wall=now + 3600), None)
    counts = hist.availability(now - 1, now + 3601, "qwen-embed")["counts"]
    assert counts["stopped"] == 1 and counts["ready"] == 1 and counts["down"] == 0
