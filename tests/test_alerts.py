import asyncio
from dataclasses import replace

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.alerts import AlertStore, Evaluator, Snapshot
from local_llm_admin.app import create_app
from local_llm_admin.ingestion import IngestStore

T0 = 1_790_000_000.0


@pytest.fixture
def store(tmp_path):
    return AlertStore(tmp_path / "admin.sqlite")


@pytest.fixture
def events(tmp_path):
    return IngestStore(tmp_path / "admin.sqlite")


def _open(store):
    return {a["key"]: a for a in store.list("open", limit=100)}


# --- store -----------------------------------------------------------------------


def test_condition_dedups_while_open_and_recurs_after_resolution(store):
    a = store.raise_condition("drive_missing", "critical", "Drive", "gone", T0)
    b = store.raise_condition("drive_missing", "critical", "Drive", "still gone", T0 + 15)
    assert a == b
    row = _open(store)["drive_missing"]
    assert (row["count"], row["last_seen"], row["detail"]) == (1, T0 + 15, "still gone")
    assert store.resolve_condition("drive_missing", T0 + 30)
    assert not store.resolve_condition("drive_missing", T0 + 45)
    c = store.raise_condition("drive_missing", "critical", "Drive", "gone again", T0 + 60)
    assert c != a and _open(store)["drive_missing"]["acked_at"] is None


def test_explicit_count_tracks_distinct_observations(store):
    store.raise_condition("request_errors:x", "warning", "E", "", T0, count=3)
    store.raise_condition("request_errors:x", "warning", "E", "", T0 + 15)
    assert _open(store)["request_errors:x"]["count"] == 3
    store.raise_condition("request_errors:x", "warning", "E", "", T0 + 30, count=5)
    assert _open(store)["request_errors:x"]["count"] == 5


def test_ack_hides_from_count_but_not_from_open(store):
    aid = store.raise_condition("engine_down", "critical", "Down", "", T0)
    store.event("engine_restarted", "warning", "Restarted", "", T0)
    assert store.summary() == {"open": 2, "unacked": 2, "worst_severity": "critical"}
    assert store.ack(aid, T0 + 1)
    assert store.summary() == {"open": 2, "unacked": 1, "worst_severity": "warning"}
    assert "engine_down" in _open(store)
    assert not store.ack(9999, T0)
    assert store.ack_all(T0 + 2) == 1
    assert store.summary()["unacked"] == 0
    assert set(_open(store)) == {"engine_down"}  # an acked event leaves "open"


def test_events_are_deduplicated_by_key_and_dedup(store):
    assert store.event("download_finished", "info", "Done", "", T0, dedup="7") is not None
    assert store.event("download_finished", "info", "Done", "", T0, dedup="7") is None
    assert len(store.list("all", limit=10)) == 1


def test_retention_keeps_open_conditions(store):
    store.raise_condition("old_open", "warning", "x", "", T0 - 200 * 86400)
    store.raise_condition("old_resolved", "warning", "x", "", T0 - 200 * 86400)
    store.resolve_condition("old_resolved", T0 - 100 * 86400)
    store.event("old_event", "info", "x", "", T0 - 100 * 86400)
    store.event("new_event", "info", "x", "", T0)
    assert store.cleanup(T0, retention_days=90) == 2
    assert {a["key"] for a in store.list("all", limit=10)} == {"old_open", "new_event"}


def test_list_paginates_newest_first(store):
    ids = [store.event(f"e{i}", "info", "x", "", T0 + i) for i in range(5)]
    page = store.list("all", limit=2)
    assert [a["id"] for a in page] == ids[:-3:-1]
    nxt = store.list("all", limit=2, before=page[-1]["id"])
    assert [a["id"] for a in nxt] == [ids[2], ids[1]]


# --- evaluator -------------------------------------------------------------------


def _snap(**kw):
    base = Snapshot(
        wall=T0,
        mono=1000.0,
        drive_mounted=True,
        access_error=None,
        access_known=True,
        engine="mlx",
        mlx_serving=True,
        intent="mlx:qwen",
        job_running=False,
        lock={"state": "none", "age_s": None, "owner": None, "legacy": False},
        pressure="normal",
        litellm_ok=True,
        suspended=None,
    )
    return replace(base, **kw)


