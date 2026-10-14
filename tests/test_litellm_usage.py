import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from local_llm_admin import litellm_usage as lu
from local_llm_admin.config import Settings

FIX = Path(__file__).parent / "fixtures"
BASE = "http://litellm.test"
PREFIX = "openai//Volumes/Models/models/"
KEY = "sk-viewer-SECRET-123"


def _fx(name):
    return json.loads((FIX / name).read_text())


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def usage(clock):
    s = Settings(litellm_base_url=BASE, litellm_admin_key=KEY)
    return lu.LiteLLMUsage(s, clock=clock)


def _mock_recorded():
    return respx.get(f"{BASE}/spend/logs/v2").mock(
        return_value=httpx.Response(200, json=_fx("spend_logs_recorded.json"))
    )


# --- contract (recorded 2026-09-29, LiteLLM v1.99.1) --------------------------


def test_contract_recorded_spend_logs_local_totals():
    rows = _fx("spend_logs_recorded.json")["data"]
    local = [r for r in rows if lu.is_local(r.get("model"), [PREFIX])]
    assert len(local) == 14
    assert sum(r["prompt_tokens"] for r in local) == 15845
    assert sum(r["completion_tokens"] for r in local) == 283


def test_default_local_models_is_the_models_prefix():
    assert Settings().local_model_strings == [PREFIX]


@pytest.mark.parametrize(
    ("model", "local"),
    [
        ("openai//Volumes/Models/models/active", True),
        ("openai//Volumes/Models/models/Qwen3.8-27B-4bit", True),
        ("/Volumes/Models/models/active", True),  # provider prefix stripped
        ("grok-4.7", False),
        (None, False),
        ("openai//Volumes/Models/modelsX/y", False),
    ],
)
def test_is_local_prefix_and_exact(model, local):
    assert lu.is_local(model, [PREFIX, "openai/exact-model"]) is local
    assert lu.is_local("openai/exact-model", [PREFIX, "openai/exact-model"]) is True
    assert lu.is_local("openai/exact-model-2", [PREFIX, "openai/exact-model"]) is False


# --- validation -------------------------------------------------------------


def test_validate_range_defaults_to_last_30_days():
    assert lu.validate_range(None, None, today=date(2026, 9, 28)) == (
        date(2026, 8, 30),
        date(2026, 9, 28),
    )


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        ("2026-13-01", "2026-09-28"),
        ("2026-09-28", "2026-09-01"),
        ("2026-01-01", "2026-09-28"),
        ("x' OR 1=1", None),
        ("2026-09-28T00:00", None),
    ],
)
def test_validate_range_rejects(frm, to):
    with pytest.raises(ValueError):
        lu.validate_range(frm, to, today=date(2026, 9, 28))


def test_validate_range_allows_exactly_90_days():
    assert lu.validate_range("2026-07-01", "2026-09-28", today=date(2026, 9, 28))[0] == date(
        2026, 7, 1
    )
    with pytest.raises(ValueError):
        lu.validate_range("2026-06-30", "2026-09-28", today=date(2026, 9, 28))


def test_utc_bounds_for_local_dates():
    assert lu.utc_bounds(date(2026, 9, 27), date(2026, 9, 28), -480) == (
        "2026-09-26 16:00:00",
        "2026-09-28 15:59:59",
    )


# --- daily -------------------------------------------------------------------


