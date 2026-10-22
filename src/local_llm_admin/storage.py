"""Admin SQLite database: additive, idempotent migrations plus a small key/value table."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS switch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    from_name TEXT,
    to_name TEXT,
    result TEXT NOT NULL,
    duration_s REAL,
    output_tail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS switch_history_log_uniq
    ON switch_history(ts, action, to_name) WHERE source = 'log';
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    name TEXT,
    from_name TEXT,
    status TEXT NOT NULL,
    returncode INTEGER,
    started REAL NOT NULL,
    finished REAL,
    output TEXT NOT NULL DEFAULT ''
);
"""


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """ALTER is not idempotent; checking first lets a half-applied migration finish."""
    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _v1_base(conn: sqlite3.Connection) -> None:
    # databases from before migrations existed are at user_version 0 with these tables
    for stmt in _BASE_SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _v2_durable_jobs(conn: sqlite3.Connection) -> None:
    _add_column(conn, "jobs", "source", "TEXT NOT NULL DEFAULT 'admin'")
    _add_column(conn, "jobs", "to_name", "TEXT")
    _add_column(conn, "jobs", "rolled_back", "INTEGER")
    _add_column(conn, "jobs", "observed_engine", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL,"
        " updated REAL NOT NULL)"
    )


def _v3_ingestion_and_alerts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS log_cursors (source TEXT PRIMARY KEY, dev INTEGER,"
        " inode INTEGER, offset INTEGER NOT NULL, carry BLOB NOT NULL DEFAULT x'',"
        " skipping INTEGER NOT NULL DEFAULT 0, gap_at REAL, updated REAL NOT NULL)"
    )
    # dedup is the line's file identity and byte offset: replaying a range is a no-op
    conn.execute(
        "CREATE TABLE IF NOT EXISTS log_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source TEXT NOT NULL, dedup TEXT NOT NULL, ts REAL NOT NULL, observed REAL NOT NULL,"
        " kind TEXT NOT NULL, signature TEXT, message TEXT NOT NULL,"
        " data TEXT NOT NULL DEFAULT '{}', UNIQUE(source, dedup))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS log_events_kind_ts ON log_events(kind, ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " key TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,"
        " detail TEXT NOT NULL DEFAULT '', first_seen REAL NOT NULL, last_seen REAL NOT NULL,"
        " count INTEGER NOT NULL DEFAULT 1, resolved_at REAL, acked_at REAL, dedup TEXT)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_key ON alerts(key)"
        " WHERE resolved_at IS NULL AND kind = 'condition'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS alerts_event_dedup ON alerts(key, dedup)"
        " WHERE dedup IS NOT NULL"
    )


def _v4_quality(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quality_runs (id TEXT PRIMARY KEY, job_id TEXT,"
        " kind TEXT NOT NULL, scope TEXT NOT NULL, model TEXT, status TEXT NOT NULL,"
        " started REAL NOT NULL, finished REAL, runtime_version TEXT, runtime_fingerprint TEXT,"
        " case_version TEXT, original TEXT, restoration TEXT, controlled INTEGER,"
        " warning TEXT, error TEXT, progress TEXT, settings TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quality_cases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL, model TEXT NOT NULL, model_path TEXT, fingerprint TEXT,"
        " case_key TEXT NOT NULL, kind TEXT NOT NULL, settings_hash TEXT, cache_state TEXT,"
        " status TEXT NOT NULL, load_s REAL, summary TEXT, detail TEXT, started REAL NOT NULL,"
        " finished REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS quality_cases_run ON quality_cases(run_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quality_measurements (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " case_id INTEGER NOT NULL, rep INTEGER NOT NULL, warmup INTEGER NOT NULL,"
        " contaminated INTEGER, error TEXT, data TEXT NOT NULL DEFAULT '{}')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS quality_measurements_case ON quality_measurements(case_id)"
    )
    # the latest valid, controlled result per comparable key; survives detail pruning
    conn.execute(
        "CREATE TABLE IF NOT EXISTS quality_baselines (fingerprint TEXT NOT NULL,"
        " case_key TEXT NOT NULL, settings_hash TEXT NOT NULL, cache_state TEXT NOT NULL,"
        " model TEXT NOT NULL, run_id TEXT NOT NULL, case_id INTEGER NOT NULL,"
        " medians TEXT NOT NULL, ts REAL NOT NULL,"
        " PRIMARY KEY (fingerprint, case_key, settings_hash, cache_state))"
    )


def _v5_observability(conn: sqlite3.Connection) -> None:
    # one row per inference request seen in mlx-server.log; key = file identity plus the
    # byte offset of its first line, so a replayed range inserts nothing
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inference_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source TEXT NOT NULL, key TEXT NOT NULL, request_id TEXT, started REAL NOT NULL,"
        " finished REAL, model TEXT, attribution TEXT NOT NULL, stream INTEGER,"
        " images INTEGER, prompt_tokens INTEGER, cached_tokens INTEGER,"
        " generated_tokens INTEGER, prefill_s REAL, prefill_tok_s REAL, ttft_s REAL,"
        " decode_s REAL, decode_tok_s REAL, elapsed_s REAL, finish_reason TEXT,"
        " state TEXT NOT NULL, error TEXT, complete INTEGER NOT NULL DEFAULT 0,"
        " log_identity TEXT, log_start INTEGER, log_end INTEGER, UNIQUE(source, key))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS inference_requests_started ON inference_requests(started)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS inference_requests_model ON inference_requests(model, started)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS inference_requests_state ON inference_requests(state, started)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS availability_samples (id INTEGER PRIMARY KEY"
        " AUTOINCREMENT, ts REAL NOT NULL, state TEXT NOT NULL, mlx_intended INTEGER,"
        " generation TEXT, model TEXT, pressure TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS availability_samples_ts ON availability_samples(ts)")