def _at(snap, dt):
    return replace(snap, wall=snap.wall + dt, mono=snap.mono + dt)


@pytest.fixture
def ev(store, events):
    return Evaluator(store, events)


def test_healthy_snapshot_raises_nothing(ev, store):
    ev.evaluate(_snap())
    assert _open(store) == {}


def test_drive_missing_and_unknown_does_not_resolve(ev, store):
    s = _snap(drive_mounted=False)
    ev.evaluate(s)
    assert "drive_missing" in _open(store)
    ev.evaluate(_at(replace(s, drive_mounted=None), 15))
    assert "drive_missing" in _open(store)  # an unknown probe keeps the fault
    ev.evaluate(_at(replace(s, drive_mounted=True), 30))
    assert "drive_missing" not in _open(store)


def test_access_error(ev, store):
    ev.evaluate(_snap(access_error="denied"))
    assert "drive_access" in _open(store)
    ev.evaluate(_snap(access_known=False))
    assert "drive_access" in _open(store)
    ev.evaluate(_snap())
    assert "drive_access" not in _open(store)


def test_engine_down_after_30_s_without_job(ev, store):
    s = _snap(engine="none", mlx_serving=None)
    ev.evaluate(s)
    ev.evaluate(_at(s, 29))
    assert "engine_down" not in _open(store)
    ev.evaluate(_at(replace(s, job_running=True), 45))  # a job resets the clock
    ev.evaluate(_at(s, 50))
    ev.evaluate(_at(s, 79))
    assert "engine_down" not in _open(store)
    ev.evaluate(_at(s, 81))
    assert _open(store)["engine_down"]["severity"] == "critical"
    ev.evaluate(_at(_snap(), 90))
    assert "engine_down" not in _open(store)


def test_comfyui_intent_mismatch_alerts(ev, store):
    s = _snap(intent="comfyui", engine="none")
    ev.evaluate(s)
    ev.evaluate(_at(s, 31))
    assert "ComfyUI" in _open(store)["engine_down"]["title"]


def test_intent_off_or_unknown_never_alerts_down(ev, store):
    for intent in ("off", "unknown", None):
        s = _snap(intent=intent, engine="none")
        ev.evaluate(s)
        ev.evaluate(_at(s, 60))
    assert "engine_down" not in _open(store)


def test_engine_down_unknown_observation_keeps_state(ev, store):
    s = _snap(engine="none")
    ev.evaluate(s)
    ev.evaluate(_at(s, 31))
    ev.evaluate(_at(replace(s, engine="unknown"), 45))
    assert "engine_down" in _open(store)


def test_conflict(ev, store):
    ev.evaluate(_snap(engine="conflict"))
    assert "engine_conflict" in _open(store)
    ev.evaluate(_snap(engine="unknown"))
    assert "engine_conflict" in _open(store)
    ev.evaluate(_snap())
    assert "engine_conflict" not in _open(store)


def test_unhealthy_mlx_after_five_minutes(ev, store):
    s = _snap(mlx_serving=False)
    ev.evaluate(s)
    ev.evaluate(_at(s, 299))
    assert "engine_unhealthy" not in _open(store)
    ev.evaluate(_at(s, 301))
    assert "engine_unhealthy" in _open(store)
    ev.evaluate(_at(_snap(), 310))
    assert "engine_unhealthy" not in _open(store)


def test_stale_lock_after_ten_minutes(ev, store):
    stale = {"state": "stale", "age_s": 599, "owner": None, "legacy": True}
    ev.evaluate(_snap(lock=stale))
    assert "stale_lock" not in _open(store)
    ev.evaluate(_snap(lock={**stale, "age_s": 601}))
    assert "stale_lock" in _open(store)
    ev.evaluate(_snap(lock=None))  # unknown
    assert "stale_lock" in _open(store)
    ev.evaluate(_snap())
    assert "stale_lock" not in _open(store)


