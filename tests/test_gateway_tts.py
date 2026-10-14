"""M7: the LAN gateway's speech route."""

import io
import wave

import httpx
import pytest

from local_llm_admin.decision import DecisionClient
from local_llm_admin.decision_keys import ClientKeys
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.gateway import create_gateway_app
from local_llm_admin.sidecars import SidecarRegistry
from local_llm_admin.tts import TtsClient
from local_llm_admin.tts_store import TtsStore

BODY = {"model": "qwen3-tts", "input": "Hello there. How are you?", "voice": "Ryan"}


@pytest.fixture
async def gw(fake_tts, fake_decision):
    s = fake_tts.settings
    db = s.admin_data_dir / "admin.sqlite"
    keys = ClientKeys(db)
    tts = TtsClient(s, transport=fake_tts.transport)
    app = create_gateway_app(
        s,
        DecisionClient(s, transport=fake_decision.transport),
        keys,
        DecisionStore(db),
        registry=SidecarRegistry(s),
        tts=tts,
        tts_history=TtsStore(db),
    )
    app.state.test_tts = tts
    alice = keys.create("alice")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 5555)), base_url="http://gw"
    ) as c:
        c.headers["Authorization"] = f"Bearer {alice['key']}"
        c.app = app
        yield c


async def test_speech_returns_wav_and_records_usage_without_text(gw, fake_tts):
    fake_tts.start()
    r = await gw.post("/v1/audio/speech", json=BODY)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnframes() > 0
    assert float(r.headers["x-audio-seconds"]) > 0
    rows = TtsStore(fake_tts.settings.admin_data_dir / "admin.sqlite").recent()
    assert (
        rows[0]["status"] == "ok"
        and rows[0]["client_id"] is not None
        and rows[0]["chars"] == len(BODY["input"])
    )
    raw = (fake_tts.settings.admin_data_dir / "admin.sqlite").read_bytes()
    assert b"How are you" not in raw


async def test_speech_503_names_running_sidecar(gw, fake_decision):
    fake_decision.start()
    r = await gw.post("/v1/audio/speech", json=BODY)
    assert r.status_code == 503 and r.json()["detail"]["running"] == "openjev"
    r = await gw.post("/v1/audio/speech", json=BODY)
    assert r.json()["detail"]["error_type"] == "unavailable"


async def test_speech_503_when_nothing_runs(gw):
    r = await gw.post("/v1/audio/speech", json=BODY)
    assert r.status_code == 503 and r.json()["detail"]["running"] is None


async def test_busy_slot_is_429_with_retry_after(gw, fake_tts):
    fake_tts.start()
    gw.app.state.test_tts.try_acquire(5000)
    try:
        r = await gw.post("/v1/audio/speech", json=BODY)
    finally:
        gw.app.state.test_tts.release()
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


async def test_client_char_cap(gw, fake_tts):
    fake_tts.start()
    fake_tts.settings.tts_client_max_input_chars = 5
    r = await gw.post("/v1/audio/speech", json=BODY)
    assert r.status_code == 400 and "5 characters" in r.json()["detail"]["message"]


async def test_bad_body_and_size(gw, fake_tts):
    fake_tts.start()
    assert (await gw.post("/v1/audio/speech", content=b"{not json")).status_code == 400
    big = {"model": "qwen3-tts", "input": "x" * 70_000}
    assert (await gw.post("/v1/audio/speech", json=big)).status_code == 413


async def test_models_lists_the_running_sidecar(gw, fake_tts):
    assert (await gw.get("/v1/models")).status_code == 503
    fake_tts.start()
    r = await gw.get("/v1/models")
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "qwen3-tts"


async def test_systemone_503_names_tts(gw, fake_tts):
    fake_tts.start()
    r = await gw.post("/v1/systemone", json={"state": "x", "questions": {"q": {"type": "noul"}}})
    assert r.status_code == 503 and r.json()["detail"]["running"] == "qwen-tts"
