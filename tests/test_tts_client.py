"""M7: TtsClient against the fake sidecar."""

import io
import wave

import pytest

from local_llm_admin import tts as tts_mod
from local_llm_admin.tts import TtsClient, TtsError


def _body(text="Hello there. How are you?", **over):
    return {"model": "qwen3-tts", "input": text, "voice": "Ryan", "response_format": "wav", **over}


@pytest.fixture
def client(fake_tts):
    fake_tts.start()
    return TtsClient(fake_tts.settings, transport=fake_tts.transport)


async def test_speak_chunks_in_order_with_the_same_voice(client, fake_tts):
    text = "First sentence here. " * 12
    res = await client.speak(_body(text, instructions="calm"))
    assert res.chunks == len(fake_tts.requests) >= 3 and res.media_type == "audio/wav"
    assert {r["voice"] for r in fake_tts.requests} == {"Ryan"}
    assert all(r["response_format"] == "wav" and r["instruct"] == "calm" for r in fake_tts.requests)
    assert all(r["model"] == fake_tts.settings.tts_upstream_model for r in fake_tts.requests)
    with wave.open(io.BytesIO(res.audio)) as w:
        assert w.getnframes() == sum(len(r["input"]) * 10 for r in fake_tts.requests)
    assert res.audio_s > 0 and res.latency_ms >= 0


async def test_speak_mp3_uses_ffmpeg(client, monkeypatch):
    seen = {}

    def fake_encode(wav, ffmpeg, timeout_s):
        seen["n"] = len(wav)
        return b"ID3mp3"

    monkeypatch.setattr(tts_mod, "encode_mp3", fake_encode)
    res = await client.speak(_body(response_format="mp3"))
    assert res.audio == b"ID3mp3" and res.media_type == "audio/mpeg" and seen["n"] > 44


async def test_speak_non_wav_is_502(client, fake_tts):
    fake_tts.flags["raw"] = b'{"oops": true}'
    with pytest.raises(TtsError) as e:
        await client.speak(_body())
    assert e.value.code == 502


async def test_mismatched_chunk_rates_are_502(client, fake_tts):
    fake_tts.flags["rates"] = {2: 16000}
    with pytest.raises(TtsError) as e:
        await client.speak(_body("One sentence. " * 20))
    assert e.value.code == 502


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, 400), (401, 502), (422, 400), (429, 429), (500, 502), (503, 429)],
)
async def test_upstream_errors_map(client, fake_tts, status, code):
    fake_tts.flags.update(status=status, body={"detail": "bad voice"})
    with pytest.raises(TtsError) as e:
        await client.speak(_body())
    assert e.value.code == code


async def test_timeout_is_504(client, fake_tts):
    fake_tts.flags["timeout"] = True
    with pytest.raises(TtsError) as e:
        await client.speak(_body())
    assert e.value.code == 504


async def test_not_listening_is_503(fake_tts):
    c = TtsClient(fake_tts.settings, transport=fake_tts.transport)
    with pytest.raises(TtsError) as e:
        await c.speak(_body())
    assert e.value.code == 503


def test_slot_is_exclusive_and_estimates_retry(client):
    assert client.try_acquire(1000)
    assert not client.try_acquire(10)
    assert client.retry_after_s() >= 1
    client.release()
    assert client.try_acquire(10)
    client.release()


def test_warmup(client, fake_tts):
    assert client.warmup_sync() is True and fake_tts.requests[-1]["input"] == "Hi."
    fake_tts.flags["status"] = 500
    assert client.warmup_sync() is False


async def test_streamed_segments_are_joined(client, fake_tts):
    fake_tts.flags["segments"] = 3
    res = await client.speak(_body("Hello there."))
    with wave.open(io.BytesIO(res.audio)) as w:
        assert w.getnframes() == len("Hello there.") * 10


async def test_runaway_chunk_is_502(client, fake_tts):
    from local_llm_admin.tts import CODEC_HZ, max_tokens_for

    cap_s = max_tokens_for("Hello there.") / CODEC_HZ
    fake_tts.flags["frames"] = int(cap_s * 24000)
    with pytest.raises(TtsError) as e:
        await client.speak(_body("Hello there."))
    assert e.value.code == 502 and "token cap" in e.value.message
