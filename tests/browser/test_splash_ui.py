"""Optional real DOM checks: uv run --with playwright pytest tests/browser -q.

All navigation and APIs are fulfilled locally; no runtime or live service is contacted.
Install the isolated browser first with `uv run --with playwright playwright install chromium`.
"""

from __future__ import annotations

from collections.abc import Iterator
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
        "note": "Reasoning on; usage unavailable. GPU sidecars stay stopped.",
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
    context = browser.new_context()
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
                decision_enabled=False,
                tts_enabled=False,
                embed_enabled=False,
            )
            r.fulfill(content_type="text/html", body=html)
            return
        if r.request.method != "GET":
            mutations.append((path, r.request.post_data_json))
        data = {
            "/api/status": current,
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
            "/api/history": {"history": []},
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
            "/api/logs": {"sources": [{"id": "splash", "exists": True}]},
            "/api/logs/splash": {"exists": True, "size": 10, "lines": ["Synthetic Splash log"]},
        }.get(path)
        if path == "/api/performance/engines" and data is not None:
            profile = parse_qs(urlsplit(r.request.url).query).get("profile", [None])[0]
            if profile:
                data["readings"] = [row for row in data["readings"] if row["profile"] == profile]
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
    pw.expect(selector).to_contain_text("stock-8bit-32k · 32K context")
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
    page.locator("#engine-apply").click()
    pw.expect(page.locator("#confirm")).to_be_visible()
    pw.expect(page.locator("#confirm-title")).to_contain_text("modified-4bit-32k")
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
        pw.expect(page.locator("#quality-start")).to_be_disabled()  # benchmark
    page.locator("#q-kind").select_option("compatibility")
    if state == "ready":
        pw.expect(page.locator("#quality-start")).to_be_enabled()
        pw.expect(page.locator("#eval-start")).to_be_enabled()
        pw.expect(page.locator("#eval-thinking")).to_be_checked()
        page.locator("#q-scope").select_option("all")
        pw.expect(page.locator("#quality-start")).to_be_disabled()
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
    pw.expect(page.locator("#perf-source")).to_contain_text("MLX log source:")
    gap = page.locator("#splash-coverage")
    if enabled:
        pw.expect(gap).to_be_visible()
        pw.expect(gap).to_contain_text("Splash usage is not collected")
        pw.expect(gap).to_contain_text("Complete request history is unsupported")
    else:
        pw.expect(gap).to_be_hidden()
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
    page.locator("#native-profile").select_option("stock")
    pw.expect(page.locator("#native-readings tbody tr")).to_have_count(1)
    pw.expect(page.locator("#native-readings tbody")).to_contain_text("stock · fp-a")
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
