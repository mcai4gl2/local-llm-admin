"""Read-only Mac VM units and fail-closed host-recovery review; no lifecycle actions."""

from __future__ import annotations

import math
import re
from typing import Any

PAGE_FIELDS = {
    "Pages free": "free_bytes",
    "Pages active": "active_bytes",
    "Pages inactive": "inactive_bytes",
    "Pages speculative": "speculative_bytes",
    "Pages wired down": "wired_bytes",
    "File-backed pages": "file_backed_bytes",
    "Anonymous pages": "anonymous_bytes",
    "Pages stored in compressor": "compressor_stored_bytes",
    "Pages occupied by compressor": "compressor_occupied_bytes",
}
COUNTER_FIELDS = {
    "Pageins": "pageins_pages",
    "Pageouts": "pageouts_pages",
    "Swapins": "swapins_pages",
    "Swapouts": "swapouts_pages",
}
OFFSETS = [2, 10, 30, 60, 75, 88, 180, 300]


def parse_vm_stat(text: str) -> dict[str, int]:
    headers = re.findall(r"page size of ([0-9]+) bytes", text)
    if len(headers) != 1:
        raise ValueError("Missing or ambiguous VM page size.")
    size = int(headers[0])
    if not 4096 <= size <= 65536 or size & (size - 1):
        raise ValueError("Invalid VM page size.")
    values = {"page_size": size}
    fields = PAGE_FIELDS | COUNTER_FIELDS
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        label = label.strip().strip('"')
        if not sep or label not in fields:
            continue
        name = fields[label]
        if name in values or not re.fullmatch(r"[0-9]+\.?", value.strip()):
            raise ValueError("Duplicate or malformed VM counter.")
        count = int(value.strip().removesuffix("."))
        values[name] = count * size if label in PAGE_FIELDS else count
    if set(values) != {"page_size", *fields.values()}:
        raise ValueError("Incomplete VM counters.")
    return values


def _integer(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (value > 0 if positive else value >= 0)


def _number(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def _valid_snapshot(s: dict[str, Any]) -> bool:
    vm = s["vm"]
    return (
        _integer(s["total_bytes"], positive=True)
        and _integer(s["available_bytes"])
        and s["available_bytes"] <= s["total_bytes"]
        and all(
            _integer(vm[k]) for k in ["page_size", *PAGE_FIELDS.values(), *COUNTER_FIELDS.values()]
        )
        and vm["page_size"] in [4096, 8192, 16384, 32768, 65536]
        and (_integer(s["other_rss_bytes"]) or s["other_rss_bytes"] is None)
        and _integer(s["other_uss_missing"])
        and (
            _integer(s["other_uss_bytes"])
            or (s["other_uss_bytes"] is None and s["other_uss_missing"] > 0)
        )
        and _number(s["boot_time"])
        and _number(s["span_s"])
        and s["span_s"] <= 15
    )


def review_reclamation(
    baseline: list[dict[str, Any]],
    stops: list[dict[str, Any]],
    *,
    complete: bool,
    ownership_ok: bool,
    functional_ok: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"gate": "inconclusive"}
    try:
        if not complete or len(baseline) != 3 or [s["target_s"] for s in stops] != OFFSETS:
            return result
        snapshots = baseline + [s["snapshot"] for s in stops]
        if not all(_valid_snapshot(s) for s in snapshots):
            return result
        if len({(s["total_bytes"], s["boot_time"]) for s in snapshots}) != 1:
            return result
        if any(
            not _number(s["observed_s"])
            or not s["target_s"] <= s["observed_s"] <= s["target_s"] + 15
            for s in stops
        ):
            return result
        base = baseline[-1]
        drift = max(s["available_bytes"] for s in baseline) - min(
            s["available_bytes"] for s in baseline
        )
        threshold = base["available_bytes"] - max(2 * 1024**3, base["available_bytes"] * 0.1)
        early = [s for s in stops if s["observed_s"] <= 90]
        recovered = any(
            all(s["snapshot"]["available_bytes"] >= threshold for s in early[i : i + 3])
            and early[i + 2]["observed_s"] - early[i]["observed_s"] >= 4
            for i in range(len(early) - 2)
        )
        end = early[-1]["snapshot"]
        result.update(
            gate="fail"
            if not ownership_ok or not functional_ok
            else "pass"
            if recovered and drift <= 1024**3
            else "inconclusive",
            recovered_90_s=recovered,
            baseline_drift_bytes=drift,
            threshold_bytes=threshold,
            baseline_available_bytes=base["available_bytes"],
            primary_observed_s=early[-1]["observed_s"],
            available_gap_bytes=base["available_bytes"] - end["available_bytes"],
            primary_available_bytes=end["available_bytes"],
            latest_above_threshold=stops[-1]["snapshot"]["available_bytes"] >= threshold,
            latest_available_bytes=stops[-1]["snapshot"]["available_bytes"],
            vm_delta_bytes={
                name: end["vm"][name] - base["vm"][name] for name in PAGE_FIELDS.values()
            },
            other_rss_delta_bytes=(end["other_rss_bytes"] - base["other_rss_bytes"])
            if end["other_rss_bytes"] is not None and base["other_rss_bytes"] is not None
            else None,
            other_uss_delta_bytes=(end["other_uss_bytes"] - base["other_uss_bytes"])
            if base["other_uss_missing"] == end["other_uss_missing"] == 0
            else None,
            other_uss_missing_before=base["other_uss_missing"],
            other_uss_missing_after=end["other_uss_missing"],
        )
    except (KeyError, TypeError, ValueError):
        return {"gate": "inconclusive"}
    return result