def _v6_inventory(conn: sqlite3.Connection) -> None:
    # when the admin first/last saw each weights directory; not its download date
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_inventory (path TEXT PRIMARY KEY,"
        " first_seen REAL NOT NULL, last_seen REAL NOT NULL, fingerprint TEXT,"
        " size_bytes INTEGER)"
    )


def _v7_downloads(conn: sqlite3.Connection) -> None:
    # the admin's view of each mlx-model download run; the host's status.json is the
    # source of truth, refreshed from download-status
    conn.execute(
        "CREATE TABLE IF NOT EXISTS downloads (id TEXT PRIMARY KEY, repo TEXT NOT NULL,"
        " revision TEXT NOT NULL, dest TEXT NOT NULL, state TEXT NOT NULL,"
        " created REAL NOT NULL, updated REAL NOT NULL, finished REAL, bytes_total INTEGER,"
        " bytes_done INTEGER, files_total INTEGER, files_done INTEGER, current_file TEXT,"
        " error_kind TEXT, message TEXT, retryable INTEGER, published_path TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS downloads_state ON downloads(state)")


def _v8_profile_fingerprint(conn: sqlite3.Connection) -> None:
    # the launch profile a quality case ran with (M3c); NULL for older rows
    _add_column(conn, "quality_cases", "profile_fingerprint", "TEXT")


def _v9_log_cursor_head(conn: sqlite3.Connection) -> None:
    # "<n>:<sha256>" of a log's first n bytes: recognises the same file after a drive
    # remount gives it a new st_dev; NULL until the next poll records it
    _add_column(conn, "log_cursors", "head", "TEXT")


def _v10_decision(conn: sqlite3.Connection) -> None:
    # M6: one row per decision read (counts and types only, never the state or the
    # question text) and one availability sample per interval for the sidecar
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL, client_id TEXT, source TEXT NOT NULL, n_questions INTEGER,"
        " types TEXT, steps INTEGER, samples INTEGER, input_tokens INTEGER,"
        " latency_ms REAL, model_ms REAL, status TEXT NOT NULL, error_code INTEGER,"
        " remote_addr TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS decision_requests_ts ON decision_requests(ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_availability (id INTEGER PRIMARY KEY"
        " AUTOINCREMENT, ts REAL NOT NULL, state TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS decision_availability_ts ON decision_availability(ts)")


def _v11_decision_clients(conn: sqlite3.Connection) -> None:
    # M6 gateway keys: only the SHA-256 of each key is stored; lookup is by prefix
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_clients (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL, key_prefix TEXT NOT NULL UNIQUE, key_hash TEXT NOT NULL,"
        " created_at REAL NOT NULL, revoked_at REAL, last_used_at REAL, note TEXT)"
    )


def _v12_sidecar_slot(conn: sqlite3.Connection) -> None:
    # M7: availability samples per sidecar, TTS request rows (counts only, never the
    # text), and the one-sidecar intent, seeded from the M6 decision intent. The old
    # key stays so an M6 rollback (user_version 11) still finds it.
    _add_column(conn, "decision_availability", "sidecar", "TEXT NOT NULL DEFAULT 'openjev'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tts_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL, client_id TEXT, source TEXT NOT NULL, chars INTEGER,"
        " chunks INTEGER, voice TEXT, format TEXT, audio_s REAL, latency_ms REAL,"
        " status TEXT NOT NULL, error_code INTEGER, remote_addr TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS tts_requests_ts ON tts_requests(ts)")
    if conn.execute("SELECT 1 FROM kv WHERE key = 'sidecar_intent'").fetchone():
        return
    row = conn.execute("SELECT value FROM kv WHERE key = 'decision_intent'").fetchone()
    if row is None:
        return
    try:
        old = json.loads(row[0])
    except ValueError:
        return
    if not isinstance(old, dict):
        return
    value = {
        "name": "openjev",
        "value": old.get("value"),
        "job_id": old.get("job_id"),
        "ts": old.get("ts"),
    }
    conn.execute(
        "INSERT INTO kv (key, value, updated) VALUES ('sidecar_intent', ?, ?)",
        (json.dumps(value), time.time()),
    )


