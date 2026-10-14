"""M7: TTS request checks and text chunking (pure)."""

import io
import random
import re
import wave

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.tts import (
    TtsError,
    join_wav,
    split_text,
    timeout_for,
    validate_speech,
    wav_seconds,
)

from .conftest import _wav


@pytest.fixture
def s():
    return Settings(_env_file=None, tts_voices="Vivian,Ryan", tts_default_voice="Vivian")


def test_valid_body_is_normalised(s):
    out = validate_speech({"model": "qwen3-tts", "input": "Hi.", "voice": "Ryan"}, s, 10_000)
    assert out == {"model": "qwen3-tts", "input": "Hi.", "voice": "Ryan", "response_format": "wav"}


def test_voice_defaults_and_is_checked(s):
    assert validate_speech({"model": "qwen3-tts", "input": "Hi."}, s, 100)["voice"] == "Vivian"
    with pytest.raises(TtsError, match="voice"):
        validate_speech({"model": "qwen3-tts", "input": "Hi.", "voice": "Bob"}, s, 100)


def test_any_valid_voice_when_no_list():
    s = Settings(_env_file=None, tts_voices="", tts_default_voice="")
    assert (
        validate_speech({"model": "qwen3-tts", "input": "x", "voice": "Uncle_Fu"}, s, 10)["voice"]
        == "Uncle_Fu"
    )
    with pytest.raises(TtsError):
        validate_speech({"model": "qwen3-tts", "input": "x", "voice": "a b"}, s, 10)
    with pytest.raises(TtsError, match="voice"):
        validate_speech({"model": "qwen3-tts", "input": "x"}, s, 10)


@pytest.mark.parametrize(
    ("over", "text"),
    [
        ({"model": "tts-1"}, "model"),
        ({"input": ""}, "input"),
        ({"input": "x" * 101}, "100 characters"),
        ({"instructions": "y" * 1001}, "instructions"),
        ({"response_format": "opus"}, "response_format"),
        ({"speed": 3}, "speed"),
        ({"extra": 1}, "extra"),
    ],
)
def test_refusals_are_400(s, over, text):
    body = {"model": "qwen3-tts", "input": "Hi.", "voice": "Ryan", **over}
    with pytest.raises(TtsError) as e:
        validate_speech(body, s, 100)
    assert e.value.code == 400 and text in e.value.message


def test_body_size_limit(s):
    with pytest.raises(TtsError, match="larger than"):
        validate_speech({"model": "qwen3-tts", "input": "x" * 70_000}, s, 100_000)


def _squash(t):
    return re.sub(r"\s+", "", t)


def test_split_text_properties():
    random.seed(7)
    alphabet = "abc def. ghi, jkl! 你好。世界，测试！ \n\n x?” y; Supercalifragilistic "
    for _ in range(2000):
        text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 400)))
        m = random.randint(5, 60)
        chunks = split_text(text, m)
        assert _squash("".join(chunks)) == _squash(text)
        assert all(0 < len(c) <= m and c == c.strip() for c in chunks)


def test_split_prefers_sentences_and_paragraphs():
    assert split_text(
        "Hello there. How are you?\n\nNew para here, with a clause! 你好。世界！", 20
    ) == [
        "Hello there.",
        "How are you?",
        "New para here,",
        "with a clause!",  # + " 你好。" would weigh 24 (CJK counts 4)
        "你好。 世界！",
    ]
    assert split_text("One. Two.\n\nThree.", 100) == ["One. Two.\n\nThree."]


def test_split_unpunctuated_cjk_and_long_words():
    assert split_text("一" * 25, 40) == ["一" * 10, "一" * 10, "一" * 5]
    assert split_text("a" * 25 + " bb", 10) == ["a" * 10, "a" * 10, "aaaaa bb"]


def test_ten_thousand_chars_split_under_the_cap():
    text = ("This is a sentence of moderate length, with a clause. " * 200)[:10_000]
    chunks = split_text(text, 1000)
    assert len(chunks) >= 10 and all(len(c) <= 1000 for c in chunks)


def test_join_concatenates_frames():
    out = join_wav([_wav(100), _wav(50)])
    with wave.open(io.BytesIO(out)) as r:
        assert r.getnframes() == 150 and r.getframerate() == 24000
    assert wav_seconds(out) == pytest.approx(150 / 24000)


