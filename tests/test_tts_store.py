"""M7: TTS request history (counts only, never the text)."""

from local_llm_admin.tts_store import TtsStore

T0 = 1_790_000_000.0
DAY = 86400


def _add(store, ts, latency=1000.0, status="ok", source="gateway", client=None, chars=100):
    store.add_request(
        ts=ts,
        source=source,
        chars=chars,
        chunks=1,
        voice="Ryan",
        fmt="wav",
        audio_s=6.0,
        latency_ms=latency,
        status=status,
        error_code=None if status == "ok" else 502,
        client_id=client,
    )


def test_daily_and_recent(tmp_path):
    store = TtsStore(tmp_path / "a.sqlite")
    _add(store, T0, 1000)
    _add(store, T0 + 1, 3000)
    _add(store, T0 + 2, status="error")
    (day,) = store.daily(T0 - DAY, T0 + DAY, -480)
    assert day["requests"] == 3 and day["errors"] == 1 and day["chars"] == 300
    assert day["audio_s"] == 12.0 and day["p50_ms"] in (1000.0, 3000.0)
    assert len(store.recent()) == 3 and "input" not in store.recent()[0]


def test_usage_by_client(tmp_path):
    store = TtsStore(tmp_path / "a.sqlite")
    _add(store, T0, client="1")
    _add(store, T0, source="admin-ui")
    _add(store, T0)
    rows = {r["client"]: r for r in store.usage(T0 - 1, T0 + 1, -480, "client", {"1": "litellm"})}
    assert set(rows) == {"litellm", "(admin page)", "(no key)"}


def test_prune(tmp_path):
    store = TtsStore(tmp_path / "a.sqlite")
    _add(store, T0 - 200 * DAY)
    _add(store, T0)
    assert store.prune(T0 - DAY) == 1 and len(store.recent()) == 1