@respx.mock
async def test_daily_buckets_recorded_rows_by_ops_day(usage):
    route = _mock_recorded()
    out = await usage.daily(date(2026, 9, 28), date(2026, 9, 29))
    assert route.call_count == 1
    p = dict(route.calls[0].request.url.params)
    assert "model" not in p  # a model filter is refused (403) for a key without model access
    assert (p["start_date"], p["end_date"]) == ("2026-09-27 16:00:00", "2026-09-29 15:59:59")
    assert p["page_size"] == str(lu.SPEND_LOGS_PAGE_SIZE)
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    d28, d29 = out["days"]
    assert (d28["date"], d28["api_requests"]) == ("2026-09-28", 13)
    # 2026-09-28T22:34 UTC is 06:34 on the 29th in Hong Kong
    assert (d29["date"], d29["api_requests"], d29["prompt_tokens"], d29["completion_tokens"]) == (
        "2026-09-29",
        1,
        2185,
        10,
    )
    assert out["totals"]["api_requests"] == 14
    assert out["totals"]["prompt_tokens"] == 15845
    assert out["totals"]["completion_tokens"] == 283
    assert out["totals"]["failed_requests"] == 0
    assert {a["app"] for a in d28["apps"]} == {"user-a-manual", "(no alias)"}
    assert d28["models"]["openai//Volumes/Models/models/Qwen3.8-27B-4bit"]["api_requests"] == 2
    assert set(d28["model_groups"]) == {"local-qwen", "qwen3.8-27b"}
    assert out["truncated"] is False


@respx.mock
async def test_daily_fills_empty_days_and_counts_failures(usage):
    rows = [
        {
            "startTime": "2026-09-27T02:00:00+00:00",
            "model": "openai//Volumes/Models/models/active",
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "total_tokens": 11,
            "status": "failure",
            "metadata": {"user_api_key_alias": "app-a"},
        },
        {
            "startTime": "2026-09-27T03:00:00+00:00",
            "model": "grok-4.7",
            "prompt_tokens": 99,
            "completion_tokens": 9,
            "total_tokens": 108,
            "status": "success",
            "metadata": {},
        },
    ]
    respx.get(f"{BASE}/spend/logs/v2").mock(
        return_value=httpx.Response(
            200, json={"data": rows, "total": 2, "page": 1, "page_size": 1000, "total_pages": 1}
        )
    )
    out = await usage.daily(date(2026, 9, 26), date(2026, 9, 28))
    assert [d["date"] for d in out["days"]] == ["2026-09-26", "2026-09-27", "2026-09-28"]
    d27 = out["days"][1]
    assert (d27["api_requests"], d27["failed_requests"], d27["successful_requests"]) == (1, 1, 0)
    assert d27["apps"] == [
        {
            "app": "app-a",
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "total_tokens": 11,
            "api_requests": 1,
            "successful_requests": 0,
            "failed_requests": 1,
        }
    ]
    assert out["days"][0]["api_requests"] == 0 and out["days"][0]["apps"] == []


@respx.mock
async def test_daily_and_sessions_share_one_cached_fetch(usage, clock):
    route = _mock_recorded()
    await usage.daily(date(2026, 9, 28), date(2026, 9, 29))
    await usage.sessions(date(2026, 9, 28), date(2026, 9, 29))
    await usage.daily(date(2026, 9, 28), date(2026, 9, 29))
    assert route.call_count == 1
    await usage.daily(date(2026, 9, 27), date(2026, 9, 29))
    assert route.call_count == 2  # different range
    clock.t += 61
    await usage.daily(date(2026, 9, 28), date(2026, 9, 29))
    assert route.call_count == 3


@pytest.mark.parametrize(
    ("resp", "kind"),
    [
        (httpx.Response(401, json={"error": "bad key"}), "auth"),
        (httpx.Response(403), "auth"),
        (httpx.Response(500, text="boom"), "upstream"),
        (httpx.Response(200, text="not json"), "upstream"),
        (httpx.Response(200, json={"results": "wrong"}), "upstream"),
    ],
)
@respx.mock
async def test_daily_errors_are_generic_and_never_leak_key(usage, resp, kind):
    respx.get(f"{BASE}/spend/logs/v2").mock(return_value=resp)
    with pytest.raises(lu.UsageError) as ei:
        await usage.daily(date(2026, 9, 26), date(2026, 9, 28))
    assert ei.value.kind == kind
    assert KEY not in str(ei.value)


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
@respx.mock
async def test_daily_unreachable(usage, exc):
    respx.get(f"{BASE}/spend/logs/v2").mock(side_effect=exc)
    with pytest.raises(lu.UsageError) as ei:
        await usage.daily(date(2026, 9, 26), date(2026, 9, 28))
    assert ei.value.kind == "unreachable"


