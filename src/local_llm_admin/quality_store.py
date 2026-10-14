"""Stored quality runs: runs, cases (benchmark or compatibility), repetitions, baselines.

Benchmark and compatibility outcomes are separate: a benchmark case is ``complete``,
``incomplete``, ``failed``, ``cancelled`` or ``interrupted``; a compatibility case is
``pass``, ``fail``, ``unsupported`` or ``unknown``. Only bounded error summaries are
kept, never prompts or generated text.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .quality import regressions, summarize
from .storage import Database

MAX_DETAIL = 500
COMPAT_OUTCOMES = ("pass", "fail", "unsupported", "unknown")
_JSON_RUN = ("original", "restoration", "progress", "settings")
_RUN_FIELDS = (
    "job_id",
    "status",
    "finished",
    "runtime_version",
    "runtime_fingerprint",
    "original",
    "restoration",
    "controlled",
    "warning",
    "error",
    "progress",
)


def _bounded(text: str | None) -> str | None:
    return None if text is None else text[:MAX_DETAIL]


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class QualityStore(Database):
    # --- runs --------------------------------------------------------------------
    def create_run(self, run: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO quality_runs (id, job_id, kind, scope, model, status, started,"
                " runtime_version, runtime_fingerprint, case_version, original, settings,"
                " warning) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["id"],
                    run.get("job_id"),
                    run["kind"],
                    run["scope"],
                    run.get("model"),
                    run["started"],
                    run.get("runtime_version"),
                    run.get("runtime_fingerprint"),
                    run.get("case_version"),
                    json.dumps(run.get("original")),
                    json.dumps(run.get("settings")),
                    run.get("warning"),
                ),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(_RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run fields: {sorted(unknown)}")
        values = []
        for k, v in fields.items():
            if k in _JSON_RUN:
                v = json.dumps(v)
            elif k in ("error", "warning"):
                v = _bounded(v)
            elif k == "controlled" and v is not None:
                v = int(bool(v))
            values.append(v)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE quality_runs SET {sets} WHERE id = ?", (*values, run_id))

    @staticmethod
    def _run_row(row: Any) -> dict[str, Any]:
        run = dict(row)
        for k in _JSON_RUN:
            run[k] = _loads(run[k])
        if run["controlled"] is not None:
            run["controlled"] = bool(run["controlled"])
        return run

    def get_run(self, run_id: str, measurements: bool = False) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM quality_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            cases = [
                self._case_row(r)
                for r in c.execute(
                    "SELECT * FROM quality_cases WHERE run_id = ? ORDER BY id", (run_id,)
                )
            ]
            if measurements:
                for case in cases:
                    case["measurements"] = [
                        self._measurement_row(m)
                        for m in c.execute(
                            "SELECT * FROM quality_measurements WHERE case_id = ? ORDER BY rep, id",
                            (case["id"],),
                        )
                    ]
        run = self._run_row(row)
        run["cases"] = cases
        return run

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM quality_runs ORDER BY started DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run_row(r) for r in rows]

    def reconcile_interrupted(self, now: float | None = None) -> list[dict[str, Any]]:
        """Runs a previous process left ``running`` are interrupted; nothing resumes."""
        now = time.time() if now is None else now
        with self._conn() as c:
            rows = [
                dict(r) for r in c.execute("SELECT * FROM quality_runs WHERE status = 'running'")
            ]
            for r in rows:
                c.execute(
                    "UPDATE quality_runs SET status = 'interrupted', finished = ? WHERE id = ?",
                    (now, r["id"]),
                )
                c.execute(
                    "UPDATE quality_cases SET status = 'interrupted', finished = ?"
                    " WHERE run_id = ? AND status = 'running'",
                    (now, r["id"]),
                )
        return rows

    # --- cases and repetitions -----------------------------------------------------
    def add_case(
        self,
        run_id: str,
        *,
        model: str,
        model_path: str | None,
        fingerprint: str | None,
        case_key: str,
        kind: str,
        settings_hash: str | None,
        cache_state: str | None,
        load_s: float | None,
        profile_fingerprint: str | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO quality_cases (run_id, model, model_path, fingerprint, case_key,"
                " kind, settings_hash, cache_state, status, load_s, started, profile_fingerprint)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (
                    run_id,
                    model,
                    model_path,
                    fingerprint,
                    case_key,
                    kind,
                    settings_hash,
                    cache_state,
                    load_s,
                    time.time(),
                    profile_fingerprint,
                ),
            )
            return int(cur.lastrowid or 0)

    def set_cache_state(self, case_id: int, cache_state: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE quality_cases SET cache_state = ? WHERE id = ?", (cache_state, case_id)
            )

    def add_measurement(
        self,
        case_id: int,
        rep: int,
        *,
        warmup: bool,
        contaminated: bool | None,
        error: str | None,
        data: dict[str, Any],
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO quality_measurements (case_id, rep, warmup, contaminated, error,"
                " data) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    rep,
                    int(warmup),
                    None if contaminated is None else int(contaminated),
                    _bounded(error),
                    json.dumps(data),
                ),
            )

    @staticmethod
    def _case_row(row: Any) -> dict[str, Any]:
        case = dict(row)
        case["summary"] = _loads(case["summary"])
        return case

    @staticmethod
    def _measurement_row(row: Any) -> dict[str, Any]:
        m = dict(row)
        m["warmup"] = bool(m["warmup"])
        m["contaminated"] = None if m["contaminated"] is None else bool(m["contaminated"])
        m["data"] = json.loads(m["data"])
        return m

    def _finish_case(
        self, case_id: int, status: str, summary: dict[str, Any] | None, detail: str | None
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE quality_cases SET status = ?, summary = ?, detail = ?, finished = ?"
                " WHERE id = ?",
                (
                    status,
                    None if summary is None else json.dumps(summary),
                    _bounded(detail),
                    time.time(),
                    case_id,
                ),
            )

    def fail_case(self, case_id: int, status: str, detail: str | None) -> None:
        """A benchmark case that could not finish: ``failed`` or ``cancelled``."""
        self._finish_case(case_id, status, None, detail)

    def set_compatibility(
        self, case_id: int, outcome: str, detail: str | None, data: dict[str, Any] | None = None
    ) -> None:
        if outcome not in COMPAT_OUTCOMES:
            raise ValueError(f"unknown compatibility outcome: {outcome}")
        self._finish_case(case_id, outcome, data, detail)

    def complete_benchmark_case(self, case_id: int, *, run_controlled: bool) -> dict[str, Any]:
        """Aggregate a case's repetitions, compare with its baseline, maybe replace it."""
        with self._conn() as c:
            case = dict(
                c.execute("SELECT * FROM quality_cases WHERE id = ?", (case_id,)).fetchone()
            )
            reps = [
                self._measurement_row(r)
                for r in c.execute(
                    "SELECT * FROM quality_measurements WHERE case_id = ? ORDER BY rep", (case_id,)
                )
            ]
        summary = summarize(reps)
        summary["controlled"] = summary["controlled"] and run_controlled
        # an unknown cache state cannot be matched to another run's, so it is never compared
        comparable = bool(
            summary["complete"]
            and summary["controlled"]
            and case["fingerprint"]
            and case["settings_hash"]
            and case["cache_state"] in ("verified_cold", "verified_warm")
        )
        base = None
        if comparable:
            base = self.baseline(
                case["fingerprint"], case["case_key"], case["settings_hash"], case["cache_state"]
            )
        summary["baseline"] = (
            {"run_id": base["run_id"], "case_id": base["case_id"], "ts": base["ts"]}
            if base
            else None
        )
        summary["regressions"] = regressions(summary["medians"], base["medians"]) if base else []
        summary["cache_state"] = case["cache_state"]
        if comparable:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO quality_baselines (fingerprint, case_key,"
                    " settings_hash, cache_state, model, run_id, case_id, medians, ts)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        case["fingerprint"],
                        case["case_key"],
                        case["settings_hash"],
                        case["cache_state"],
                        case["model"],
                        case["run_id"],
                        case_id,
                        json.dumps(summary["medians"]),
                        time.time(),
                    ),
                )
        self._finish_case(
            case_id, "complete" if summary["complete"] else "incomplete", summary, None
        )
        return summary

    def baseline(
        self, fingerprint: str, case_key: str, settings_hash: str, cache_state: str
    ) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM quality_baselines WHERE fingerprint = ? AND case_key = ?"
                " AND settings_hash = ? AND cache_state = ?",
                (fingerprint, case_key, settings_hash, cache_state),
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["medians"] = json.loads(out["medians"])
        return out

    def latest_compatibility(self) -> dict[str, dict[str, dict[str, Any]]]:
        """``{model: {case_key: {status, detail, run_id, finished}}}`` from the newest run."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT model, case_key, status, detail, run_id, finished, profile_fingerprint"
                " FROM quality_cases"
                " WHERE kind = 'compatibility' AND status IN ('pass', 'fail', 'unsupported',"
                " 'unknown') ORDER BY finished, id"
            ).fetchall()
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for r in rows:  # later rows replace earlier ones
            out.setdefault(r["model"], {})[r["case_key"]] = {
                "status": r["status"],
                "detail": r["detail"],
                "run_id": r["run_id"],
                "finished": r["finished"],
                "profile_fingerprint": r["profile_fingerprint"],
            }
        return out

    # --- retention -------------------------------------------------------------------
    def prune(self, now: float, days: int = 90) -> int:
        """Drop finished runs older than ``days`` with their cases and repetitions.
        Baselines (compact medians) and the jobs table are kept."""
        cutoff = now - days * 86400
        with self._conn() as c:
            ids = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM quality_runs WHERE status != 'running' AND started < ?",
                    (cutoff,),
                )
            ]
            for run_id in ids:
                c.execute(
                    "DELETE FROM quality_measurements WHERE case_id IN"
                    " (SELECT id FROM quality_cases WHERE run_id = ?)",
                    (run_id,),
                )
                c.execute("DELETE FROM quality_cases WHERE run_id = ?", (run_id,))
                c.execute("DELETE FROM quality_runs WHERE id = ?", (run_id,))
        return len(ids)
