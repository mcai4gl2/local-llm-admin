#!/usr/bin/env python3
"""Plan bounded continuation windows from private runs, without contacting a server.

This is an estimate and a worksheet, never authorization or a readiness proof.
Keep its output private. Fresh preflight, reviewed manifests and an independent
deadline supervisor are still required before any approved live window.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import bench

_INCOMPLETE_GATES = {
    "insufficient_repetitions_or_sessions",
    "sessions_not_counterbalanced",
    "insufficient_p95_samples",
    "client_wall_rate_is_not_native_decode",
    "confidence_interval_overlaps_boundary",
}


def reviewed_history(runs: list[bench.Run]) -> tuple[int, str]:
    """Validate evidence without treating incomplete qualification as a failed capture."""
    if not runs:
        raise ValueError("missing previous evidence")
    # Revalidate mutable models and compare without dropping any failed/partial runs.
    checked = [bench.Run.model_validate(r.model_dump()) for r in runs]
    report = bench.compare_runs(checked)
    if set(report["issues"]) - _INCOMPLETE_GATES:
        raise ValueError("invalid previous evidence")
    sessions = sorted({r.manifest.session for r in checked})
    if sessions != list(range(1, len(sessions) + 1)) or len(sessions) > 3:
        raise ValueError("previous sessions must be contiguous from one, at most three")
    first_order = next(r.manifest.order for r in checked if r.manifest.session == 1)
    for session in sessions:
        pair = [r for r in checked if r.manifest.session == session]
        expected_order = first_order
        if session % 2 == 0:
            expected_order = "splash-first" if first_order == "mlx-first" else "mlx-first"
        if len(pair) != 2 or any(r.manifest.order != expected_order for r in pair):
            raise ValueError("invalid paired session order")
        indexed = {
            r.manifest.backend: {(s.case_id, s.rep): s.measurement for s in r.samples}
            for r in pair
        }
        for r in pair:
            cases = {c.id: c for c in r.manifest.cases}
            for sample in r.samples:
                value = sample.measurement
                if (
                    not bench.positive(value.total_s)
                    or value.finish_reason != "length"
                    or value.completion_tokens != cases[sample.case_id].max_tokens
                ):
                    raise ValueError("incomplete request, including warmup")
                other = indexed["splash" if r.manifest.backend == "mlx" else "mlx"]
                if value.prompt_tokens != other[(sample.case_id, sample.rep)].prompt_tokens:
                    raise ValueError("paired prompt token mismatch")
    return len(sessions), first_order


def plan_windows(
    cohorts: dict[str, list[bench.Run]],
    *,
    window_s: float = 7200.0,
    setup_s: float = 900.0,
    restore_s: float = 600.0,
    slowdown: float = 1.25,
) -> dict[str, Any]:
    """Pack whole future pairs in session order, retaining an explicit restore reserve."""
    if (
        not cohorts
        or any(not bench.positive(v) for v in (window_s, restore_s, slowdown))
        or not math.isfinite(setup_s)
        or setup_s < 0
        or slowdown < 1
        or setup_s + restore_s >= window_s
    ):
        raise ValueError("invalid window budget")
    pending: list[tuple[int, str, list[dict[str, Any]]]] = []
    histories: list[dict[str, Any]] = []
    for label, runs in sorted(cohorts.items()):
        if not label or len(label) > 80 or not label.isascii() or not all(
            c.isalnum() or c in "_-" for c in label
        ) or not label[0].isalnum():
            raise ValueError("invalid cohort label")
        completed, first_order = reviewed_history(runs)
        histories.append({
            "cohort": label,
            "completed_pairs": completed,
            "comparison_sha256": runs[0].manifest.comparison_key(),
            "sources": [
                {"id": r.manifest.id, "sha256": bench.digest(r.model_dump())}
                for r in sorted(runs, key=lambda r: (r.manifest.session, r.manifest.backend))
            ],
        })
        backend_costs: dict[str, float] = {}
        for backend in ("mlx", "splash"):
            maxima: dict[str, float] = defaultdict(float)
            for r in runs:
                if r.manifest.backend != backend:
                    continue
                for sample in r.samples:
                    assert sample.measurement.total_s is not None
                    maxima[sample.case_id] = max(maxima[sample.case_id], sample.measurement.total_s)
            backend_costs[backend] = (
                sum(maxima.values()) * (runs[0].manifest.repeats + 1) * slowdown
            )
        for session in range(completed + 1, 4):
            order = first_order
            if session % 2 == 0:
                order = "splash-first" if first_order == "mlx-first" else "mlx-first"
            backends = ("mlx", "splash") if order == "mlx-first" else ("splash", "mlx")
            planned = []
            for backend in backends:
                source = max(
                    (r for r in runs if r.manifest.backend == backend),
                    key=lambda r: r.manifest.session,
                )
                planned.append({
                    "cohort": label,
                    "session": session,
                    "order": order,
                    "backend": backend,
                    "source_run_id": source.manifest.id,
                    "source_sha256": bench.digest(source.model_dump()),
                    "request_count": len(source.manifest.cases) * (source.manifest.repeats + 1),
                    "budgeted_request_s": backend_costs[backend],
                    "requires_fresh_preflight_and_manifest": True,
                })
            pending.append((session, label, planned))

    windows: list[dict[str, Any]] = []
    overhead_s = setup_s + restore_s
    for _, _, pair in sorted(pending):
        cost = sum(r["budgeted_request_s"] for r in pair)
        if not math.isfinite(cost) or overhead_s + cost > window_s:
            raise ValueError("whole paired session cannot fit the window budget")
        if not windows or windows[-1]["budgeted_total_s"] + cost > window_s:
            windows.append({
                "number": len(windows) + 1,
                "maximum_s": window_s,
                "setup_and_switch_reserve_s": setup_s,
                "restore_reserve_s": restore_s,
                "budgeted_total_s": overhead_s,
                "runs": [],
            })
        windows[-1]["runs"].extend(pair)
        windows[-1]["budgeted_total_s"] += cost
    return {
        "schema_version": 1,
        "authorization": "required",
        "adoption_decision": "unassessed",
        "estimator": "slowest recorded request per case including warmups, times slowdown",
        "slowdown": slowdown,
        "histories": histories,
        "remaining_pairs": len(pending),
        "windows": windows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", nargs=2, action="append", required=True, metavar=("ID", "RESULTS"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window-minutes", type=float, default=120.0)
    parser.add_argument("--setup-minutes", type=float, default=15.0)
    parser.add_argument("--restore-minutes", type=float, default=10.0)
    parser.add_argument("--slowdown", type=float, default=1.25)
    args = parser.parse_args()
    try:
        cohorts = {}
        for name, directory in args.cohort:
            if name in cohorts:
                raise ValueError("duplicate cohort")
            cohorts[name] = bench.load_runs(Path(directory))
        plan = plan_windows(
            cohorts,
            window_s=args.window_minutes * 60,
            setup_s=args.setup_minutes * 60,
            restore_s=args.restore_minutes * 60,
            slowdown=args.slowdown,
        )
        bench.private_write(args.out, json.dumps(plan, indent=2) + "\n", exclusive=True)
        print(f"Prepared {len(plan['windows'])} window(s); live authorization still required.")
        return 0
    except (ValueError, OSError):
        print("Invalid evidence/budget or output exists; inspect private files locally.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
