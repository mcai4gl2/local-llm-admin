import json
import sqlite3

import pytest

from local_llm_admin.jobs import JobStore
from local_llm_admin.storage import SCHEMA_VERSION, Database

# the schema shipped before migrations existed (user_version 0)
LEGACY = """
CREATE TABLE switch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, action TEXT NOT NULL,
    from_name TEXT, to_name TEXT, result TEXT NOT NULL, duration_s REAL,
    output_tail TEXT NOT NULL DEFAULT '', source TEXT NOT NULL
);
CREATE UNIQUE INDEX switch_history_log_uniq
    ON switch_history(ts, action, to_name) WHERE source = 'log';
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, action TEXT NOT NULL, name TEXT, from_name TEXT,
    status TEXT NOT NULL, returncode INTEGER, started REAL NOT NULL, finished REAL,
    output TEXT NOT NULL DEFAULT ''
);
INSERT INTO jobs VALUES ('old1', 'stop', NULL, 'qwen', 'succeeded', 0, 1.0, 2.0, 'done');
INSERT INTO switch_history (ts, action, from_name, to_name, result, duration_s, source)
    VALUES (1.0, 'stop', 'qwen', NULL, 'succeeded', 1.0, 'admin');
"""


def _columns(path, table):
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def test_fresh_database_is_at_current_version(tmp_path):
    path = tmp_path / "a.sqlite"
    Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert {"source", "to_name", "rolled_back", "observed_engine"} <= _columns(path, "jobs")
    assert {"key", "value"} <= _columns(path, "kv")


def test_legacy_database_upgrades_twice_and_keeps_rows(tmp_path):
    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY)
    JobStore(path)
    store = JobStore(path)  # second open is a no-op
    job = store.get_job("old1")
    assert job["status"] == "succeeded" and job["source"] == "admin" and job["output"] == "done"
    assert job["rolled_back"] is None and job["observed_engine"] is None
    (row,) = store.history()
    assert row["action"] == "stop" and row["source"] == "admin"


def test_partially_migrated_database_is_completed(tmp_path):
    """A crash between ALTERs must not make the next start fail on a duplicate column."""
    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY)
        c.execute("ALTER TABLE jobs ADD COLUMN source TEXT NOT NULL DEFAULT 'admin'")
    Database(path)
    assert {"source", "to_name", "rolled_back", "observed_engine"} <= _columns(path, "jobs")


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as c:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_kv_roundtrip_and_delete(tmp_path):
    db = Database(tmp_path / "a.sqlite")
    assert db.get_kv("intent") is None
    db.set_kv("intent", {"value": "off", "n": 1})
    db.set_kv("intent", {"value": "comfyui"})
    assert db.get_kv("intent") == {"value": "comfyui"}
    db.delete_kv("intent")
    assert db.get_kv("intent") is None


def test_failed_migration_step_rolls_back_with_its_version(tmp_path, monkeypatch):
    from local_llm_admin import storage

    path = tmp_path / "a.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY)

    def broken(conn):
        conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT")
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(storage, "MIGRATIONS", [storage._v1_base, broken])
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "source" not in _columns(path, "jobs")  # the partial step was rolled back
    monkeypatch.undo()
    Database(path)
    assert "source" in _columns(path, "jobs")


def test_v12_moves_the_decision_intent_and_tags_availability(tmp_path):
    path = tmp_path / "a.sqlite"
    Database(path)  # current schema
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA user_version = 11")
        c.execute("DELETE FROM kv WHERE key = 'sidecar_intent'")
        c.execute(
            "INSERT INTO kv (key, value, updated) VALUES ('decision_intent', ?, 1)",
            (json.dumps({"value": "running", "job_id": "j1", "ts": 5.0}),),
        )
        c.execute("INSERT INTO decision_availability (ts, state) VALUES (1, 'ready')")
    db = Database(path)
    assert db.get_kv("sidecar_intent") == {
        "name": "openjev",
        "value": "running",
        "job_id": "j1",
        "ts": 5.0,
    }
    assert db.get_kv("decision_intent")["value"] == "running"  # kept for an M6 rollback
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT sidecar FROM decision_availability").fetchone()[0] == "openjev"
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert {
        "ts",
        "client_id",
        "source",
        "chars",
        "chunks",
        "voice",
        "format",
        "audio_s",
        "latency_ms",
        "status",
        "error_code",
        "remote_addr",
    } <= _columns(path, "tts_requests")


def test_v12_never_overwrites_a_newer_sidecar_intent(tmp_path):
    path = tmp_path / "a.sqlite"
    db = Database(path)
    db.set_kv("sidecar_intent", {"name": "qwen-tts", "value": "running"})
    db.set_kv("decision_intent", {"value": "stopped"})
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA user_version = 11")
    assert Database(path).get_kv("sidecar_intent")["name"] == "qwen-tts"


def test_v13_adds_embed_requests_and_stopped_intent(tmp_path):
    import json
    import sqlite3

    from local_llm_admin.storage import SCHEMA_VERSION, Database, migrate

    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    migrate(conn)
    assert SCHEMA_VERSION >= 13
    cols = {r[1] for r in conn.execute("PRAGMA table_info(embed_requests)")}
    assert {"route", "inputs", "chars", "tokens", "dims", "client_id", "status"} <= cols
    row = conn.execute("SELECT value FROM kv WHERE key = 'embed_intent'").fetchone()
    assert json.loads(row[0])["value"] == "stopped"
    conn.close()
    Database(path)  # re-running is a no-op


def test_v13_keeps_an_existing_intent(tmp_path):
    import json
    import sqlite3

    from local_llm_admin.storage import MIGRATIONS, migrate

    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    for number, step in enumerate(MIGRATIONS[:12], start=1):
        step(conn)
        conn.execute(f"PRAGMA user_version = {number}")
    conn.execute(
        "INSERT INTO kv (key, value, updated) VALUES ('embed_intent', ?, 0)",
        (json.dumps({"value": "running"}),),
    )
    migrate(conn)
    row = conn.execute("SELECT value FROM kv WHERE key = 'embed_intent'").fetchone()
    assert json.loads(row[0])["value"] == "running"


def test_v14_adds_eval_tables_and_is_rerunnable(tmp_path):
    import sqlite3

    from local_llm_admin.storage import SCHEMA_VERSION, Database, migrate

    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    migrate(conn)
    assert SCHEMA_VERSION >= 14
    for table in ("eval_suites", "eval_results", "eval_scores"):
        assert conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    Database(path)  # re-running is a no-op


def test_v15_adds_token_usage_and_is_rerunnable(tmp_path):
    import sqlite3

    from local_llm_admin.storage import MIGRATIONS, SCHEMA_VERSION, Database, migrate

    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    migrate(conn)
    assert SCHEMA_VERSION >= 15
    assert {"requests", "origin"} <= _columns(path, "inference_requests")
    assert {"date", "source", "model", "origin", "cached_tokens"} <= _columns(
        path, "token_usage_daily"
    )
    MIGRATIONS[14](conn)  # a half-applied step finishes cleanly
    conn.close()
    Database(path)
