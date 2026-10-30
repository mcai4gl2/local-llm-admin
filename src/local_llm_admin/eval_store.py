"""Eval storage (M10): suites (builtin and custom, versioned), per-case results with the
stored response, and the latest promoted score per comparable key."""

from __future__ import annotations

import json
import time
from typing import Any

from .eval_stats import mean_ci
from .eval_suites import Suite, parse_suite
from .storage import Database

MAX_RESPONSE_BYTES = 64 * 1024
MAX_DETAIL_CHARS = 2048
EXCLUDED_STATES = ("judge_error", "ungraded", "failed")
_RESULT_FIELDS = frozenset(
    {
        "run_id", "quality_case_id", "model", "fingerprint", "settings_hash", "suite_id",
        "suite_hash", "case_id", "category", "response", "reasoning_chars", "tool_calls",
        "finish_reason", "prompt_tokens", "completion_tokens", "latency_s", "error", "score",
        "passed", "grade_state", "grade_detail", "judge", "self_judged", "judged_at",
        "thinking",
    }
)  # fmt: skip


def _clip_bytes(text: str, limit: int) -> str:
    raw = text.encode()
    return text if len(raw) <= limit else raw[:limit].decode(errors="ignore")


class EvalStore(Database):
    def sync_builtins(self, suites: list[Suite]) -> None:
        with self._conn() as c:
            for suite in suites:
                digest = suite.content_hash()
                row = c.execute(
                    "SELECT origin, content_hash FROM eval_suites WHERE id = ? AND version = ?",
                    (suite.id, suite.version),
                ).fetchone()
                if row is None:
                    c.execute(
                        "INSERT INTO eval_suites (id, version, title, origin, content_hash, body,"
                        " created) VALUES (?, ?, ?, 'builtin', ?, ?, ?)",
                        (suite.id, suite.version, suite.title, digest, suite.model_dump_json(),
                         time.time()),
                    )  # fmt: skip
                elif row["origin"] != "builtin" or row["content_hash"] != digest:
                    raise RuntimeError(
                        f"builtin suite {suite.id} v{suite.version} changed or is taken;"
                        " bump the version instead"
                    )

    def add_custom(self, suite: Suite) -> None:
        with self._conn() as c:
            if c.execute(
                "SELECT 1 FROM eval_suites WHERE id = ? AND origin = 'builtin'", (suite.id,)
            ).fetchone():
                raise ValueError(f"{suite.id} is a builtin suite id")
            top = c.execute(
                "SELECT MAX(version) FROM eval_suites WHERE id = ?", (suite.id,)
            ).fetchone()[0]
            numbered = suite.model_copy(update={"version": (top or 0) + 1})
            c.execute(
                "INSERT INTO eval_suites (id, version, title, origin, content_hash, body, created)"
                " VALUES (?, ?, ?, 'custom', ?, ?, ?)",
                (numbered.id, numbered.version, numbered.title, numbered.content_hash(),
                 numbered.model_dump_json(), time.time()),
            )  # fmt: skip

    def suites(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, version, title, origin, content_hash, body FROM eval_suites s"
                " WHERE version = (SELECT MAX(version) FROM eval_suites WHERE id = s.id)"
                " ORDER BY id"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["cases"] = len(json.loads(d.pop("body")).get("cases", []))
            out.append(d)
        return out

    def suite(self, id: str, version: int | None = None) -> Suite | None:
        with self._conn() as c:
            if version is None:
                row = c.execute(
                    "SELECT body FROM eval_suites WHERE id = ? ORDER BY version DESC LIMIT 1",
                    (id,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT body FROM eval_suites WHERE id = ? AND version = ?", (id, version)
                ).fetchone()
        return parse_suite(json.loads(row["body"])) if row else None

    def suite_by_hash(self, id: str, content_hash: str) -> Suite | None:
        """The stored suite version a result was produced from."""
        with self._conn() as c:
            row = c.execute(
                "SELECT body FROM eval_suites WHERE id = ? AND content_hash = ?"
                " ORDER BY version DESC LIMIT 1",
                (id, content_hash),
            ).fetchone()
        return parse_suite(json.loads(row["body"])) if row else None

    def is_promoted(self, run_id: str, fingerprint: str, suite_id: str) -> bool:
        with self._conn() as c:
            return (
                c.execute(
                    "SELECT 1 FROM eval_scores WHERE run_id = ? AND fingerprint = ?"
                    " AND suite_id = ?",
                    (run_id, fingerprint, suite_id),
                ).fetchone()
                is not None
            )

    def delete_custom(self, id: str) -> int:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM eval_suites WHERE id = ? AND origin = 'custom'", (id,)
            ).rowcount

    def add_result(self, **fields: Any) -> int:
        unknown = set(fields) - _RESULT_FIELDS
        if unknown:
            raise ValueError(f"unknown result fields: {sorted(unknown)}")
        if isinstance(fields.get("response"), str):
            fields["response"] = _clip_bytes(fields["response"], MAX_RESPONSE_BYTES)
        if isinstance(fields.get("grade_detail"), str):
            fields["grade_detail"] = fields["grade_detail"][:MAX_DETAIL_CHARS]
        if fields.get("tool_calls") is not None and not isinstance(fields["tool_calls"], str):
            fields["tool_calls"] = json.dumps(fields["tool_calls"])
        names = list(fields)
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO eval_results ({', '.join(names)})"
                f" VALUES ({', '.join('?' for _ in names)})",
                [fields[n] for n in names],
            )
            return int(cur.lastrowid or 0)

    def results(self, run_id: str, model: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM eval_results WHERE run_id = ?"
        args: list[Any] = [run_id]
        if model is not None:
            sql += " AND model = ?"
            args.append(model)
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY id", args).fetchall()]

    def needs_judge(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM eval_results WHERE run_id = ? AND grade_state = 'needs_judge'"
                " ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_judged(
        self,
        result_id: int,
        *,
        score: float | None,
        passed: bool | None,
        detail: str | None,
        judge: str | None,
        self_judged: bool,
        state: str,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE eval_results SET score = ?, passed = ?, grade_detail = ?, judge = ?,"
                " self_judged = ?, grade_state = ?, judged_at = ? WHERE id = ?",
                (score, None if passed is None else int(passed),
                 None if detail is None else detail[:MAX_DETAIL_CHARS], judge,
                 int(self_judged), state, time.time(), result_id),
            )  # fmt: skip

    def update_grade(
        self, result_id: int, *, score: float | None, passed: bool | None, state: str,
        detail: str | None,
    ) -> None:  # fmt: skip
        """Replace a deterministic grade (regrade); judge fields are left alone."""
        with self._conn() as c:
            c.execute(
                "UPDATE eval_results SET score = ?, passed = ?, grade_state = ?, grade_detail = ?"
                " WHERE id = ?",
                (score, None if passed is None else int(passed), state,
                 None if detail is None else detail[:MAX_DETAIL_CHARS], result_id),
            )  # fmt: skip

    def finalize(self, run_id: str, model: str, *, promote: bool) -> dict[str, Any]:
        """Score one (run, model, suite). Only ``graded`` rows count; rows in
        ``EXCLUDED_STATES`` are reported as ``excluded``. With nothing graded the summary
        has ``n`` 0 and no score, and nothing is promoted."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM eval_results WHERE run_id = ? AND model = ? ORDER BY id",
                (run_id, model),
            ).fetchall()
        if len({r["suite_id"] for r in rows}) > 1:
            raise ValueError(f"run {run_id} has more than one suite for {model}")
        graded = [r for r in rows if r["grade_state"] == "graded" and r["score"] is not None]
        excluded = sum(1 for r in rows if r["grade_state"] in EXCLUDED_STATES)
        summary: dict[str, Any] = {
            "run_id": run_id,
            "model": model,
            "suite_id": rows[0]["suite_id"] if rows else None,
            "n": len(graded),
            "excluded": excluded,
            "mean": None,
            "lo": None,
            "hi": None,
            "by_category": {},
            "judge": None,
            "self_judged": False,
        }
        if not graded:
            return summary
        interval = mean_ci([r["score"] for r in graded])
        cats: dict[str, list[float]] = {}
        for r in graded:
            cats.setdefault(r["category"], []).append(r["score"])
        judges = [r["judge"] for r in graded if r["judge"]]
        summary.update(
            mean=interval.mean,
            lo=interval.lo,
            hi=interval.hi,
            by_category={k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
            judge=judges[0] if judges else None,
            self_judged=any(r["self_judged"] for r in graded),
        )
        first = graded[0]
        if promote and first["fingerprint"] is not None:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO eval_scores (fingerprint, suite_id, suite_hash,"
                    " settings_hash, model, run_id, n, excluded, mean, lo, hi, by_category,"
                    " judge, self_judged, ts, thinking)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (first["fingerprint"], first["suite_id"], first["suite_hash"],
                     first["settings_hash"], model, run_id, summary["n"], excluded,
                     interval.mean, interval.lo, interval.hi,
                     json.dumps(summary["by_category"]), summary["judge"],
                     int(summary["self_judged"]), time.time(), int(bool(first["thinking"]))),
                )  # fmt: skip
        return summary

    def scorecard(self) -> list[dict[str, Any]]:
        """The newest promoted score per model, suite and thinking setting: cells keyed
        ``<suite_id>`` (thinking off) and ``<suite_id>+thinking``. Each cell names the suite
        version it was scored on (None once that version is no longer stored)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT s.*, q.original AS run_original, q.settings AS run_settings,"
                " q.runtime_version, q.runtime_fingerprint,"
                " (SELECT MAX(version) FROM eval_suites v WHERE v.id = s.suite_id"
                " AND v.content_hash = s.suite_hash) AS suite_version"
                " FROM eval_scores s LEFT JOIN quality_runs q ON q.id = s.run_id ORDER BY s.ts, s.rowid"
            ).fetchall()
        cards: dict[str, dict[str, Any]] = {}
        for r in rows:  # ascending ts, so the newest score per cell wins
            card = cards.setdefault(
                r["fingerprint"],
                {"fingerprint": r["fingerprint"], "model": r["model"], "suites": {}},
            )
            card["model"] = r["model"]
            thinking = bool(r["thinking"])
            card["suites"][r["suite_id"] + ("+thinking" if thinking else "")] = {
                "suite_id": r["suite_id"],
                "suite_version": r["suite_version"],
                "thinking": thinking,
                "suite_hash": r["suite_hash"],
                "settings_hash": r["settings_hash"],
                "provenance": {
                    "model": r["model"],
                    "scope": "model",
                    "original": json.loads(r["run_original"]) if r["run_original"] else None,
                    "settings": json.loads(r["run_settings"]) if r["run_settings"] else None,
                    "runtime_version": r["runtime_version"],
                    "runtime_fingerprint": r["runtime_fingerprint"],
                },
                "run_id": r["run_id"],
                "n": r["n"],
                "excluded": r["excluded"],
                "mean": r["mean"],
                "lo": r["lo"],
                "hi": r["hi"],
                "by_category": json.loads(r["by_category"]),
                "judge": r["judge"],
                "self_judged": bool(r["self_judged"]),
                "ts": r["ts"],
            }
        return sorted(cards.values(), key=lambda x: (x["model"], x["fingerprint"]))

    def case_scores(self, run_id: str, model: str, suite_id: str) -> dict[str, float]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT case_id, score FROM eval_results WHERE run_id = ? AND model = ?"
                " AND suite_id = ? AND grade_state = 'graded' AND score IS NOT NULL",
                (run_id, model, suite_id),
            ).fetchall()
        return {r["case_id"]: r["score"] for r in rows}

    def prune(self, now: float, days: int) -> int:
        """Drop result rows of finished runs older than ``days``, and rows whose run row is
        gone (the quality prune removes runs first; results are only written during a live
        run, which has its row). Scores and suites stay."""
        cutoff = now - days * 86400
        with self._conn() as c:
            return c.execute(
                "DELETE FROM eval_results WHERE run_id NOT IN (SELECT id FROM quality_runs)"
                " OR run_id IN (SELECT id FROM quality_runs"
                " WHERE status != 'running' AND started < ?)",
                (cutoff,),
            ).rowcount