def test_memory_pressure_sustained_both_ways(ev, store):
    s = _snap(pressure="critical")
    ev.evaluate(s)
    ev.evaluate(_at(s, 119))
    assert "memory_critical" not in _open(store)
    ev.evaluate(_at(s, 121))
    assert "memory_critical" in _open(store)
    n = _at(_snap(), 130)
    ev.evaluate(n)
    ev.evaluate(_at(replace(n, pressure="warn"), 200))  # not normal: no resolve clock
    ev.evaluate(_at(n, 260))
    assert "memory_critical" in _open(store)
    ev.evaluate(_at(n, 381))
    assert "memory_critical" not in _open(store)


def test_litellm_needs_two_consecutive_failures(ev, store):
    ev.evaluate(_snap(litellm_ok=False))
    ev.evaluate(_snap(litellm_ok=None))  # not checked this tick
    assert "litellm_unreachable" not in _open(store)
    ev.evaluate(_snap(litellm_ok=False))
    assert "litellm_unreachable" in _open(store)
    ev.evaluate(_snap(litellm_ok=True))
    assert "litellm_unreachable" not in _open(store)
    ev.evaluate(_snap(litellm_ok=False))
    ev.evaluate(_snap(litellm_ok=True))
    ev.evaluate(_snap(litellm_ok=False))
    assert "litellm_unreachable" not in _open(store)


def test_watchdog_suspended(ev, store):
    ev.evaluate(_snap(suspended="second crash"))
    assert _open(store)["watchdog_suspended"]["detail"] == "second crash"
    ev.evaluate(_snap())
    assert "watchdog_suspended" not in _open(store)


def _err(events, i, ts, sig="s1", msg="Error loading model gpt-4o"):
    cur = {
        "dev": 1,
        "inode": 1,
        "offset": 0,
        "carry": b"",
        "skipping": 0,
        "gap_at": None,
        "updated": ts,
    }
    events.commit(
        "mlx-server",
        cur,
        [
            {
                "kind": "error",
                "message": msg,
                "ts": ts,
                "observed": ts,
                "signature": sig,
                "dedup": f"{sig}{i}",
                "data": {},
            }
        ],
    )


def test_request_errors_threshold_and_quiet_resolution(ev, store, events):
    for i in range(2):
        _err(events, i, T0 - 60 * i)
    ev.evaluate(_snap())
    assert not [k for k in _open(store) if k.startswith("request_errors")]
    _err(events, 2, T0 - 10)
    ev.evaluate(_snap())
    (alert,) = [a for k, a in _open(store).items() if k == "request_errors:s1"]
    assert alert["count"] == 3 and "gpt-4o" in alert["detail"] and "active" in alert["detail"]
    ev.evaluate(_at(_snap(), 59 * 60))
    assert "request_errors:s1" in _open(store)
    ev.evaluate(_at(_snap(), 61 * 60))
    assert "request_errors:s1" not in _open(store)


def test_errors_spread_over_more_than_15_minutes_do_not_alert(ev, store, events):
    for i in range(3):
        _err(events, i, T0 - 600 * i)
    ev.evaluate(_snap())
    assert "request_errors:s1" not in _open(store)


# --- API -----------------------------------------------------------------------------


@pytest.fixture
async def api(fake_comfy):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    s.probe_timeout_s = 2
    s.litellm_base_url = "http://127.0.0.1:1"  # refused
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
            yield c, app, fake_comfy


async def test_alert_endpoints_require_login_and_csrf(fake_comfy):
    s = fake_comfy.settings
    s.admin_session_secret = "t"
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as c:
        for path in ("/api/alerts", "/api/diagnostics"):
            assert (await c.get(path)).status_code == 401
        for path in (
            "/api/alerts/1/ack",
            "/api/alerts/ack-all",
            "/api/diagnostics/unlock",
            "/api/watchdog/resume",
        ):
            assert (await c.post(path)).status_code == 401