async def test_missing_key_is_config_error(clock):
    u = lu.LiteLLMUsage(Settings(litellm_base_url=BASE, litellm_admin_key=""), clock=clock)
    with pytest.raises(lu.UsageError) as ei:
        await u.daily(date(2026, 9, 26), date(2026, 9, 28))
    assert ei.value.kind == "config"


# --- sessions ---------------------------------------------------------------


def _mock_spend_logs(pages=None):
    def handler(request):
        page = int(request.url.params["page"])
        if pages is not None:
            return httpx.Response(200, json=pages(page))
        return httpx.Response(200, json=_fx(f"spend_logs_p{page}.json"))

    return respx.get(f"{BASE}/spend/logs/v2").mock(side_effect=handler)


@respx.mock
async def test_sessions_pages_filters_and_groups(usage):
    route = _mock_spend_logs()
    out = await usage.sessions(date(2026, 9, 27), date(2026, 9, 27), page=1)
    assert route.call_count == 2
    p = dict(route.calls[0].request.url.params)
    assert (p["start_date"], p["end_date"]) == ("2026-09-26 16:00:00", "2026-09-27 15:59:59")
    rows = {(s["app"], s["session_id"]): s for s in out["sessions"]}
    assert set(rows) == {("app-d-key", "s-1"), ("u2", "s-2"), ("app-e-agent", "(no session)")}
    s1 = rows[("app-d-key", "s-1")]
    assert (s1["requests"], s1["prompt_tokens"], s1["completion_tokens"]) == (2, 2500, 50)
    assert (s1["first_ts"], s1["last_ts"]) == ("2026-09-27T02:00:00", "2026-09-27T02:05:00")
    assert s1["success"] == 2 and s1["failure"] == 0
    assert rows[("u2", "s-2")]["failure"] == 1
    assert out["sessions"][0]["app"] == "app-e-agent"  # newest first
    assert out["total_sessions"] == 3 and out["truncated"] is False


@respx.mock
async def test_sessions_page_cap(usage):
    def page(n):
        return {"data": [], "total": 99999, "page": n, "page_size": 1000, "total_pages": 1000}

    route = _mock_spend_logs(page)
    out = await usage.sessions(date(2026, 9, 27), date(2026, 9, 27), page=1)
    assert route.call_count == lu.MAX_PAGES
    assert out["truncated"] is True


@respx.mock
async def test_capped_total_keeps_paging_while_pages_are_full(usage):
    row = {
        "startTime": "2026-09-27T02:00:00+00:00",
        "model": "openai//Volumes/Models/models/active",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
        "metadata": {},
    }

    def page(n):
        size = lu.SPEND_LOGS_PAGE_SIZE if n < 3 else 5
        return {
            "data": [row] * size,
            "total": lu.SPEND_LOGS_PAGE_SIZE,
            "page": n,
            "page_size": lu.SPEND_LOGS_PAGE_SIZE,
            "total_pages": 1,
            "total_is_capped": True,
        }

    route = _mock_spend_logs(page)
    out = await usage.daily(date(2026, 9, 27), date(2026, 9, 27))
    assert route.call_count == 3
    assert out["totals"]["api_requests"] == 2 * lu.SPEND_LOGS_PAGE_SIZE + 5


@respx.mock
async def test_sessions_admin_paging(usage):
    def page(n):
        rows = [
            {
                "request_id": f"r{i}",
                "startTime": f"2026-09-27T00:{i:02d}:00",
                "model": "/Volumes/Models/models/active",
                "session_id": f"s{i}",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "user": "u",
                "metadata": {},
                "status": None,
            }
            for i in range(60)
        ]
        return {"data": rows, "total": 60, "page": 1, "page_size": 100, "total_pages": 1}

    _mock_spend_logs(page)
    out = await usage.sessions(date(2026, 9, 27), date(2026, 9, 27), page=2, page_size=50)
    assert len(out["sessions"]) == 10 and out["total_pages"] == 2 and out["page"] == 2
    assert out["sessions"][0]["success"] == 1  # null status counts as success


