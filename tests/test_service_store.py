"""Additive resident request history and embedding compatibility."""

import json
import sqlite3

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.embed_store import EmbedStore
from local_llm_admin.service_store import ServiceStore
from local_llm_admin.storage import MIGRATIONS


def test_additive_migration_keeps_embedding_rows_and_intent(tmp_path):
    path = tmp_path / "admin.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    for version, migration in enumerate(MIGRATIONS[:16], 1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.execute("INSERT INTO embed_requests (ts,source,route,status,inputs,chars) VALUES (1,'gateway','embed','ok',2,10)")
    conn.execute("UPDATE kv SET value = ? WHERE key='embed_intent'", (json.dumps({"value": "running"}),))
    conn.close()
    history = ServiceStore(path, Settings())
    assert history.recent("qwen-embed")[0]["capability"] == "embeddings"
    assert history.recent("qwen-embed")[0]["model"] == "qwen3-embed"
    assert history.recent("qwen-embed")[0]["input_chars"] == 10
    assert EmbedStore(path).recent()[0]["chars"] == 10
    assert history.get_kv("embed_intent")["value"] == "running"
    day = history.daily("qwen-embed", 0, 2, 0)[0]
    assert day["requests"] == 1 and day["inputs"] == 2 and day["pages"] is None
    with history._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM embed_requests").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM service_requests").fetchone()[0] == 0


def test_ocr_history_is_typed_metadata_only(tmp_path):
    history = ServiceStore(tmp_path / "admin.sqlite", Settings())
    history.add_request(
        ts=1.0, service="paddleocr-vl", model="local-ocr", capability="ocr", source="gateway",
        latency_ms=120.0, status="ok", error_code=None, pages=2, input_bytes=123, output_chars=24,
    )
    row = history.recent("paddleocr-vl")[0]
    assert row["pages"] == 2 and row["input_bytes"] == 123 and row["output_chars"] == 24
    assert not {"filename", "text", "markdown", "data", "tokens", "cost"} & row.keys()
    with pytest.raises(TypeError):
        history.add_request(text="must never be stored")
    assert history.recent("third") == []


def test_daily_units_latency_unknown_usage_and_retention(tmp_path):
    history = ServiceStore(tmp_path / "admin.sqlite", Settings())
    for ts, status, ms, pages in [(1.0, "ok", 10.0, 2), (2.0, "ok", 30.0, 1), (3.0, "error", None, None)]:
        history.add_request(ts=ts, service="paddleocr-vl", model="local-ocr", capability="ocr",
                            source="admin-ui", latency_ms=ms, status=status, error_code=503 if status == "error" else None,
                            pages=pages, input_bytes=None, output_chars=None)
    day = history.daily("paddleocr-vl", 0, 4, 0)[0]
    assert day["requests"] == 3 and day["errors"] == 1 and day["pages"] == 3
    assert day["input_bytes"] is None and day["output_chars"] is None
    assert day["p50_ms"] == 10.0  # preserve the existing nearest-rank percentile convention
    assert history.prune(3, limit=1) == 1
    assert len(history.recent("paddleocr-vl")) == 2


def test_generic_history_does_not_duplicate_legacy_embedding_requests(tmp_path):
    history = ServiceStore(tmp_path / "admin.sqlite", Settings())
    with pytest.raises(ValueError, match="legacy"):
        history.add_request(ts=1.0, service="qwen-embed", model="qwen3-embed",
                            capability="embeddings", source="gateway", latency_ms=1.0,
                            status="ok", error_code=None, pages=None, input_bytes=None, output_chars=None)