def _v13_embed_service(conn: sqlite3.Connection) -> None:
    # M8: one row per embeddings or rerank request (counts only; never the text, documents
    # or vectors), and the always-on service's own intent, stopped until the owner starts
    # it. The M7 app ignores both (rollback to user_version 12).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embed_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts REAL NOT NULL, client_id TEXT, source TEXT NOT NULL, route TEXT NOT NULL,"
        " inputs INTEGER, chars INTEGER, tokens INTEGER, dims INTEGER, latency_ms REAL,"
        " status TEXT NOT NULL, error_code INTEGER, remote_addr TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS embed_requests_ts ON embed_requests(ts)")
    conn.execute(
        "INSERT OR IGNORE INTO kv (key, value, updated) VALUES ('embed_intent', ?, ?)",
        (json.dumps({"value": "stopped", "job_id": None, "ts": time.time()}), time.time()),
    )


def _v14_evals(conn: sqlite3.Connection) -> None:
    # M10: eval suites, per-case results with the stored response, latest scores per
    # comparable key; the M9 app ignores them (rollback to user_version 13).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS eval_suites (id TEXT NOT NULL, version INTEGER NOT NULL,"
        " title TEXT NOT NULL, origin TEXT NOT NULL CHECK (origin IN ('builtin','custom')),"
        " content_hash TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL,"
        " PRIMARY KEY (id, version))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS eval_results (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL, quality_case_id INTEGER NOT NULL, model TEXT NOT NULL,"
        " fingerprint TEXT, settings_hash TEXT NOT NULL, suite_id TEXT NOT NULL,"
        " suite_hash TEXT NOT NULL, case_id TEXT NOT NULL, category TEXT NOT NULL,"
        " response TEXT, reasoning_chars INTEGER, tool_calls TEXT, finish_reason TEXT,"
        " prompt_tokens INTEGER, completion_tokens INTEGER, latency_s REAL, error TEXT,"
        " score REAL, passed INTEGER, grade_state TEXT NOT NULL"
        " CHECK (grade_state IN ('graded','needs_judge','judge_error','ungraded','failed')),"
        " grade_detail TEXT, judge TEXT, self_judged INTEGER, judged_at REAL,"
        " thinking INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS eval_results_run ON eval_results(run_id, model)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS eval_scores (fingerprint TEXT NOT NULL,"
        " suite_id TEXT NOT NULL, suite_hash TEXT NOT NULL, settings_hash TEXT NOT NULL,"
        " model TEXT NOT NULL, run_id TEXT NOT NULL, n INTEGER NOT NULL,"
        " excluded INTEGER NOT NULL, mean REAL NOT NULL, lo REAL NOT NULL, hi REAL NOT NULL,"
        " by_category TEXT NOT NULL, judge TEXT, self_judged INTEGER NOT NULL,"
        " ts REAL NOT NULL, thinking INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (fingerprint, suite_id, suite_hash, settings_hash))"
    )


def _v15_token_usage(conn: sqlite3.Connection) -> None:
    # token usage: each request row says how many requests it stands for (a Strata gap
    # row stands for several) and whether the admin's own quality/eval traffic made it;
    # the daily table keeps token totals of rows the retention pass deleted. The v14 app
    # ignores all three (rollback to user_version 14).
    _add_column(conn, "inference_requests", "requests", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "inference_requests", "origin", "TEXT NOT NULL DEFAULT 'client'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS token_usage_daily (date TEXT NOT NULL,"
        " source TEXT NOT NULL, model TEXT NOT NULL, origin TEXT NOT NULL,"
        " requests INTEGER NOT NULL, measured INTEGER NOT NULL,"
        " cached_unknown INTEGER NOT NULL, input_tokens INTEGER NOT NULL,"
        " cached_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
        " first_started REAL NOT NULL, last_started REAL NOT NULL,"
        " PRIMARY KEY (date, source, model, origin))"
    )


# Append only; never edit a released step. Each must be safe to re-run.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _v1_base,
    _v2_durable_jobs,
    _v3_ingestion_and_alerts,
    _v4_quality,
    _v5_observability,
    _v6_inventory,
    _v7_downloads,
    _v8_profile_fingerprint,
    _v9_log_cursor_head,
    _v10_decision,
    _v11_decision_clients,
    _v12_sidecar_slot,
    _v13_embed_service,
    _v14_evals,
    _v15_token_usage,
]
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> None:
    """``conn`` must be in autocommit mode (``isolation_level=None``) so each step and
    its version bump commit together; SQLite DDL is transactional."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"admin database schema {version} is newer than this app ({SCHEMA_VERSION});"
            " roll the app forward or restore the matching database backup"
        )
    for number in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            MIGRATIONS[number - 1](conn)
            conn.execute(f"PRAGMA user_version = {number}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            migrate(conn)
        finally:
            conn.close()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def get_kv(self, key: str) -> Any:
        with self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_kv(self, key: str, value: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) ON CONFLICT(key)"
                " DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (key, json.dumps(value), time.time()),
            )

    def delete_kv(self, key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM kv WHERE key = ?", (key,))