async def test_ack_flow_and_badge_counts(api):
    c, app, _ = api
    store = app.state.alerts
    aid = store.raise_condition("drive_missing", "critical", "Drive", "gone", T0)
    st = (await c.get("/api/status")).json()
    assert st["alerts"] == {"open": 1, "unacked": 1, "worst_severity": "critical"}
    token = c.headers.pop("X-CSRF-Token")
    assert (await c.post(f"/api/alerts/{aid}/ack")).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert (await c.post(f"/api/alerts/{aid}/ack")).status_code == 200
    assert (await c.post("/api/alerts/99999/ack")).status_code == 404
    assert (await c.post("/api/alerts/abc/ack")).status_code == 422
    body = (await c.get("/api/alerts", params={"state": "open"})).json()
    assert [a["id"] for a in body["alerts"]] == [aid] and body["alerts"][0]["acked_at"]
    assert body["summary"]["unacked"] == 0
    store.event("engine_restarted", "warning", "R", "", T0)
    assert (await c.post("/api/alerts/ack-all")).json() == {"acked": 1}
    assert (await c.get("/api/alerts", params={"state": "bogus"})).status_code == 422
    assert (await c.get("/api/alerts", params={"limit": 0})).status_code == 422


async def test_alerts_pagination(api):
    c, app, _ = api
    ids = [app.state.alerts.event(f"e{i}", "info", "x", "", T0 + i) for i in range(4)]
    page = (await c.get("/api/alerts", params={"state": "all", "limit": 2})).json()
    assert [a["id"] for a in page["alerts"]] == [ids[3], ids[2]]
    nxt = (
        await c.get(
            "/api/alerts", params={"state": "all", "limit": 2, "before": page["alerts"][-1]["id"]}
        )
    ).json()
    assert [a["id"] for a in nxt["alerts"]] == [ids[1], ids[0]]


async def test_diagnostics_endpoint(api):
    c, _, _ = api
    checks = {x["id"]: x for x in (await c.get("/api/diagnostics")).json()["checks"]}
    assert checks["drive"]["status"] == "ok"
    assert checks["litellm_network"]["status"] == "fail"


async def _job(c, job_id):
    for _ in range(300):
        j = (await c.get(f"/api/jobs/{job_id}")).json()
        if j["status"] != "running":
            return j
        await asyncio.sleep(0.02)
    raise AssertionError("stuck")


async def test_unlock_runs_as_a_job_and_bypasses_only_the_lock_check(api):
    c, app, fake = api
    lock = fake.settings.run_dir / "mlx.lock"
    lock.mkdir()
    assert (await c.post("/api/models/stop")).status_code == 409  # lock blocks mutations
    r = await c.post("/api/diagnostics/unlock")
    assert r.status_code == 202
    job = await _job(c, r.json()["id"])
    assert job["status"] == "succeeded" and job["action"] == "unlock"
    assert "Removed stale lock" in job["output"] and not lock.exists()
    checks = {x["id"]: x for x in (await c.get("/api/diagnostics")).json()["checks"]}
    assert checks["mlx_lock"]["status"] == "ok"
    before = app.state.control.intent()
    assert (await c.post("/api/diagnostics/unlock")).status_code == 202
    await asyncio.sleep(0.3)
    assert app.state.control.intent() == before  # unlock never changes the intent


async def test_unlock_conflicts_with_a_running_job(api):
    c, app, _ = api
    token = app.state.control.runner.claim()
    try:
        r = await c.post("/api/diagnostics/unlock")
        assert r.status_code == 409 and "another job" in r.json()["detail"]
    finally:
        app.state.control.runner.release(token)


async def test_unlock_refused_without_drive(api):
    c, _, fake = api
    fake.settings.mlx_root = fake.settings.mlx_root.parent / "gone"
    assert (await c.post("/api/diagnostics/unlock")).status_code == 409


async def test_watchdog_resume(api):
    c, app, _ = api
    app.state.control.suspend("second crash")
    st = (await c.get("/api/status")).json()
    assert st["watchdog"]["suspended"] == "second crash"
    assert (await c.post("/api/watchdog/resume")).json()["suspended"] is None
    assert app.state.control.recovery_blocked() is None


async def test_alerts_page_renders(api):
    c, _, _ = api
    r = await c.get("/alerts")
    assert r.status_code == 200 and 'id="alert-list"' in r.text and "alerts.js" in r.text
    assert 'id="nav-alerts"' in r.text


async def test_supervisor_tick_through_the_app(api):
    c, app, _ = api
    app.state.control.store.set_kv("intent", {"value": "mlx:qwen"})  # MLX is not running
    sup = app.state.supervisor
    sup._startup_until = 0
    await sup.tick()
    assert (await c.get("/api/status")).json()["watchdog"]["last_decision"]["action"] == "wait"
