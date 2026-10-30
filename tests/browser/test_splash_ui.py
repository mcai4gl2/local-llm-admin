"""Optional real DOM checks: uv run --with playwright pytest tests/browser -q.

All navigation and APIs are fulfilled locally; no runtime or live service is contacted.
Install the isolated browser first with `uv run --with playwright playwright install chromium`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from local_llm_admin.splash_telemetry import source_coverage

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2] / "src/local_llm_admin"


def status(state: str = "ready") -> dict[str, Any]:
    enabled = state != "disabled"
    splash = {
        "id": "splash",
        "label": "Splash",
        "enabled": enabled,
        "exclusive": True,
        "operations": ["profiles", "chat", "quality"],
        "reference": "stock-8bit-32k",
        "running": enabled,
        "ready": state in ("ready", "conflict"),
        "state": "ready" if state == "conflict" else state,
        "note": "Reasoning on; response usage when reported. GPU sidecars stay stopped.",
        "targets": [
            {
                "value": f"splash:{profile}",
                "label": f"Splash · {profile}",
                "max_context": 32768,
                "reasoning": True,
                "cohort": cohort,
            }
            for profile, cohort in [
                ("stock-8bit-32k", "stock-8bit"),
                ("modified-4bit-32k", "modified-4bit"),
            ]
        ],
    }
    mlx = {
        "id": "mlx",
        "label": "MLX",
        "enabled": True,
        "exclusive": False,
        "operations": ["multi-model", "chat", "quality"],
        "targets": [],
        "running": state == "conflict",
        "ready": False,
        "state": "absent",
    }
    return {
        "state": state,
        "selected": "qwen",
        "job": None,
        "engine": {
            "active": "none" if not enabled else "conflict" if state == "conflict" else "splash",
            "backends": {"splash": splash, "mlx": mlx},
            "splash": {"model": "example/stock-8bit", "max_context": 32768},
            "comfyui": {"running": False},
            "strata_enabled": False,
        },
    }


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        print(f"Browser: Chromium {browser.version}")
        yield browser
        browser.close()


@pytest.fixture
def ui(browser: Any) -> Iterator[Any]:
    context = browser.new_context(timezone_id="America/Los_Angeles")
    page = context.new_page()
    page.set_default_timeout(5000)
    errors, mutations = [], []
    current = status()
    page.on("pageerror", lambda e: errors.append(str(e)))
    templates = Environment(
        loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape()
    )

    def route(r: Any) -> None:
        path = urlsplit(r.request.url).path
        if path.startswith("/static/"):
            file = ROOT / path.lstrip("/")
            r.fulfill(path=file)
            return
        if not path.startswith("/api/"):
            name = path.strip("/") or "dashboard"
            html = templates.get_template(name + ".html").render(
                page=name,
                csrf="test",
                version="test",
                decision_enabled=current.get("features", False),
                tts_enabled=current.get("features", False),
                embed_enabled=current.get("features", False),
                ocr_enabled=current.get("features", False),
                ops_timezone_offset_min=current.get("ops_offset", -480),
            )
            r.fulfill(content_type="text/html", body=html)
            return
        if path.startswith("/api/services/") and path.endswith("/history"):
            r.fulfill(json={"recent": [], "daily": [], "availability": {"ratio": None}})
            return
        if r.request.method != "GET":
            try:
                body = r.request.post_data_json
            except Exception:
                body = None  # upload fixture metadata does not retain document bytes
            mutations.append((path, body))
        data = {
            "/api/status": current,
            "/api/sidecars": {"sidecars": [{"name": "openjev", "title": "Decision", "running": False, "ready": False, "weights": "complete", "allowed_mlx": [], "budget": {"ok": False, "lines": [], "reasons": ["Synthetic memory budget unavailable"]}}], "conflict": False},
            "/api/services": {"services": current.get("resident_services", [{"name": "qwen-embed", "running": False, "ready": False, "intent": "stopped", "models": [], "allowed_mlx": [], "budget": {"ok": False, "lines": [], "reasons": ["Synthetic memory budget unavailable"]}}])},
            "/api/comfyui/queue": {"state": "ok", "checked_at": datetime.now(UTC).timestamp(), "running_count": 0, "pending_count": 0, "running": [], "pending": [], "capabilities": {}},
            "/api/comfyui/outputs": {"available": True, "items": [], "total": 0, "scanned_at": 100, "next_offset": None},
            "/api/usage/health": {"reachable": True, "key_ok": True},
            "/api/usage/daily": {"from": parse_qs(urlsplit(r.request.url).query).get("from", ["2026-10-01"])[0], "to": parse_qs(urlsplit(r.request.url).query).get("to", ["2026-10-30"])[0], "totals": {"api_requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "failed_requests": 0}, "days": []},
            "/api/usage/sessions": {"sessions": [], "page": 1, "total_pages": 1, "total_sessions": 0},
            "/api/tts/usage": {"rows": []},
            "/api/embed/usage": {"rows": []},
            "/api/alerts": {"alerts": [], "summary": {"open": 0, "unacked": 0}},
            "/api/chat/config": {"identity": {"engine": current["engine"]["active"], "engine_label": "Splash", "target_label": "Splash · stock-8bit · 128K", "reference": "stock-8bit-32k", "loaded_context": 131072}, "defaults": {"temperature": 0.7, "max_tokens": 8192, "enable_thinking": True}, "output_cap": 32768},
            "/api/decision/clients": {"clients": []},
            "/api/decision/usage": {"rows": [], "gateway": ":8081", "require_key": False},
            "/api/decision": current.get("service", {"running": False, "ready": False}),
            "/api/tts": {"max_chars": 1000, "voices": [], "default_voice": "", **current.get("service", {"running": False, "ready": False})},
            "/api/embed": {"dims": 8, "max_inputs": 5, **current.get("service", {"running": False, "ready": False})},
            "/api/decision/history": {"days": 14, "availability": {"ratio": None}, "daily": [], "recent": []},
            "/api/tts/history": {"availability": {"ratio": None}, "daily": [], "recent": []},
            "/api/embed/history": {"availability": {"ratio": None}, "daily": [], "recent": []},
            "/api/models": {
                "models": [
                    {
                        "name": "qwen",
                        "selected": True,
                        "dir": "Qwen",
                        "size_bytes": 1000,
                        "weights": {"state": "complete", "progress": None},
                        "dir_exists": True,
                        "has_config": True,
                    }
                ],
                "discovered": [],
            },
            "/api/models/inventory": {"available": False, "reason": "fake inventory"},
            "/api/metrics/current": {
                "cpu_percent": 0,
                "cpu_per_core": [],
                "load": [],
                "mem_percent": 0,
                "mem_used": 0,
                "mem_total": 1000,
                "swap_used": 0,
                "pressure": None,
                "gpu_util": None,
                "disks": [],
                "mlx": None,
                "engine_bundle": current.get("bundle"),
            },
            "/api/metrics/history": {"points": []},
            "/api/diagnostics": {"checks": []},
            "/api/history": {"history": current.get("history_rows", [])},
            "/api/downloads/runs": {"runs": [], "allowed_owners": "example"},
            "/api/comfyui": {"running": False, "queue_running": None, "output": {}, "weights": []},
            "/api/quality/latest": {
                "compatibility": {},
                "compatibility_cases": [],
                "settings": {},
                "benchmark_cases": [],
                "warmup": 1,
                "repeats": 2,
            },
            "/api/quality/runs": {"runs": [], "active": None},
            "/api/evals/suites": {
                "suites": [
                    {"id": "core", "title": "Core", "cases": 1, "version": "1", "origin": "builtin"}
                ]
            },
            "/api/evals/scorecard": {"models": []},
            "/api/performance/summary": {
                "source": {"parser": "mlx-vlm", "last_ingest": None},
                "coverage": {
                    "splash": current.get(
                        "native_coverage",
                        source_coverage(current["engine"]["backends"]["splash"]["enabled"]),
                    )
                },
                "models": [],
                "days": [],
                "totals": {},
                "slow_ttft_s": 10,
            },
            "/api/performance/tokens": {
                "totals": {
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "requests": 0,
                    "unmeasured": 0,
                },
                "models": [],
                "days": [],
            },
            "/api/performance/engines": {
                "coverage": current.get(
                    "native_coverage",
                    source_coverage(current["engine"]["backends"]["splash"]["enabled"]),
                ),
                "readings": current.get("native_readings", []),
            },
            "/api/performance/requests": {"requests": [], "next": None},
            "/api/availability": {"from": "2026-10-28", "days": [], "segments": [], "totals": {}},
            "/api/maintenance/log-rotation": {"hour": 4, "max_mib": 50, "keep": 8},
            "/api/logs": {"sources": [{"id": "mlx-server", "exists": True}, {"id": "splash", "exists": True}]},
            "/api/logs/mlx-server": {"exists": True, "size": 10, "lines": ["Synthetic MLX log"]},
            "/api/logs/splash": {"exists": True, "size": 10, "lines": ["Synthetic Splash log"]},
        }.get(path)
        if path == "/api/performance/engines" and data is not None:
            query = parse_qs(urlsplit(r.request.url).query)
            all_rows = [{"id": i + 1, **row} for i, row in enumerate(data["readings"])]
            data["profiles"] = [{"profile": p} for p in sorted({row["profile"] for row in all_rows})]
            data["epochs"] = [{"epoch": e, "profile": next(row["profile"] for row in all_rows if row["epoch"] == e)} for e in sorted({row["epoch"] for row in all_rows})]
            rows = sorted(all_rows, key=lambda row: (row["observed"], row["id"]), reverse=True)
            for key in ("profile", "epoch"):
                if query.get(key):
                    rows = [row for row in rows if row[key] == query[key][0]]
            if query.get("before"):
                ts, row_id = query["before"][0].split(":")
                rows = [row for row in rows if (row["observed"], row["id"]) < (float(ts), int(row_id))]
            limit = int(query.get("limit", [200])[0])
            data["limit"] = limit
            data["readings"] = rows[:limit]
            data["next"] = f"{rows[limit-1]['observed']}:{rows[limit-1]['id']}" if len(rows) > limit else None
        if data is None:
            errors.append("Unexpected API: " + path)
            r.fulfill(status=404, json={"detail": "unexpected fake request"})
        else:
            r.fulfill(json=data)

    context.route("**/*", route)
    yield page, current, errors, mutations
    context.close()
    assert not errors
    assert not mutations


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize(
    "state", ["disabled", "starting", "ready", "released", "recovering", "unknown", "conflict"]
)
def test_models_states_and_selection(ui: Any, width: int, state: str) -> None:
    page, current, _, _ = ui
    current.update(status(state))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://admin.test/models")
    selector = page.locator("#engine-target")
    pw.expect(selector.locator("option")).to_have_count(4 if state != "disabled" else 2)
    if state == "disabled":
        pw.expect(page.locator("#backend-cards")).to_be_empty()
        return
    pw.expect(selector).to_contain_text("stock-8bit · 32K context")
    pw.expect(page.locator("#backend-cards")).to_contain_text("stock-8bit")
    pw.expect(page.locator("#backend-cards")).to_contain_text("32K")
    pw.expect(page.locator("#state-badge")).to_contain_text(
        {
            "starting": "Loading",
            "ready": "Ready",
            "released": "weights released",
            "recovering": "Recovering",
            "unknown": "Unknown",
            "conflict": "Conflict",
        }[state]
    )
    screenshots = Path(".smoke/splash-phase3-browser")
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / f"models-{state}-{width}.png"), full_page=True)
    selector.select_option("splash:modified-4bit-32k")
    page.evaluate("refreshStatus()")
    pw.expect(selector).to_have_value("splash:modified-4bit-32k")
    if state in ("starting", "recovering", "unknown", "conflict"):
        pw.expect(page.locator("#engine-apply")).to_be_disabled()
    else:
        page.locator("#engine-apply").click()
        pw.expect(page.locator("#confirm")).to_be_visible()
        pw.expect(page.locator("#confirm-title")).to_contain_text("modified-4bit")
        pw.expect(page.locator("#confirm-body")).to_contain_text("GPU sidecars")
        page.locator('#confirm button[value="cancel"]').click()
    if state == "conflict":
        pw.expect(page.locator("#conflict-banner")).to_be_visible()
    else:
        pw.expect(page.locator("#conflict-banner")).to_be_hidden()
    if state == "conflict":
        pw.expect(page.locator('[data-keep="splash:stock-8bit-32k"]')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
        "[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth).map(e => [e.tagName, e.id, e.className, e.getBoundingClientRect().right])"
    )


@pytest.mark.parametrize(
    "state", ["disabled", "starting", "ready", "released", "recovering", "unknown", "conflict"]
)
@pytest.mark.parametrize("width", [390, 1440])
def test_chat_quality_and_log_controls(ui: Any, state: str, width: int) -> None:
    page, current, _, _ = ui
    current.update(status(state))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#state-badge")).not_to_have_text("…")
    if state == "ready":
        pw.expect(page.locator("#btn-send")).to_be_enabled()
        pw.expect(page.locator("#chat-engine-note")).to_contain_text("Reasoning on")
    else:
        pw.expect(page.locator("#btn-send")).to_be_disabled()
    page.goto("http://admin.test/quality")
    if state == "ready":
        pw.expect(page.locator("#quality-start")).to_be_enabled()  # compatibility
    page.locator("#q-kind").select_option("compatibility")
    if state == "ready":
        pw.expect(page.locator("#quality-start")).to_be_enabled()
        pw.expect(page.locator("#eval-start")).to_be_enabled()
        pw.expect(page.locator("#eval-thinking")).to_be_checked()
        pw.expect(page.locator('#q-scope option[value="all"]')).to_have_attribute("disabled", "")
    elif state != "disabled":
        pw.expect(page.locator("#quality-start")).to_be_disabled()
        pw.expect(page.locator("#eval-start")).to_be_disabled()
    page.goto("http://admin.test/logs?source=splash")
    pw.expect(page.locator("#source")).to_have_value("splash")
    pw.expect(page.locator("#log-view")).to_contain_text("Synthetic Splash log")
    pw.expect(page.locator("#download")).to_have_attribute("href", "/api/logs/splash/download")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("width", [390, 1440])
def test_performance_displays_coverage_gap_separately_from_historical_totals(
    ui: Any, enabled: bool, width: int
) -> None:
    page, current, _, _ = ui
    current.update(status("ready" if enabled else "disabled"))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://admin.test/performance")
    page.locator("#perf-engine").select_option("mlx")
    pw.expect(page.locator("#perf-source")).to_contain_text("MLX server log")
    gap = page.locator("#splash-coverage")
    pw.expect(gap).to_be_hidden()
    page.locator("#perf-engine").select_option("splash")
    pw.expect(gap).to_be_visible()
    pw.expect(gap).to_contain_text("Complete request history is unsupported")
    pw.expect(page.locator("#tokens")).to_be_hidden()
    pw.expect(page.locator("#perf-error")).to_be_hidden()


def test_native_snapshot_history_and_profile_filter_in_real_dom(ui: Any) -> None:
    from tests.test_splash_ingestion import reading

    page, current, _, _ = ui
    current["native_readings"] = [
        {**reading(), "observed": 100},
        {**reading(profile="modified", fingerprint="fp-b"), "observed": 101},
    ]
    current["native_coverage"] = {
        **source_coverage(True),
        "coverage": "aggregate_snapshots",
        "reason": "Native cumulative execution snapshots only; request history and billed token coverage are unavailable.",
        "stale": True,
        "error": "unavailable",
        "last_poll": 100,
        "last_new_data": 90,
        "gaps": [{"reason": "observation_gap"}],
    }
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto("http://admin.test/performance")
    pw.expect(page.locator("#native-coverage")).to_contain_text("aggregate_snapshots")
    pw.expect(page.locator("#native-coverage")).to_contain_text("stale")
    pw.expect(page.locator("#native-readings tbody tr")).to_have_count(2)
    page.locator("#model").select_option("stock")
    pw.expect(page.locator("#native-readings tbody tr")).to_have_count(1)
    pw.expect(page.locator("#native-readings tbody")).to_contain_text("stock")
    pw.expect(page.locator("#native-readings tbody")).to_contain_text("fp-a")
    pw.expect(page.locator("#native-availability")).to_contain_text("observation_gap")
    page.screenshot(path=".smoke/splash-phase3-browser/native-history-390.png", full_page=True)


def test_dashboard_labels_partial_bundle_and_unknown_primary_truthfully(ui: Any) -> None:
    page, current, _, _ = ui
    current["bundle"] = {
        "source": "splash",
        "state": "partial",
        "rss": None,
        "cpu_percent": None,
        "observed_rss": 1000,
        "members": [{"pid": 100}, {"pid": 101}],
    }
    page.goto("http://admin.test/")
    pw.expect(page.locator("#metric-tiles")).to_contain_text("Engine bundle · splash")
    pw.expect(page.locator("#metric-tiles")).to_contain_text("partial")
    pw.expect(page.locator("#metric-tiles")).to_contain_text("shared pages may be counted twice")
    pw.expect(page.locator("#metric-tiles")).to_contain_text("unavailable")


@pytest.mark.parametrize("capacity", [32768, 131072])
def test_human_identity_and_distinct_context_evidence(ui: Any, capacity: int) -> None:
    page, current, _, _ = ui
    backend = current["engine"]["backends"]["splash"]
    for target in backend["targets"]:
        target["label"] = "Splash · " + target["cohort"] + f" · {capacity // 1024}K"
        target["max_context"] = capacity
    current["engine"]["splash"]["max_context"] = None
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#engine-current")).to_have_text("Splash · stock-8bit")
    selected = page.locator('#engine-target option[value="splash:stock-8bit-32k"]')
    assert selected.text_content().count(f"{capacity // 1024}K") == 1
    pw.expect(page.locator("#engine-target")).to_have_value("splash:stock-8bit-32k")
    pw.expect(page.locator("#engine-apply")).to_be_disabled()
    pw.expect(page.locator("#backend-cards")).to_contain_text("Configured context")
    pw.expect(page.locator("#backend-cards")).to_contain_text("Unverified")
    assert page.locator("#backend-cards details").count() > 0
    pw.expect(page.locator("#backend-cards")).not_to_contain_text("Usageunavailable")
    current["engine"]["splash"]["max_context"] = capacity // 2
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#backend-cards")).to_contain_text("Mismatch")
    current["engine"]["splash"]["max_context"] = capacity
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#backend-cards")).to_contain_text("Verified")


@pytest.mark.parametrize("state", ["starting", "released", "recovering", "unknown", "conflict"])
def test_chat_keyboard_obeys_readiness(ui: Any, state: str) -> None:
    page, current, _, _ = ui
    current.update(status(state))
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#btn-send")).to_be_disabled()
    page.locator("#prompt").fill("Do not submit when unavailable")
    page.locator("#prompt").press("Enter")
    page.wait_for_timeout(150)
    pw.expect(page.locator("#messages .msg")).to_have_count(0)
    pw.expect(page.locator("#prompt")).to_have_value("Do not submit when unavailable")


def test_chat_reservation_and_completion_recheck_current_status(ui: Any) -> None:
    page, current, _, mutations = ui
    current["quality_active"] = {"id": "test-run"}
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#btn-send")).to_be_disabled()
    pw.expect(page.locator("#chat-disabled")).to_contain_text("quality")
    current["quality_active"] = None
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#btn-send")).to_be_enabled()

    def refuse(r: Any) -> None:
        current.update(status("recovering"))
        page.evaluate("refreshStatus()")
        r.fulfill(status=409, json={"detail": "Engine recovering"})

    page.route("**/api/chat", refuse)
    page.locator("#prompt").fill("Synthetic request")
    page.locator("#prompt").press("Enter")
    pw.expect(page.locator("#messages")).to_contain_text("Engine recovering")
    pw.expect(page.locator("#btn-send")).to_be_disabled()
    assert not mutations


def test_status_renderer_failure_is_isolated_and_transport_failure_disables_actions(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#btn-send")).to_be_enabled()
    page.evaluate("onStatus(() => { throw new Error('synthetic renderer'); }); onStatus(() => { document.body.dataset.laterListener = 'updated'; }); refreshStatus()")
    pw.expect(page.locator("body")).to_have_attribute("data-later-listener", "updated")
    pw.expect(page.locator("#state-badge")).to_contain_text("Ready")
    page.route("**/api/status", lambda r: r.fulfill(status=503, json={"detail": "Synthetic outage"}))
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#state-badge")).to_contain_text("stale")
    pw.expect(page.locator("#btn-send")).to_be_disabled()
    pw.expect(page.locator("#status-freshness")).to_contain_text("Last success")


@pytest.mark.parametrize("busy", ["job", "quality_active", "unknown", "conflict"])
def test_models_unified_lifecycle_gating(ui: Any, busy: str) -> None:
    page, current, _, _ = ui
    if busy in ("job", "quality_active"):
        current[busy] = {"id": "test-job", "status": "running"}
    else:
        current.update(status(busy))
    # Avoid job polling: an active quality reservation exercises the same lifecycle policy.
    if busy == "job":
        page.route("**/api/jobs/test-job", lambda r: r.fulfill(json={"id": "test-job", "status": "running"}))
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#btn-start")).to_be_disabled()
    pw.expect(page.locator("#btn-stop")).to_be_disabled()
    page.locator("#engine-target").select_option("splash:modified-4bit-32k")
    pw.expect(page.locator("#engine-apply")).to_be_disabled()
    if busy == "conflict":
        pw.expect(page.locator('[data-keep="splash:stock-8bit-32k"]')).to_be_enabled()


@pytest.mark.parametrize("count", [200, 500])
@pytest.mark.parametrize("width", [390, 1440])
def test_dense_native_history_stays_collapsed_and_paged(ui: Any, count: int, width: int) -> None:
    from tests.test_splash_ingestion import reading
    page, current, _, _ = ui
    current["native_readings"] = [{**reading(), "observed": 100 + i} for i in range(count)]
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://admin.test/performance")
    page.locator("#perf-engine").select_option("splash")
    pw.expect(page.locator("#native-readings tbody tr")).to_have_count(20)
    diagnostics = page.locator("#engine-diagnostics")
    assert not diagnostics.evaluate("e => e.open")
    availability = page.locator("#availability-card")
    assert availability.bounding_box()["y"] < diagnostics.bounding_box()["y"]
    if width == 1440:
        assert availability.bounding_box()["y"] < 900
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    diagnostics.locator("summary").first.click()
    pw.expect(page.locator("#native-page-info")).to_contain_text("20")
    page.locator("#native-older").click()
    pw.expect(page.locator("#native-readings tbody tr")).to_have_count(20)
    pw.expect(page.locator("#native-newer")).to_be_enabled()
    page.locator("#native-newer").click()
    pw.expect(page.locator("#native-newer")).to_be_disabled()


def test_performance_resize_redraws_without_fetching(ui: Any) -> None:
    page, _, _, _ = ui
    fetched = []
    page.on("request", lambda req: fetched.append(req.url) if "/api/performance/" in req.url else None)
    page.goto("http://admin.test/performance")
    pw.expect(page.locator("#perf-source")).not_to_be_empty()
    before = len(fetched)
    page.set_viewport_size({"width": 700, "height": 900})
    page.wait_for_timeout(150)
    assert len(fetched) == before


def test_performance_discards_old_scope_responses_and_reports_partial_failure(ui: Any) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "none"
    held = []

    def summary_route(r: Any) -> None:
        source = parse_qs(urlsplit(r.request.url).query).get("source", [""])[0]
        payload = {"models": [], "days": [], "totals": {"requests": 7 if source == "strata" else 3}, "slow_ttft_s": 10}
        if source == "mlx-perf":
            held.append((r, payload))
        else:
            r.fulfill(json=payload)

    page.route("**/api/performance/summary?**", summary_route)
    page.goto("http://admin.test/performance")
    page.locator("#perf-engine").select_option("strata")
    pw.expect(page.locator("#perf-source")).to_contain_text("Strata request metrics")
    for r, payload in held:
        r.fulfill(json=payload)
    pw.expect(page.locator("#perf-source")).to_contain_text("Strata request metrics")
    pw.expect(page.locator("#perf-totals")).to_contain_text("7")
    page.route("**/api/performance/summary?**", lambda r: r.fulfill(status=503, json={"detail": "Synthetic failure"}))
    page.locator('[data-days="30"]').click()
    pw.expect(page.locator("#perf-summary-state")).to_contain_text("Stale")
    pw.expect(page.locator("#tokens-state")).to_contain_text("Updated")


def test_charts_keep_observation_gaps_and_offer_keyboard_data(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/")
    page.evaluate("""() => {
      const host = document.createElement('div'); host.id = 'synthetic-chart'; host.className = 'chart'; document.querySelector('main').append(host);
      lineChart(host, [{t: 1, v: 2}, {t: 2, v: 3}, {t: 3, v: null}, {t: 4, v: 0}, {t: 5, v: 4}]);
    }""")
    assert page.locator("#synthetic-chart path.line").count() == 2
    pw.expect(page.locator("#synthetic-chart-data")).to_contain_text("Not observed")
    page.evaluate("""() => {
      stackedBars(document.getElementById('synthetic-chart'), [{label: '2026-10-30', a: null, b: null, lines: ['Missing observation']}]);
    }""")
    point = page.locator("#synthetic-chart .bar-hit")
    point.focus()
    pw.expect(point).to_be_focused()
    pw.expect(page.locator("#synthetic-chart .tip")).to_be_visible()
    pw.expect(page.locator("#synthetic-chart .tip")).to_contain_text("Missing observation")


@pytest.mark.parametrize("surface,button,message", [("decision", "run", "decision-disabled"), ("tts", "tts-run", "tts-disabled"), ("embed", "embed-sim-run", "embed-disabled")])
@pytest.mark.parametrize("state", ["starting", "paused", "ready"])
def test_sidecar_test_pages_use_readiness_and_pause_policy(ui: Any, surface: str, button: str, message: str, state: str) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "mlx" if state != "paused" else "splash"
    current["service"] = {"running": state != "paused", "ready": state == "ready", "availability": {"available": state == "ready", "state": state, "owner": "splash" if state == "paused" else None, "reason": "Splash owns the GPU. Switch engines on Models." if state == "paused" else "Starting; waiting for readiness."}}
    page.goto("http://admin.test/" + surface)
    if surface == "tts":
        page.locator("#tts-text").fill("Synthetic speech")
    if state == "ready":
        pw.expect(page.locator("#" + button)).to_be_enabled()
    else:
        pw.expect(page.locator("#" + button)).to_be_disabled()
        pw.expect(page.locator("#" + message)).to_contain_text("Splash" if state == "paused" else "readiness")
        pw.expect(page.locator("#" + message + " a")).to_have_attribute("href", "/models")


def test_dashboard_uses_strata_measurements_and_keeps_job_near_controls(ui: Any) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "strata"
    current["engine"]["backends"]["strata"] = {"id": "strata", "label": "Strata", "enabled": True, "operations": ["profiles", "chat", "quality"], "running": True, "ready": True, "state": "ready", "reference": "IQ3_XXS", "targets": []}
    current["engine"]["strata"] = {"model": "example/model", "max_context": 65536, "metrics": {"tok_s": 42, "prompt_tok_s": 100, "queued": 2}}
    page.goto("http://admin.test/")
    pw.expect(page.locator("#st-decode")).to_have_text("42.0 tok/s")
    pw.expect(page.locator("#st-prefill")).to_have_text("100.0 tok/s")
    pw.expect(page.locator("#st-facts")).to_contain_text("Queued")
    pw.expect(page.locator("#st-facts")).not_to_contain_text("Tool parser")
    page.goto("http://admin.test/models")
    assert page.evaluate("document.getElementById('engine-card').nextElementSibling.id") == "job-card"
    assert page.locator("#inventory-card").evaluate("node => Boolean(node.closest('details'))")


def test_chat_displays_resolved_defaults_and_attributed_reply_after_engine_change(ui: Any) -> None:
    import json
    page, current, _, _ = ui
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#chat-defaults")).to_contain_text("8192")
    pw.expect(page.locator("#chat-capacity")).to_contain_text("128K")
    payload = [{"type": "identity", "identity": {"engine": "splash", "engine_label": "Splash", "target_label": "Splash · stock-8bit · 128K", "reference": "stock-8bit-32k", "profile_fingerprint": "fp-a"}}, {"type": "delta", "content": "Synthetic answer"}, {"type": "done", "identity": {"engine": "splash", "engine_label": "Splash", "target_label": "Splash · stock-8bit · 128K", "reference": "stock-8bit-32k", "profile_fingerprint": "fp-a"}, "settings": {"max_tokens": 8192}}]

    def respond(r: Any) -> None:
        current["engine"]["active"] = "mlx"
        current["engine"]["backends"]["mlx"].update(ready=True, state="ready")
        page.evaluate("refreshStatus()")
        r.fulfill(content_type="text/event-stream", body="".join("data: " + json.dumps(event) + "\n\n" for event in payload))

    page.route("**/api/chat", respond)
    page.locator("#prompt").fill("Synthetic question")
    page.locator("#btn-send").click()
    pw.expect(page.locator("#messages .assistant")).to_contain_text("Splash · stock-8bit")
    pw.expect(page.locator("#messages .assistant")).to_contain_text("Synthetic answer")
    pw.expect(page.locator("#chat-context-note")).to_contain_text("retained")
    assert page.locator("#messages .assistant .reply-identity").inner_text().count("128K") <= 1


def test_clear_during_generation_prevents_late_stream_history(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/chat")
    pw.expect(page.locator("#btn-send")).to_be_enabled()
    page.evaluate(r"""() => {
      const original = window.fetch;
      window.fetch = (url, opts) => url !== '/api/chat' ? original(url, opts) : Promise.resolve(new Response(new ReadableStream({start(controller) {
        window.syntheticStream = controller;
        controller.enqueue(new TextEncoder().encode('data: {"type":"delta","content":"Before clear"}\n\n'));
      }}), {headers: {'content-type': 'text/event-stream'}}));
    }""")
    page.locator("#prompt").fill("Synthetic question")
    page.locator("#btn-send").click()
    pw.expect(page.locator("#messages")).to_contain_text("Before clear")
    page.locator("#btn-clear").click()
    page.evaluate(r"""syntheticStream.enqueue(new TextEncoder().encode('data: {"type":"delta","content":"After clear"}\n\n')); syntheticStream.close();""")
    page.wait_for_timeout(100)
    pw.expect(page.locator("#messages")).to_be_empty()
    assert page.evaluate("history.length") == 0


def test_quality_opens_on_supported_kind_and_disables_invalid_scopes(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/quality")
    pw.expect(page.locator("#q-kind")).to_have_value("compatibility")
    pw.expect(page.locator('#q-kind option[value="benchmark"]')).to_have_attribute("disabled", "")
    pw.expect(page.locator('#q-scope option[value="all"]')).to_have_attribute("disabled", "")
    pw.expect(page.locator('#q-scope option[value="model"]')).to_have_attribute("disabled", "")
    pw.expect(page.locator('#q-scope option[value="current"]')).to_contain_text("Splash · stock-8bit")
    pw.expect(page.locator("#quality-start")).to_be_enabled()


def test_historical_result_labels_preserve_unknown_context_and_distinct_settings(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/quality")
    result = page.evaluate("""() => EngineUI.runLabel({original: {engine: 'splash', reference: 'stock-8bit-32k'}, settings: {enable_thinking: true}, runtime_version: '1.4.0', runtime_fingerprint: 'fp-a'})""")
    assert "Splash" in result and "stock-8bit" in result
    assert "context unrecorded" in result
    assert "128K" not in result
    assert "fp-a" in result


def test_alerts_poll_failure_is_visible_while_diagnostics_continue(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/alerts")
    pw.expect(page.locator("#alert-summary")).to_contain_text("0 open")
    page.route("**/api/alerts?**", lambda r: r.fulfill(status=503, json={"detail": "Synthetic outage"}))
    page.evaluate("refreshAll()")
    pw.expect(page.locator("#alert-list-freshness")).to_contain_text("Stale")
    pw.expect(page.locator("#diag-list-freshness")).to_contain_text("Updated")


def test_logs_default_to_active_engine_preserve_choice_and_show_follow_state(ui: Any) -> None:
    page, _, _, _ = ui
    page.add_init_script("""window.syntheticSources = []; window.EventSource = class {
      constructor(url) { this.url = url; window.syntheticSources.push(this); setTimeout(() => this.onopen?.(), 0); }
      close() { this.closed = true; }
    };""")
    page.goto("http://admin.test/logs")
    pw.expect(page.locator("#source")).to_have_value("splash")
    page.locator("#source").select_option("mlx-server")
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#source")).to_have_value("mlx-server")
    page.locator("#grep").fill("keep")
    page.locator("#level").select_option("ERROR")
    page.locator("#btn-follow").click()
    pw.expect(page.locator("#log-connection")).to_contain_text("Following")
    url = page.evaluate("syntheticSources.at(-1).url")
    query = parse_qs(urlsplit(url).query)
    assert query["level"] == ["ERROR"] and query["grep"] == ["keep"]
    page.evaluate("syntheticSources.at(-1).onerror()")
    pw.expect(page.locator("#log-connection")).to_contain_text("Reconnecting")
    page.locator("#btn-follow").click()
    pw.expect(page.locator("#log-connection")).to_contain_text("Paused")
    assert page.evaluate("syntheticSources.at(-1).closed")


@pytest.mark.parametrize("offset,day,label", [(-480, "2026-10-30", "UTC+08:00"), (300, "2026-10-29", "UTC−05:00")])
def test_usage_and_performance_use_server_calendar_days_in_a_different_browser_timezone(ui: Any, offset: int, day: str, label: str) -> None:
    page, current, _, _ = ui
    current["ops_offset"] = offset
    current["engine"]["active"] = "none"
    page.clock.install(time=datetime(2026, 10, 30, 0, 30, tzinfo=UTC))
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/usage/daily?" in r.url or "/api/performance/summary?" in r.url else None)
    page.goto("http://admin.test/usage")
    pw.expect(page.locator("#to")).to_have_value(day)
    pw.expect(page.locator("#usage-scope")).to_contain_text(label)
    page.goto("http://admin.test/performance")
    pw.expect(page.locator("#perf-scope")).to_contain_text(label)
    assert requests
    assert all(parse_qs(urlsplit(url).query)["to"] == [day] for url in requests)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 1440])
def test_shell_semantics_contrast_mobile_navigation_and_zoom(ui: Any, theme: str, width: int) -> None:
    page, current, _, _ = ui
    current["features"] = True
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://admin.test/chat")
    page.locator("#theme-select").select_option(theme)
    pw.expect(page.locator("main h1")).to_have_text("Chat")
    assert page.locator("#prompt").evaluate("e => e.labels.length") == 1
    page.locator(".skip-link").focus()
    page.keyboard.press("Enter")
    pw.expect(page.locator("main")).to_be_focused()
    if width == 390:
        assert not page.locator("#site-nav").evaluate("e => e.open")
        page.locator("#site-nav > summary").click()
        pw.expect(page.get_by_role("navigation", name="Primary")).to_be_visible()
        page.locator("#site-nav > summary").click()
        assert page.locator("#btn-send").bounding_box()["height"] >= 44
    ratios = page.evaluate(r"""() => {
      const rgb = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
      const lum = color => rgb(color).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
      const ratio = (a,b) => (Math.max(lum(a),lum(b)) + .05) / (Math.min(lum(a),lum(b)) + .05);
      const muted = getComputedStyle(document.querySelector('footer'));
      const primary = getComputedStyle(document.getElementById('btn-send'));
      return [ratio(muted.color, getComputedStyle(document.body).backgroundColor), ratio(primary.color, primary.backgroundColor)];
    }""")
    assert min(ratios) >= 4.5, ratios
    page.evaluate("document.body.style.zoom = '2'")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_usage_tabs_have_keyboard_navigation_and_linked_panels(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/usage")
    daily = page.get_by_role("tab", name="By date")
    sessions = page.get_by_role("tab", name="Sessions")
    daily.focus()
    page.keyboard.press("ArrowRight")
    pw.expect(sessions).to_be_focused()
    pw.expect(sessions).to_have_attribute("aria-selected", "true")
    panel_id = sessions.get_attribute("aria-controls")
    pw.expect(page.locator("#" + panel_id)).to_be_visible()
    pw.expect(page.locator("#" + panel_id)).to_have_attribute("aria-labelledby", sessions.get_attribute("id"))
    page.keyboard.press("Home")
    pw.expect(daily).to_be_focused()
    pw.expect(daily).to_have_attribute("aria-selected", "true")


def test_unloaded_context_and_history_do_not_imply_legacy_capacity(ui: Any) -> None:
    page, current, _, _ = ui
    current.update(status("released"))
    current["history_rows"] = [{"ts": 100, "action": "engine", "from_name": "mlx:qwen", "to_name": "splash:stock-8bit-32k", "result": "succeeded", "duration_s": 1.0, "source": "admin"}]
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#backend-cards")).to_contain_text("Unverified")
    assert "stock-8bit-32k" not in page.locator("#history-table").inner_text()
    pw.expect(page.locator("#history-table details").last).to_contain_text("splash:stock-8bit-32k")
    details = page.locator('#backend-cards > details')
    details.locator("summary").first.click()
    opened = details.evaluate("e => e.open")
    page.evaluate("refreshStatus()")
    assert details.evaluate("e => e.open") is opened


@pytest.mark.parametrize("engine", ["mlx", "strata", "splash", "comfyui", "none", "unknown", "conflict"])
@pytest.mark.parametrize("features", [False, True])
def test_backend_and_optional_service_matrix_at_mobile_width(ui: Any, engine: str, features: bool) -> None:
    page, current, _, _ = ui
    current["features"] = features
    current["engine"]["active"] = engine
    current["engine"]["backends"]["mlx"].update(ready=engine == "mlx", state="ready" if engine == "mlx" else "absent")
    current["engine"]["backends"]["comfyui"] = {"id": "comfyui", "label": "ComfyUI", "enabled": True, "running": engine == "comfyui", "ready": engine == "comfyui", "state": "ready" if engine == "comfyui" else "absent", "exclusive": True, "operations": ["queue", "logs"], "targets": [{"value": "comfyui", "label": "ComfyUI"}]}
    current["engine"]["comfyui"] = {"running": engine == "comfyui", "ready": engine == "comfyui"}
    current["engine"]["backends"]["strata"] = {"id": "strata", "label": "Strata", "enabled": True, "running": engine == "strata", "ready": engine == "strata", "state": "ready" if engine == "strata" else "absent", "exclusive": True, "operations": ["profiles", "chat", "quality", "logs"], "reference": "IQ3_XXS", "targets": [{"value": "strata:IQ3_XXS", "label": "Strata · IQ3_XXS", "max_context": 32768}]}
    current["engine"]["strata_enabled"] = True
    current["engine"]["strata"] = {"running": engine == "strata", "ready": engine == "strata", "pack": "IQ3_XXS", "model": "example/model", "max_context": 32768, "metrics": {}}
    page.set_viewport_size({"width": 390, "height": 900})
    for surface in ("models", "dashboard", "chat", "quality", "performance", "outputs"):
        page.goto("http://admin.test/" + surface)
        pw.expect(page.locator("#state-badge")).not_to_have_text("…")
        pw.expect(page.locator("main h1")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (surface, page.evaluate("[...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 15).map(e => [e.tagName, e.id, e.className, Math.round(e.getBoundingClientRect().width)])"))
        if surface == "chat":
            if engine in ("mlx", "strata", "splash"):
                pw.expect(page.locator("#btn-send")).to_be_enabled()
            else:
                pw.expect(page.locator("#btn-send")).to_be_disabled()
        if surface == "models" and engine == "comfyui":
            assert page.locator("#comfy-card").evaluate("node => node.open")
            pw.expect(page.locator("#queue-panel")).to_be_visible()


def test_long_target_names_stay_usable_and_missing_metadata_does_not_guess_a_target(ui: Any) -> None:
    page, current, _, _ = ui
    backend = current["engine"]["backends"]["splash"]
    backend["targets"][0]["label"] = "Splash · " + "LongTarget" * 30 + " · 128K"
    backend["targets"][0].pop("cohort")
    backend["targets"][0]["max_context"] = 131072
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#engine-current")).to_contain_text("LongTarget")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    backend["reference"] = None
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#engine-current")).to_contain_text("unverified")
    pw.expect(page.locator("#engine-target")).to_have_value("")


def test_failed_service_snapshot_keeps_lifecycle_controls_disabled(ui: Any) -> None:
    page, current, _, _ = ui
    current["features"] = True
    page.route("**/api/services", lambda r: r.fulfill(status=503, json={"detail": "Synthetic service outage"}))
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#resident-services-freshness")).to_contain_text("Synthetic service outage")
    assert page.locator("[data-resident-action]:enabled").count() == 0


def test_stored_quality_context_uses_the_selected_historical_target(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/quality")
    labels = page.evaluate("""() => {
      const original = {engine: 'splash', target_identities: {
        old: {engine: 'splash', target_label: 'Splash · stock-8bit', loaded_context: 32768, profile_fingerprint: 'old-fp'},
        updated: {engine: 'splash', target_label: 'Splash · stock-8bit', loaded_context: 131072, profile_fingerprint: 'new-fp'}
      }};
      return ['old', 'updated'].map(model => EngineUI.runLabel({scope: 'model', model, original}));
    }""")
    assert "32K" in labels[0] and "128K" not in labels[0] and "old-fp" in labels[0]
    assert "128K" in labels[1] and "new-fp" in labels[1]


@pytest.mark.parametrize("active", ["unknown", "conflict"])
def test_service_controls_fail_closed_when_ownership_becomes_uncertain(ui: Any, active: str) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "mlx"
    current["service"] = {"running": True, "ready": True}
    page.goto("http://admin.test/decision")
    pw.expect(page.locator("#run")).to_be_enabled()
    current["engine"]["active"] = active
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#run")).to_be_disabled()
    pw.expect(page.locator("#decision-disabled")).to_contain_text(active)


def test_service_refreshes_readiness_after_engine_transition(ui: Any) -> None:
    page, current, _, _ = ui
    current["service"] = {"running": False, "ready": False}
    page.goto("http://admin.test/decision")
    pw.expect(page.locator("#decision-disabled")).to_contain_text("Splash")
    current["engine"]["active"] = "mlx"
    current["service"] = {"running": True, "ready": True}
    page.evaluate("refreshStatus()")
    pw.expect(page.locator("#run")).to_be_enabled()


def test_older_requests_preserves_pending_summary_and_suppresses_duplicate_loads(ui: Any) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "none"
    summaries, older = [], []
    page.route("**/api/performance/summary?**", lambda r: summaries.append(r))
    def requests(r: Any) -> None:
        if "before=" in r.request.url:
            older.append(r)
        else:
            r.fulfill(json={"requests": [], "next": 10})
    page.route("**/api/performance/requests?**", requests)
    page.goto("http://admin.test/performance")
    page.locator("#more").click()
    pw.expect(page.locator("#more")).to_be_disabled()
    for r in summaries:
        r.fulfill(json={"models": [], "days": [], "totals": {"requests": 3}, "slow_ttft_s": 10})
    pw.expect(page.locator("#perf-summary-state")).to_contain_text("Updated")
    assert len(older) == 1
    older[0].fulfill(json={"requests": [], "next": None})
    pw.expect(page.locator("#more")).to_be_hidden()


@pytest.mark.parametrize("surface,table", [("decision", "hist-daily"), ("tts", "tts-daily"), ("embed", "embed-daily")])
def test_service_history_errors_are_visible_without_erasing_input(ui: Any, surface: str, table: str) -> None:
    page, _, _, _ = ui
    page.route("**/api/" + surface + "/history?**", lambda r: r.fulfill(status=503, json={"detail": "Synthetic history outage"}))
    page.goto("http://admin.test/" + surface)
    field = {"decision": "state", "tts": "tts-text", "embed": "embed-texts"}[surface]
    page.locator("#" + field).fill("Retained synthetic input")
    pw.expect(page.locator("#" + table + "-freshness")).to_contain_text("Synthetic history outage")
    pw.expect(page.locator("#" + field)).to_have_value("Retained synthetic input")


@pytest.mark.parametrize("surface,path,target", [("quality", "quality/latest", "q-params"), ("quality", "evals/suites", "eval-suites"), ("models", "downloads/runs", "dl-runs"), ("usage", "tts/usage", "tts-usage-table")])
def test_auxiliary_poll_failures_have_section_feedback(ui: Any, surface: str, path: str, target: str) -> None:
    page, current, _, _ = ui
    current["features"] = True
    page.route("**/api/" + path + "*", lambda r: r.fulfill(status=503, json={"detail": "Synthetic auxiliary outage"}))
    page.goto("http://admin.test/" + surface)
    pw.expect(page.locator("#" + target + "-freshness")).to_contain_text("Synthetic auxiliary outage")


def test_strata_card_keeps_configured_context_separate_from_unverified_loaded(ui: Any) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "strata"
    current["engine"]["strata_enabled"] = True
    current["engine"]["backends"]["strata"] = {"id": "strata", "label": "Strata", "enabled": True, "ready": False, "running": True, "state": "starting", "reference": "IQ3_XXS", "operations": ["profiles"], "targets": [{"value": "strata:IQ3_XXS", "label": "Strata · IQ3_XXS", "max_context": 65536}]}
    current["engine"]["strata"] = {"running": True, "ready": False, "model": "example/model", "pack": "IQ3_XXS", "max_context": 32768, "metrics": {}}
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#strata-facts")).to_contain_text("Configured context64K")
    pw.expect(page.locator("#strata-facts")).to_contain_text("Loaded contextUnverified")


def test_availability_strip_keeps_unobserved_portion_of_selected_range(ui: Any) -> None:
    page, _, _, _ = ui
    page.route("**/api/availability?**", lambda r: r.fulfill(json={"range_start": 100, "range_end": 1100, "segments": [{"start": 600, "end": 700, "state": "serving"}], "totals": {"coverage": 0.1, "availability": 1}, "days": []}))
    page.goto("http://admin.test/performance")
    segment = page.locator("#avail-strip span")
    pw.expect(segment).to_have_attribute("style", "left:50%;width:10%")


def test_chat_discards_defaults_resolved_before_engine_becomes_unavailable(ui: Any) -> None:
    page, current, _, _ = ui
    held = []
    page.route("**/api/chat/config", lambda r: held.append(r))
    page.goto("http://admin.test/chat")
    page.wait_for_function("configKey !== null")
    current["engine"]["active"] = "unknown"
    page.evaluate("refreshStatus()")
    for r in held:
        r.fulfill(json={"identity": {"engine": "splash", "engine_label": "Splash", "loaded_context": 131072}, "defaults": {"max_tokens": 8192}, "output_cap": 32768})
    pw.expect(page.locator("#chat-defaults")).not_to_contain_text("Splash defaults")
    pw.expect(page.locator("#chat-defaults")).to_contain_text("unavailable")
    pw.expect(page.locator("#chat-capacity")).not_to_contain_text("128K")


def test_empty_installation_keeps_pages_usable_and_does_not_propose_a_switch(ui: Any) -> None:
    page, current, errors, mutations = ui
    current["engine"]["active"] = "none"
    current["selected"] = None
    for backend in current["engine"]["backends"].values():
        backend.update(running=False, ready=False, reference=None, targets=[])
    page.route("**/api/models", lambda r: r.fulfill(json={"models": [], "discovered": []}))
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#engine-target")).to_have_value("off")
    pw.expect(page.locator("#engine-apply")).to_be_disabled()
    pw.expect(page.locator("#btn-start")).to_be_disabled()
    for surface in ("chat", "quality", "performance", "usage", "logs", "outputs"):
        page.goto("http://admin.test/" + surface)
        pw.expect(page.locator("main h1")).to_be_visible()
        if surface == "quality":
            pw.expect(page.locator("#quality-start")).to_be_disabled()
            pw.expect(page.locator("#eval-start")).to_be_disabled()
    assert not errors
    assert not mutations


def test_dashboard_does_not_verify_context_from_an_unready_mlx_snapshot(ui: Any) -> None:
    page, current, _, _ = ui
    current["engine"]["active"] = "mlx"
    current["engine"]["backends"]["mlx"].update(running=True, ready=False, state="starting")
    current["health"] = {"loaded_context_size": 32768}
    page.goto("http://admin.test/")
    pw.expect(page.locator("#st-facts")).to_contain_text("Loaded contextUnknown · Unverified")


@pytest.mark.parametrize("service,facts", [("decision", "sidecar-facts"), ("embed", "service-qwen-embed-facts")])
def test_models_does_not_call_failed_service_history_zero_traffic(ui: Any, service: str, facts: str) -> None:
    page, current, _, _ = ui
    current["features"] = True
    path = "**/api/services/qwen-embed/history" if service == "embed" else "**/api/" + service + "/history?**"
    page.route(path, lambda r: r.fulfill(status=503, json={"detail": "Synthetic daily traffic outage"}))
    page.goto("http://admin.test/models")
    pw.expect(page.locator("#" + facts)).to_contain_text("Unavailable: Synthetic daily traffic outage")


def test_performance_section_timestamps_use_declared_operations_timezone(ui: Any) -> None:
    page, _, _, _ = ui
    page.goto("http://admin.test/performance")
    page.locator("#perf-engine").select_option("mlx")
    pw.expect(page.locator("#perf-summary-state")).to_contain_text("UTC+08:00")


def test_resident_cards_render_third_registration_and_safe_labels(ui: Any) -> None:
    page, current, errors, _ = ui
    current["features"] = True
    current["resident_services"] = [
        {"name": name, "title": title, "enabled": True, "running": False, "ready": False,
         "intent": "running", "models": [name + "-model"], "allowed_mlx": [],
         "log_sources": [name + "-log"],
         "ui_path": "/" + name, "availability": {"reason": "Suspended by Splash", "owner": "splash"},
         "budget": {"ok": False, "lines": ["Synthetic reservation"], "reasons": ["Exclusive engine active"]}}
        for name, title in [("qwen-embed", "Embeddings"), ("paddleocr-vl", "OCR"),
                            ("third", "<img src=x onerror=alert(1)>")]
    ]
    page.goto("http://admin.test/models")
    pw.expect(page.locator("[data-resident-service]")).to_have_count(3)
    pw.expect(page.locator("#service-third-facts")).to_contain_text("third-model")
    pw.expect(page.locator("#service-third-start")).to_be_disabled()
    pw.expect(page.locator("#service-third-logs a")).to_have_attribute("href", "/logs?source=third-log")
    assert page.locator("#resident-services img").count() == 0
    assert errors == []


def test_ocr_upload_escapes_results_and_downloads_each_format(ui: Any) -> None:
    from tests.ocr_fixtures import png, result
    page, current, errors, _ = ui
    current["features"] = True
    body = result()
    body["pages"][0]["text"] = body["pages"][0]["markdown"] = "<script>window.ocrInjected=true</script>"
    body["text"] = body["markdown"] = body["pages"][0]["text"]
    page.route("**/api/ocr", lambda r: r.fulfill(json={
        "model": "local-ocr", "reason": None, "admission": {"active": False, "queued": 0},
        "limits": {"max_bytes": 10485760, "max_pages": 16}}))
    page.route("**/api/ocr/recognize", lambda r: r.fulfill(json=body))
    page.goto("http://admin.test/ocr")
    page.locator("#ocr-document").set_input_files({"name": "synthetic.png", "mimeType": "image/png", "buffer": png()})
    page.locator("#ocr-run").click()
    pw.expect(page.locator("#ocr-results")).to_contain_text("<script>window.ocrInjected=true</script>")
    assert page.evaluate("window.ocrInjected") is None
    for mode in ("text", "markdown", "json"):
        with page.expect_download() as download:
            page.locator("#ocr-download-" + mode).click()
        assert download.value.suggested_filename.startswith("ocr-result.")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []
