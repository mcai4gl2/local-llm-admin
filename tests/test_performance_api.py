"""M4 §4: performance, availability and rotation API, page, and the monitor wiring."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.app import create_app
from local_llm_admin.availability import AvailabilityStore
from local_llm_admin.performance_store import PERF_SOURCE, PerformanceStore

FIX = Path(__file__).parent / "fixtures"
HKT = timezone(timedelta(hours=8))


@pytest.fixture
def settings(fake_comfy):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "test-secret"
    return s


@pytest.fixture
async def app(settings, fake_comfy):
    app = create_app(settings, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        yield c


def _db(settings):
    return settings.admin_data_dir / "admin.sqlite"


def _today() -> date:
    return datetime.now(HKT).date()


def _row(i, started, **kw):
    base = {
        "key": f"k{i}",
        "request_id": f"r{i}",
        "started": started,
        "finished": started + 2,
        "model": "qwen",
        "attribution": "generation",
        "stream": 1,
        "images": 0,
        "prompt_tokens": 10,
        "cached_tokens": 0,
        "generated_tokens": 5,
        "prefill_s": 0.1,
        "prefill_tok_s": 100.0,
        "ttft_s": 1.0,
        "decode_s": 0.5,
        "decode_tok_s": 10.0,
        "elapsed_s": 1.5,
        "finish_reason": "stop",
        "state": "completed",
        "error": None,
        "complete": 1,
        "log_identity": "1:2:0",
        "log_start": 0,
        "log_end": 10,
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    "path",
    [
        "/api/performance/summary",
        "/api/performance/requests",
        "/api/performance/requests/1/context",
        "/api/performance/tokens",
        "/api/availability",
        "/api/maintenance/log-rotation",
    ],
)
async def test_endpoints_require_login(app, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.get(path)).status_code == 401


async def test_summary_reports_measured_values_and_source(client, settings):
    now = time.time()
    PerformanceStore(_db(settings)).insert_rows(
        PERF_SOURCE,
        [
            _row(1, now - 60, ttft_s=2.0),
            _row(2, now - 50, ttft_s=20.0),
            _row(3, now - 40, state="error", ttft_s=None, decode_tok_s=None, complete=0),
        ],
    )
    r = await client.get("/api/performance/summary")
    assert r.status_code == 200
    body = r.json()
    t = body["totals"]
    assert t["requests"] == 3 and t["errors"] == 1 and t["slow"] == 1 and t["partial"] == 1
    assert t["ttft_p50"] == 2.0 and t["ttft_p90"] == 20.0
    assert body["to"] == _today().isoformat()
    assert body["source"]["source"] == PERF_SOURCE and "mlx-vlm" in body["source"]["parser"]
    assert body["source"]["last_ingest"] is None
    assert body["models"] == ["qwen"]
    assert "cache_hit" not in str(body)  # never a guessed cache percentage


async def test_summary_model_filter_and_range_validation(client, settings):
    now = time.time()
    PerformanceStore(_db(settings)).insert_rows(
        PERF_SOURCE, [_row(1, now - 60), _row(2, now - 50, model="gemma")]
    )
    body = (await client.get("/api/performance/summary?model=gemma")).json()
    assert body["totals"]["requests"] == 1 and body["models"] == ["gemma", "qwen"]
    assert (await client.get("/api/performance/summary?from=2026-13-01")).status_code == 400
    assert (
        await client.get("/api/performance/summary?from=2026-01-01&to=2026-06-30")
    ).status_code == 400
    too_long = "x" * 201
    assert (await client.get(f"/api/performance/summary?model={too_long}")).status_code == 422


async def test_requests_are_paginated_filtered_and_bounded(client, settings):
    now = time.time()
    PerformanceStore(_db(settings)).insert_rows(
        PERF_SOURCE,
        [
            _row(
                i,
                now - 100 + i,
                state="error" if i == 3 else "completed",
                ttft_s=30.0 if i == 4 else 1.0,
            )
            for i in range(6)
        ],
    )
    page = (await client.get("/api/performance/requests?limit=2")).json()
    assert [r["request_id"] for r in page["requests"]] == ["r5", "r4"]
    nxt = (await client.get(f"/api/performance/requests?limit=2&before={page['next']}")).json()
    assert [r["request_id"] for r in nxt["requests"]] == ["r3", "r2"]
    errs = (await client.get("/api/performance/requests?state=error")).json()
    assert [r["request_id"] for r in errs["requests"]] == ["r3"]
    slow = (await client.get("/api/performance/requests?slow=true")).json()
    assert [r["request_id"] for r in slow["requests"]] == ["r4"]
    assert (await client.get("/api/performance/requests?limit=500")).status_code == 422
    assert (await client.get("/api/performance/requests?state=weird")).status_code == 422


async def test_request_context_is_bounded_redacted_and_only_from_the_same_file(
    app, client, settings
):
    log = settings.mlx_log_dir / "mlx-server.log"
    text = (FIX / "mac/mlx-server.log").read_text()
    text = text.replace(
        "Decode started: request=735875ffb0",
        "Decode started: request=735875ffb0 api_key=sk-abcdef123456",
    )
    log.write_text(text)
    await app.state.supervisor.tick()
    rows = (await client.get("/api/performance/requests")).json()["requests"]
    short = next(r for r in rows if r["request_id"] == "735875ffb0")
    ctx = (await client.get(f"/api/performance/requests/{short['id']}/context")).json()
    assert ctx["available"] is True
    assert ctx["lines"][0].endswith("prompt_tokens=17 max_tokens=50 images=0 audio=0 videos=0")
    assert any("Decode completed" in ln for ln in ctx["lines"])
    assert not any("sk-abcdef" in ln for ln in ctx["lines"])
    assert all("Request started" not in ln for ln in ctx["lines"][:1])
    # the log was replaced: the stored offsets no longer point at this request
    fresh = log.with_name("fresh.tmp")
    fresh.write_text(text)  # allocated while the old inode is alive: a different file
    fresh.replace(log)
    gone = (await client.get(f"/api/performance/requests/{short['id']}/context")).json()
    assert gone["available"] is False and gone["lines"] == []
    assert (await client.get("/api/performance/requests/99999/context")).status_code == 404


async def test_request_context_reads_a_rotated_archive(app, client, settings):
    log = settings.mlx_log_dir / "mlx-server.log"
    log.write_text((FIX / "mac/mlx-server.log").read_text())
    await app.state.supervisor.tick()
    log.rename(log.with_name("mlx-server.log.20261001-040000"))
    log.write_text("")
    rows = (await client.get("/api/performance/requests")).json()["requests"]
    ctx = (await client.get(f"/api/performance/requests/{rows[0]['id']}/context")).json()
    assert ctx["available"] is True and ctx["lines"]


async def test_availability_endpoint(client, settings):
    store = AvailabilityStore(_db(settings))
    now = time.time()
    for k in range(10):
        store.add(now - 600 + k * 60, "serving", 1, None, "qwen", "normal")
    body = (await client.get("/api/availability")).json()
    assert body["totals"]["availability"] == 1.0
    assert body["interval_s"] == settings.availability_interval_s
    assert body["segments"][0]["state"] == "serving"
    assert 0 < body["totals"]["coverage"] < 1  # most of the 30 days was never observed
    assert (await client.get("/api/availability?from=bad")).status_code == 400


async def test_rotation_state_endpoint(client, settings):
    body = (await client.get("/api/maintenance/log-rotation")).json()
    assert body["max_mib"] == settings.log_rotate_max_mib and body["last"] is None


async def test_performance_page_renders_with_nav(client):
    r = await client.get("/performance")
    assert r.status_code == 200
    assert 'href="/performance"' in r.text and "/static/performance.js" in r.text
    assert (await client.get("/static/performance.js")).status_code == 200


async def test_a_monitor_tick_ingests_requests_and_samples_availability(app, client, settings):
    (settings.mlx_log_dir / "mlx-server.log").write_text((FIX / "mac/mlx-server.log").read_text())
    await app.state.supervisor.tick()
    rows = (await client.get("/api/performance/requests")).json()["requests"]
    assert len(rows) == 3
    samples = AvailabilityStore(_db(settings)).samples(0, 2e9)
    assert len(samples) == 1
    body = (await client.get("/api/performance/summary")).json()
    # the recorded log is from 2026-09-28; the default range ends today
    assert body["source"]["last_ingest"] is not None


async def test_context_read_failure_is_reported_not_raised(client, settings, monkeypatch):
    import local_llm_admin.app as app_mod

    PerformanceStore(_db(settings)).insert_rows(PERF_SOURCE, [_row(1, time.time())])

    def boom(*a, **kw):
        raise OSError("hung volume")

    monkeypatch.setattr(app_mod, "request_context", boom)
    row = (await client.get("/api/performance/requests")).json()["requests"][0]
    ctx = (await client.get(f"/api/performance/requests/{row['id']}/context")).json()
    assert ctx == {"available": False, "lines": [], "reason": "the log could not be read"}


async def test_a_hung_request_poller_never_hides_the_watchdogs_log_evidence(app, settings):
    import threading

    sup = app.state.supervisor
    settings.probe_timeout_s = 0.3
    release = threading.Event()
    app.state.perf_logs.poll = lambda: release.wait(5)  # a huge first scan, or a hung drive
    seen: list = []
    real = sup._download_events
    sup._download_events = lambda polled, wall: (seen.append(set(polled)), real(polled, wall))
    try:
        await sup.tick()
    finally:
        release.set()
    assert seen and "mlx-server" in seen[0] and PERF_SOURCE not in seen[0]


async def test_request_ingestion_skips_an_unknown_drive_and_logs_errors(
    app, settings, monkeypatch, caplog
):
    sup = app.state.supervisor
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise OSError("read failed")

    app.state.perf_logs.poll = boom
    monkeypatch.setattr(sup.mlx, "drive_mounted", lambda: None)
    await sup.tick()
    assert calls == []  # never touches an unknown drive
    monkeypatch.undo()
    app.state.perf_logs.poll = boom
    await sup.tick()
    assert calls == [1] and "request ingestion failed" in caplog.text


async def test_the_daily_retention_pass_prunes_old_requests(app, settings):
    store = PerformanceStore(_db(settings))
    old = time.time() - 200 * 86400
    store.insert_rows(PERF_SOURCE, [_row(i, old + i) for i in range(3)])
    store.insert_rows(PERF_SOURCE, [_row(9, time.time())])
    sup = app.state.supervisor
    await sup.tick()  # the first tick runs the daily prune
    rows = await _anon_rows(settings)
    assert rows == 1
    # the pruned rows' tokens were folded, not lost
    t = store.tokens(0, 4e9, tz_offset_min=settings.litellm_timezone_offset_min, group="day")
    assert t["totals"]["requests"] == 4 and t["totals"]["input_tokens"] == 40


async def _anon_rows(settings) -> int:
    return len(PerformanceStore(_db(settings)).requests(0, 2e9)["requests"])


async def test_token_usage_by_day_and_model(client, settings):
    now = time.time()
    PerformanceStore(_db(settings)).insert_rows(
        PERF_SOURCE,
        [
            _row(1, now - 60, prompt_tokens=100, cached_tokens=30, generated_tokens=7),
            _row(2, now - 50, model="gemma", prompt_tokens=20, cached_tokens=None),
            _row(3, now - 40, prompt_tokens=None, generated_tokens=None, complete=0),
        ],
    )
    r = await client.get("/api/performance/tokens")
    assert r.status_code == 200
    body = r.json()
    assert body["to"] == _today().isoformat() and body["tz_offset_min"] == (
        settings.litellm_timezone_offset_min
    )
    t = body["totals"]
    assert (t["requests"], t["measured"], t["unmeasured"], t["cached_unknown"]) == (3, 2, 1, 1)
    assert (t["input_tokens"], t["cached_tokens"], t["output_tokens"]) == (120, 30, 12)
    assert [d["date"] for d in body["days"]] == [_today().isoformat()]
    assert [(m["model"], m["requests"]) for m in body["models"]] == [("gemma", 1), ("qwen", 2)]
    assert body["models"][0]["first"] == body["models"][0]["last"] == _today().isoformat()
    one = (await client.get("/api/performance/tokens?model=gemma")).json()
    assert one["totals"]["requests"] == 1
    assert (await client.get("/api/performance/tokens?origin=quality")).json()["totals"][
        "requests"
    ] == 0
    assert (await client.get("/api/performance/tokens?origin=client")).json()["totals"][
        "requests"
    ] == 3


async def test_token_usage_validates_its_filters(client):
    assert (await client.get("/api/performance/tokens?from=2026-13-01")).status_code == 400
    assert (await client.get("/api/performance/tokens?origin=everyone")).status_code == 422
    too_long = "x" * 201
    assert (await client.get(f"/api/performance/tokens?model={too_long}")).status_code == 422


async def test_the_strata_usage_poller_is_a_monitor_listener(app):
    from local_llm_admin.strata_usage import StrataUsage

    usage = app.state.strata_usage
    assert isinstance(usage, StrataUsage)
    await usage.on_tick(None, {"engine": "mlx"})  # not the engine: no call, no error
