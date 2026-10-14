"""Local-model token usage by date, read from the LiteLLM gateway API (read-only key).

Phase 0 (2026-09-29) findings against LiteLLM v1.99.1 shape this module:

* The daily rollups behind ``/user/daily/activity*`` are UTC days; ``timezone`` does not
  re-bucket them. Days are therefore built here from ``/spend/logs/v2`` rows, bucketed in
  the ops timezone.
* A ``model`` query filter is checked against the key's own model access, so the
  viewer key (no model access) gets 403. Rows are fetched unfiltered and matched to the
  local model strings here.

The key is sent only as a Bearer header to ``LITELLM_BASE_URL``. Errors raised from here
carry fixed messages, never response bodies, headers, or the key.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .config import Settings
from .jobs import ops_tz

MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 30
MAX_PAGES = 20
SPEND_LOGS_PAGE_SIZE = 1000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_METRIC_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "api_requests",
    "successful_requests",
    "failed_requests",
)
NO_ALIAS = "(no alias)"
NO_SESSION = "(no session)"
_UTC = ops_tz(0)


class UsageError(Exception):
    """kind: config | auth | unreachable | upstream."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _zero() -> dict[str, int]:
    return dict.fromkeys(_METRIC_FIELDS, 0)


def is_local(model: Any, local_models: list[str]) -> bool:
    """Match a row's model against configured strings; entries ending in "/" are prefixes.

    Rows may carry the model with or without the ``openai/`` provider prefix.
    """
    if not isinstance(model, str) or not model:
        return False
    for entry in local_models:
        variants = [entry] + ([entry.split("/", 1)[1]] if "/" in entry else [])
        for v in variants:
            if not v:
                continue
            if entry.endswith("/") and model.startswith(v):
                return True
            if not entry.endswith("/") and model == v:
                return True
    return False


def validate_range(start: str | None, end: str | None, today: date) -> tuple[date, date]:
    """ISO dates only, start <= end, at most 90 days. Defaults to the last 30 days."""

    def parse(s: str) -> date:
        if not _DATE_RE.fullmatch(s):
            raise ValueError("dates must be YYYY-MM-DD")
        return date.fromisoformat(s)

    to = parse(end) if end else today
    frm = parse(start) if start else to - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    if frm > to:
        raise ValueError("from must not be after to")
    if (to - frm).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"range is limited to {MAX_RANGE_DAYS} days")
    return frm, to


def utc_bounds(start: date, end: date, tz_offset_min: int) -> tuple[str, str]:
    """Local [start 00:00, end 23:59:59] as UTC strings for ``/spend/logs/v2``."""
    tz = ops_tz(tz_offset_min)
    lo = datetime.combine(start, datetime.min.time(), tz)
    hi = datetime.combine(end, datetime.max.time().replace(microsecond=0), tz)
    fmt = "%Y-%m-%d %H:%M:%S"
    return lo.astimezone(_UTC).strftime(fmt), hi.astimezone(_UTC).strftime(fmt)


def _local_date(ts: Any, tz_offset_min: int) -> str | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)  # LiteLLM stores startTime in UTC
    return dt.astimezone(ops_tz(tz_offset_min)).date().isoformat()


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    md = row.get("metadata")
    return md if isinstance(md, dict) else {}


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or _metadata(row).get("status") or "success")


def _row_metrics(row: dict[str, Any]) -> dict[str, int]:
    ok = _status(row) == "success"
    return {
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "api_requests": 1,
        "successful_requests": int(ok),
        "failed_requests": int(not ok),
    }


def _add(acc: dict[str, int], m: dict[str, int]) -> None:
    for f in _METRIC_FIELDS:
        acc[f] += m[f]


def _empty_day(d: str) -> dict[str, Any]:
    return {"date": d, **_zero(), "apps": [], "model_groups": {}, "models": {}}


def daily_from_rows(
    rows: list[dict[str, Any]], start: date, end: date, tz_offset_min: int
) -> list[dict[str, Any]]:
    """One entry per local date in [start, end] (empty days included)."""
    days: dict[str, dict[str, Any]] = {}
    apps: dict[str, dict[str, dict[str, int]]] = {}
    cur = start
    while cur <= end:
        days[cur.isoformat()] = _empty_day(cur.isoformat())
        apps[cur.isoformat()] = {}
        cur += timedelta(days=1)
    for row in rows:
        d = _local_date(row.get("startTime"), tz_offset_min)
        if d not in days:
            continue
        m = _row_metrics(row)
        day = days[d]
        _add(day, m)
        app = _metadata(row).get("user_api_key_alias") or NO_ALIAS
        _add(apps[d].setdefault(app, _zero()), m)
        for field, key in (("model_groups", row.get("model_group")), ("models", row.get("model"))):
            _add(day[field].setdefault(str(key or "(none)"), _zero()), m)
    for d, day in days.items():
        day["apps"] = [
            {"app": a, **v}
            for a, v in sorted(apps[d].items(), key=lambda kv: (-kv[1]["total_tokens"], kv[0]))
        ]
    return list(days.values())