def test_join_rejects_mismatched_formats():
    with pytest.raises(TtsError) as e:
        join_wav([_wav(10), _wav(10, rate=16000)])
    assert e.value.code == 502


def test_join_rejects_non_wav():
    with pytest.raises(TtsError) as e:
        join_wav([b'{"error": "x"}'])
    assert e.value.code == 502


def test_timeout_scales_with_length(s):
    assert timeout_for(0, s) == s.tts_timeout_base_s
    assert timeout_for(10_000, s) == pytest.approx(
        s.tts_timeout_base_s + 10_000 * s.tts_timeout_per_char_s
    )


# --- the mlx-audio 0.5.7 contract recorded in Phase 0 (2026-10-04) ---


def test_cjk_weighs_more_than_latin():
    from local_llm_admin.tts import CJK_WEIGHT, text_weight

    assert text_weight("ab 你好。") == 3 + 2 * CJK_WEIGHT + 1
    assert text_weight("こんにちは한국") == 7 * CJK_WEIGHT


def test_split_limit_is_a_weight():
    from local_llm_admin.tts import CJK_WEIGHT, text_weight

    assert split_text("一" * 25, 10 * CJK_WEIGHT) == ["一" * 10, "一" * 10, "一" * 5]
    text = "老灯塔看守人每天傍晚都会爬上螺旋楼梯。" * 40
    chunks = split_text(text, 200)
    assert all(text_weight(c) <= 200 for c in chunks) and _squash("".join(chunks)) == text


def test_split_weight_properties():
    from local_llm_admin.tts import text_weight

    random.seed(11)
    alphabet = "abc def. 你好。世界，测试！ \n\n x? 一二三四五六七八九十 "
    for _ in range(1000):
        text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 300)))
        m = random.randint(8, 80)
        chunks = split_text(text, m)
        assert _squash("".join(chunks)) == _squash(text)
        assert all(0 < text_weight(c) <= m for c in chunks)


def test_speed_other_than_one_is_refused(s):
    body = {"model": "qwen3-tts", "input": "Hi.", "voice": "Ryan"}
    assert validate_speech({**body, "speed": 1.0}, s, 100)["input"] == "Hi."
    with pytest.raises(TtsError) as e:
        validate_speech({**body, "speed": 1.25}, s, 100)
    assert e.value.code == 400 and "speed" in e.value.message


def test_upstream_body_uses_mlx_audio_names_and_bounds_tokens(s):
    from local_llm_admin.tts import max_tokens_for, text_weight, upstream_body

    req = {"voice": "Ryan", "instructions": "calm", "speed": 1.0, "response_format": "mp3"}
    body = upstream_body(req, s, "Hello. 你好。")
    assert body["instruct"] == "calm" and "instructions" not in body and "speed" not in body
    assert body["stream"] is True and body["response_format"] == "wav"
    assert (body["temperature"], body["top_p"], body["top_k"], body["repetition_penalty"]) == (
        0.9,
        1.0,
        50,
        1.05,
    )
    assert body["max_tokens"] == max_tokens_for("Hello. 你好。")
    assert max_tokens_for("x" * 800) > 800 * 0.9  # room for slow speech, still bounded
    assert max_tokens_for("x" * 800) < 800 * 2 + 200
    assert max_tokens_for("你" * 200) == max_tokens_for("x" * text_weight("你" * 200))


def test_split_wav_stream():
    from local_llm_admin.tts import split_wav_stream

    a, b = _wav(100), _wav(50)
    assert split_wav_stream(a + b) == [a, b]
    assert split_wav_stream(a) == [a]
    for bad in (b"", b'{"detail": "x"}', a + b"RIFF\x10\x00", a[:-10]):
        with pytest.raises(TtsError) as e:
            split_wav_stream(bad)
        assert e.value.code == 502


def test_phase0_defaults():
    s = Settings(_env_file=None)
    assert s.tts_voice_list == [
        "Vivian",
        "Serena",
        "Uncle_Fu",
        "Dylan",
        "Eric",
        "Ryan",
        "Aiden",
        "Ono_Anna",
        "Sohee",
    ]
    assert s.tts_default_voice == "Vivian" and s.tts_footprint_gb == 8.0
    assert s.tts_chunk_chars == 800 and s.tts_ready_path == "/v1/models"
