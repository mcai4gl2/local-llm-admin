"""M8: embeddings usage rows (counts only)."""

import time

from local_llm_admin.embed_store import EmbedStore


def _add(store, ts, **over):
    row = dict(
        ts=ts,
        source="gateway",
        route="embed",
        inputs=2,
        chars=40,
        tokens=12,
        dims=1024,
        latency_ms=50.0,
        status="ok",
        error_code=None,
        client_id="1",
        remote_addr="10.0.0.2",
    )
    row.update(over)
    store.add_request(**row)


def test_rows_never_hold_text(tmp_path):
    store = EmbedStore(tmp_path / "admin.sqlite")
    _add(store, time.time())
    with store._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(embed_requests)")]
    assert not {"text", "input", "query", "documents", "vector"} & set(cols)


def test_daily_usage_and_prune(tmp_path):
    store = EmbedStore(tmp_path / "admin.sqlite")
    now = time.time()
    _add(store, now)
    _add(store, now, route="rerank", inputs=10, latency_ms=150.0)
    _add(store, now, status="error", error_code=429, latency_ms=None, client_id=None)
    _add(store, now - 200 * 86400)
    day = store.daily(now - 86400, now + 1, 0)[-1]
    assert day["requests"] == 3 and day["errors"] == 1 and day["inputs"] == 14
    assert day["p50_ms"] == 50.0  # nearest-rank (decision_store._percentile): round(0.5) == 0
    by_client = {
        r["client"]: r for r in store.usage(now - 86400, now + 1, 0, "client", {"1": "cortex"})
    }
    assert by_client["cortex"]["requests"] == 2 and by_client["(no key)"]["errors"] == 1
    assert store.recent(10)[0]["route"] in ("embed", "rerank")
    assert store.prune(now - 100 * 86400) == 1