def group_sessions(rows: list[dict[str, Any]], local_models: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not is_local(row.get("model"), local_models):
            continue
        md = _metadata(row)
        app = md.get("user_api_key_alias") or row.get("user") or NO_ALIAS
        sid = row.get("session_id") or NO_SESSION
        g = groups.setdefault(
            (app, sid),
            {
                "app": app,
                "session_id": sid,
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "first_ts": None,
                "last_ts": None,
                "success": 0,
                "failure": 0,
            },
        )
        m = _row_metrics(row)
        g["requests"] += 1
        for f in ("prompt_tokens", "completion_tokens", "total_tokens"):
            g[f] += m[f]
        g["success"] += m["successful_requests"]
        g["failure"] += m["failed_requests"]
        ts = row.get("startTime")
        if isinstance(ts, str):
            g["first_ts"] = min(filter(None, (g["first_ts"], ts)))
            g["last_ts"] = max(filter(None, (g["last_ts"], ts)))
    return sorted(groups.values(), key=lambda g: g["last_ts"] or "", reverse=True)


class LiteLLMUsage:
    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._transport = transport
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

    def today(self) -> date:
        return datetime.now(ops_tz(self.settings.litellm_timezone_offset_min)).date()

    def _client(self) -> httpx.AsyncClient:
        key = self.settings.litellm_admin_key.get_secret_value()
        if not key:
            raise UsageError("config", "LITELLM_ADMIN_KEY is not configured")
        if not self.settings.local_model_strings:
            raise UsageError("config", "LITELLM_LOCAL_MODELS is empty")
        return httpx.AsyncClient(
            base_url=self.settings.litellm_base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=self.settings.litellm_timeout_s,
            transport=self._transport,
        )

    @staticmethod
    async def _get(http: httpx.AsyncClient, path: str, params: dict[str, Any]) -> Any:
        try:
            resp = await http.get(path, params=params)
        except httpx.HTTPError:
            raise UsageError("unreachable", "LiteLLM gateway is unreachable") from None
        if resp.status_code in (401, 403):
            raise UsageError("auth", "LiteLLM rejected the admin key")
        if resp.status_code != 200:
            raise UsageError("upstream", f"LiteLLM returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise UsageError("upstream", "LiteLLM returned invalid JSON") from None

    async def _spend_page(
        self, http: httpx.AsyncClient, lo: str, hi: str, page: int, page_size: int
    ) -> dict[str, Any]:
        body = await self._get(
            http,
            "/spend/logs/v2",
            {"start_date": lo, "end_date": hi, "page": page, "page_size": page_size},
        )
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise UsageError("upstream", "unexpected LiteLLM response shape")
        return body

    async def local_rows(self, start: date, end: date) -> tuple[list[dict[str, Any]], bool]:
        """Local-model spend-log rows for local dates [start, end]; cached per range."""
        key = ("rows", start, end)
        hit = self._cache.get(key)
        if hit and hit[0] > self._clock():
            rows_cached: tuple[list[dict[str, Any]], bool] = hit[1]
            return rows_cached
        lo, hi = utc_bounds(start, end, self.settings.litellm_timezone_offset_min)
        local = self.settings.local_model_strings
        rows: list[dict[str, Any]] = []
        truncated = True
        async with self._client() as http:
            for page in range(1, MAX_PAGES + 1):
                body = await self._spend_page(http, lo, hi, page, SPEND_LOGS_PAGE_SIZE)
                data = [r for r in body["data"] if isinstance(r, dict)]
                rows.extend(r for r in data if is_local(r.get("model"), local))
                total_pages = int(body.get("total_pages") or 1)
                # a capped total understates total_pages: keep going while pages are full
                capped_more = (
                    bool(body.get("total_is_capped")) and len(data) >= SPEND_LOGS_PAGE_SIZE
                )
                if page >= total_pages and not capped_more:
                    truncated = False
                    break
        self._cache[key] = (self._clock() + self.settings.litellm_cache_ttl_s, (rows, truncated))
        return rows, truncated

    async def daily(self, start: date, end: date) -> dict[str, Any]:
        rows, truncated = await self.local_rows(start, end)
        tz = self.settings.litellm_timezone_offset_min
        days = daily_from_rows(rows, start, end, tz)
        totals = _zero()
        for d in days:
            for f in _METRIC_FIELDS:
                totals[f] += d[f]
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "timezone_offset_min": tz,
            "models_queried": self.settings.local_model_strings,
            "days": days,
            "totals": totals,
            "truncated": truncated,
        }

    async def sessions(
        self, start: date, end: date, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        rows, truncated = await self.local_rows(start, end)
        groups = group_sessions(rows, self.settings.local_model_strings)
        total_pages = max(1, -(-len(groups) // page_size))
        page = min(max(1, page), total_pages)
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "sessions": groups[(page - 1) * page_size : page * page_size],
            "page": page,
            "total_pages": total_pages,
            "total_sessions": len(groups),
            "truncated": truncated,
        }

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.litellm_base_url,
                timeout=self.settings.litellm_timeout_s,
                transport=self._transport,
            ) as http:
                await http.get("/health/liveliness")
        except httpx.HTTPError:
            return {"reachable": False, "key_ok": None, "error": "LiteLLM gateway is unreachable"}
        today = self.today()
        lo, hi = utc_bounds(today, today, self.settings.litellm_timezone_offset_min)
        try:
            async with self._client() as http:
                await self._spend_page(http, lo, hi, 1, 1)
        except UsageError as exc:
            return {"reachable": True, "key_ok": False, "error": exc.message}
        return {"reachable": True, "key_ok": True, "error": None}