def test_group_sessions_tolerates_odd_rows():
    rows = [{"model": "/Volumes/Models/models/active", "metadata": "not-a-dict", "startTime": None}]
    (s,) = lu.group_sessions(rows, [PREFIX])
    assert s["app"] == "(no alias)" and s["requests"] == 1 and s["prompt_tokens"] == 0


def test_group_sessions_recorded():
    groups = lu.group_sessions(_fx("spend_logs_recorded.json")["data"], [PREFIX])
    assert sum(g["requests"] for g in groups) == 14
    assert all(g["session_id"].startswith("session-") for g in groups)


# --- health ------------------------------------------------------------------


@respx.mock
async def test_health_ok(usage):
    respx.get(f"{BASE}/health/liveliness").mock(return_value=httpx.Response(200, json="I'm alive!"))
    route = _mock_recorded()
    assert await usage.health() == {"reachable": True, "key_ok": True, "error": None}
    assert "model" not in dict(route.calls[0].request.url.params)


@respx.mock
async def test_health_bad_key(usage):
    respx.get(f"{BASE}/health/liveliness").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/spend/logs/v2").mock(return_value=httpx.Response(401))
    h = await usage.health()
    assert h["reachable"] is True and h["key_ok"] is False and KEY not in h["error"]


@respx.mock
async def test_health_unreachable(usage):
    respx.get(f"{BASE}/health/liveliness").mock(side_effect=httpx.ConnectError("x"))
    assert (await usage.health())["reachable"] is False


@respx.mock
async def test_sessions_bad_shape(usage):
    respx.get(f"{BASE}/spend/logs/v2").mock(return_value=httpx.Response(200, json={"rows": []}))
    with pytest.raises(lu.UsageError) as ei:
        await usage.sessions(date(2026, 9, 27), date(2026, 9, 27))
    assert ei.value.kind == "upstream"


@respx.mock
async def test_sessions_cached_across_pages(usage):
    route = _mock_spend_logs()
    await usage.sessions(date(2026, 9, 27), date(2026, 9, 27), page=1)
    await usage.sessions(date(2026, 9, 27), date(2026, 9, 27), page=2)
    assert route.call_count == 2  # the two upstream pages, fetched once


@respx.mock
async def test_health_without_local_models_is_config_error(clock):
    u = lu.LiteLLMUsage(
        Settings(litellm_base_url=BASE, litellm_admin_key=KEY, litellm_local_models=" , "),
        clock=clock,
    )
    respx.get(f"{BASE}/health/liveliness").mock(return_value=httpx.Response(200))
    assert await u.health() == {
        "reachable": True,
        "key_ok": False,
        "error": "LITELLM_LOCAL_MODELS is empty",
    }
    with pytest.raises(lu.UsageError) as ei:
        await u.daily(date(2026, 9, 27), date(2026, 9, 27))
    assert ei.value.kind == "config"


@pytest.mark.parametrize(
    ("ts", "day"),
    [
        ("2026-09-28T22:34:22.000+00:00", "2026-09-29"),
        ("2026-09-28T15:59:59", "2026-09-28"),  # naive timestamps are UTC
        ("2026-09-28T16:00:00Z", "2026-09-29"),
        ("garbage", None),
        (None, None),
    ],
)
def test_local_date(ts, day):
    assert lu._local_date(ts, -480) == day


def test_is_local_ignores_empty_variants():
    assert lu.is_local("anything", ["x/"]) is False  # "x/" splits to an empty prefix
    assert lu.is_local("", [PREFIX]) is False


def test_daily_from_rows_ignores_rows_outside_range():
    rows = [
        {"startTime": "2026-10-05T00:00:00+00:00", "model": "openai//Volumes/Models/models/a"},
        {"startTime": "bad", "model": "openai//Volumes/Models/models/a"},
    ]
    days = lu.daily_from_rows(rows, date(2026, 9, 28), date(2026, 9, 28), -480)
    assert days[0]["api_requests"] == 0


def test_group_sessions_skips_non_local():
    rows = [
        {"model": "grok-4.7", "session_id": "s"},
        {"model": "openai//Volumes/Models/models/a", "session_id": "s"},
    ]
    (g,) = lu.group_sessions(rows, [PREFIX])
    assert g["requests"] == 1
