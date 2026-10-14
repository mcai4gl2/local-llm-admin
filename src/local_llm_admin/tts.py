"""The Qwen3-TTS sidecar adapter (M7): request checks, text chunking, audio joining and
the client. The sidecar is driven only through tts/bin/qwen-tts and its loopback HTTP
API; the admin never imports mlx-audio. Long input is split at sentence and paragraph
boundaries, synthesized chunk by chunk with the same voice, and joined (D-S10).

The upstream contract is mlx-audio 0.5.7's ``mlx_audio.server`` as measured in Phase 0
(plan record, 2026-10-04): the style field is ``instruct``; ``speed`` is accepted but
ignored by Qwen3-TTS; only WAV and PCM come back non-empty; an unknown voice or empty
input still returns 200 audio, so the admin checks both. Requests stream (one complete
WAV per segment, back to back) because that keeps the sidecar's peak memory flat at
~6.5 GB instead of growing ~0.2 GB per second of audio, and they carry the model's own
sampling settings and a bounded ``max_tokens``: with the server defaults long chunks
sometimes never stop."""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .config import Settings

MAX_BODY_BYTES = 64 * 1024
MAX_INSTRUCTIONS = 1000
FORMATS = {"wav": "audio/wav", "mp3": "audio/mpeg"}
VoiceName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

_ENDS = frozenset(".!?;。！？；")
_CLAUSE = frozenset(",，、:：")
_CLOSERS = frozenset("\"'”’)]）」』")

# Chunk size is a weight, not a length: Qwen3-TTS reads Chinese at ~3 chars/s and English
# at ~15, so one CJK character weighs as much speech as ~4 Latin ones (Phase 0).
CJK_WEIGHT = 4
_CJK = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\U00020000-\U0002fa1f]"
)
CODEC_HZ = 12.5  # codec frames (= generated tokens) per second of audio
TOKENS_PER_WEIGHT = 1.8  # measured ~0.9 for EN and ZH; twice that before a chunk is cut off
TOKENS_SLACK = 100
# generation_config.json of Qwen3-TTS-12Hz-1.7B-CustomVoice; mlx-audio's server defaults
# (0.7 / 0.95 / 40 / 1.0) loop on long chunks
SAMPLING = {"temperature": 0.9, "top_p": 1.0, "top_k": 50, "repetition_penalty": 1.05}


def text_weight(text: str) -> int:
    return len(text) + (CJK_WEIGHT - 1) * len(_CJK.findall(text))


def max_tokens_for(text: str) -> int:
    """The per-chunk token cap: about twice the speech the text needs, so a chunk that
    never ends is stopped (and refused) instead of running to the server's 8k tokens."""
    return math.ceil(text_weight(text) * TOKENS_PER_WEIGHT) + TOKENS_SLACK


class TtsError(Exception):
    """A refused or failed speech request; ``code`` is the HTTP status the routes return."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SpeechRequest(BaseModel):
    """OpenAI ``POST /v1/audio/speech``, narrowed: no voice cloning, WAV or MP3 only."""

    model_config = ConfigDict(extra="forbid")

    model: str
    input: Annotated[str, StringConstraints(min_length=1)]
    voice: VoiceName | None = None
    instructions: Annotated[str, StringConstraints(max_length=MAX_INSTRUCTIONS)] | None = None
    response_format: Literal["wav", "mp3"] = "wav"
    # OpenAI clients send it; Qwen3-TTS ignores it (Phase 0), so only 1.0 is honest
    speed: Annotated[float, Field(ge=0.5, le=2.0)] | None = None


def validate_speech(body: Any, settings: Settings, max_chars: int) -> dict[str, Any]:
    """The normalised request, or TtsError(400) naming the first problem."""
    try:
        size = len(json.dumps(body))
    except (TypeError, ValueError):
        raise TtsError(400, "body is not JSON") from None
    if size > MAX_BODY_BYTES:
        raise TtsError(400, f"request is larger than {MAX_BODY_BYTES} bytes")
    try:
        req = SpeechRequest.model_validate(body)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"]) or "body"
        raise TtsError(400, f"{where}: {err['msg']}") from None
    if req.model != settings.tts_model_id:
        raise TtsError(400, f"model: only {settings.tts_model_id} is served")
    if req.speed is not None and req.speed != 1.0:
        raise TtsError(400, "speed: only 1.0 is supported by this model")
    if len(req.input) > max_chars:
        raise TtsError(400, f"input is longer than {max_chars} characters")
    voices = settings.tts_voice_list
    voice = req.voice or settings.tts_default_voice or None
    if voice is None:
        raise TtsError(400, "voice: required (no default voice is configured)")
    if voices and voice not in voices:
        raise TtsError(400, f"voice: {voice} is not one of {', '.join(voices)}")
    out: dict[str, Any] = req.model_dump(exclude_none=True)
    out["voice"] = voice
    return out


def _cut(text: str, marks: frozenset[str]) -> list[str]:
    """Split after each run of ``marks`` (and any closing quotes); nothing is dropped."""
    pieces: list[str] = []
    start = i = 0
    n = len(text)
    while i < n:
        if text[i] in marks:
            j = i + 1
            while j < n and (text[j] in marks or text[j] in _CLOSERS):
                j += 1
            pieces.append(text[start:j])
            start = i = j
        else:
            i += 1
    if start < n:
        pieces.append(text[start:])
    return pieces


def _head(word: str, max_w: int) -> int:
    """How many leading characters of ``word`` fit in ``max_w`` (at least one)."""
    total = 0
    for i, ch in enumerate(word):
        total += text_weight(ch)
        if total > max_w:
            return max(i, 1)
    return len(word)


def _words(text: str, max_chars: int) -> list[str]:
    """Pack words; a word heavier than a chunk (or unspaced CJK) is cut hard."""
    out: list[str] = []
    cur = ""
    for w in text.split(" "):
        while text_weight(w) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            k = _head(w, max_chars)
            out.append(w[:k])
            w = w[k:]
        cand = f"{cur} {w}" if cur else w
        if text_weight(cand) <= max_chars:
            cur = cand
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def _units(para: str, max_chars: int) -> list[str]:
    units: list[str] = []
    for sentence in _cut(para, _ENDS):
        sentence = sentence.strip()
        if not sentence:
            continue
        if text_weight(sentence) <= max_chars:
            units.append(sentence)
            continue
        for clause in _cut(sentence, _CLAUSE):
            clause = clause.strip()
            if clause:
                units.extend(
                    [clause] if text_weight(clause) <= max_chars else _words(clause, max_chars)
                )
    return units


def split_text(text: str, max_chars: int) -> list[str]:
    """Chunks weighing at most ``max_chars`` (``text_weight``): paragraphs first, then
    sentences, clauses, words.

    Whitespace is normalised (runs become one space; blank lines separate paragraphs);
    every other character is kept, in order.
    """
    chunks: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        for k, unit in enumerate(_units(para, max_chars)):
            sep = ("\n\n" if k == 0 else " ") if cur else ""
            if text_weight(cur) + len(sep) + text_weight(unit) <= max_chars:
                cur += sep + unit
            else:
                chunks.append(cur)
                cur = unit
    if cur:
        chunks.append(cur)
    return chunks


def split_wav_stream(body: bytes) -> list[bytes]:
    """mlx-audio's streamed body: complete WAV files back to back, each sized by its RIFF
    header. Anything else (JSON, a cut-off file, trailing bytes) is a 502."""
    parts: list[bytes] = []
    i = 0
    while i < len(body):
        if body[i : i + 4] != b"RIFF" or len(body) - i < 8:
            raise TtsError(502, "the TTS sidecar returned something that is not WAV audio")
        end = i + 8 + int.from_bytes(body[i + 4 : i + 8], "little")
        if end > len(body):
            raise TtsError(502, "the TTS sidecar returned a cut-off WAV segment")
        parts.append(body[i:end])
        i = end
    if not parts:
        raise TtsError(502, "the TTS sidecar returned no audio")
    return parts


def join_wav(parts: list[bytes]) -> bytes:
    """One WAV from chunk WAVs; every chunk must share channels, width and rate."""
    params: tuple[int, int, int] | None = None
    frames: list[bytes] = []
    for part in parts:
        try:
            with wave.open(io.BytesIO(part), "rb") as r:
                got = (r.getnchannels(), r.getsampwidth(), r.getframerate())
                data = r.readframes(r.getnframes())
        except (wave.Error, EOFError):
            raise TtsError(
                502, "the TTS sidecar returned something that is not WAV audio"
            ) from None
        if params is None:
            params = got
        elif got != params:
            raise TtsError(502, f"TTS chunk format {got} differs from {params}")
        frames.append(data)
    if params is None:
        raise TtsError(502, "the TTS sidecar returned no audio")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(b"".join(frames))
    return out.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as r:
        return r.getnframes() / float(r.getframerate())


def encode_mp3(wav: bytes, ffmpeg: Path, timeout_s: float) -> bytes:
    """The joined WAV as MP3, encoded once (upstream MP3 chunks are never concatenated)."""
    try:
        r = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-f",
                "mp3",
                "-q:a",
                "2",
                "pipe:1",
            ],
            input=wav,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TtsError(502, f"MP3 encoding failed: {exc}") from None
    if r.returncode != 0 or not r.stdout:
        raise TtsError(502, "MP3 encoding failed: " + r.stderr.decode(errors="replace")[:200])
    return r.stdout


def timeout_for(weight: int, settings: Settings) -> float:
    """Seconds for a whole request of ``text_weight`` = ``weight``."""
    return settings.tts_timeout_base_s + weight * settings.tts_timeout_per_char_s


def upstream_body(req: dict[str, Any], settings: Settings, text: str) -> dict[str, Any]:
    """One chunk's request to mlx-audio, in its field names (Phase 0). ``speed`` is never
    sent: Qwen3-TTS ignores it."""
    body: dict[str, Any] = {
        "model": settings.tts_upstream_model,
        "input": text,
        "voice": req["voice"],
        "response_format": "wav",
        "stream": True,
        "max_tokens": max_tokens_for(text),
        **SAMPLING,
    }
    if "instructions" in req:
        body["instruct"] = req["instructions"]
    return body


def upstream_error(status: int, body: Any) -> TtsError:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("error_type") or "error")
    elif isinstance(detail, str):
        message = detail
    else:
        message = f"upstream returned HTTP {status}"
    if status in (401, 403):
        return TtsError(502, "the TTS sidecar refused the admin's key")
    if status in (429, 503):
        return TtsError(429, f"the TTS sidecar is busy: {message}")
    if 400 <= status < 500:
        return TtsError(400, message)
    return TtsError(502, f"the TTS sidecar failed (HTTP {status})")


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes
    media_type: str
    audio_s: float
    chunks: int
    latency_ms: float


class TtsClient:
    """Talks to the sidecar on loopback. One request at a time (``try_acquire``), shared by
    the admin page and the gateway: mlx-audio synthesizes serially (D-S11)."""

    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._lock = threading.Lock()
        self._busy_chars: int | None = None
        self._busy_since = 0.0

    def try_acquire(self, chars: int) -> bool:
        with self._lock:
            if self._busy_chars is not None:
                return False
            self._busy_chars = chars
            self._busy_since = time.monotonic()
            return True

    def release(self) -> None:
        with self._lock:
            self._busy_chars = None

    def retry_after_s(self) -> int:
        """Seconds until the request in flight should finish, from its length."""
        with self._lock:
            chars, since = self._busy_chars, self._busy_since
        if chars is None:
            return 1
        left = chars * self.settings.tts_timeout_per_char_s - (time.monotonic() - since)
        return max(1, math.ceil(left))

    def _headers(self) -> dict[str, str]:
        key = self.settings.tts_api_key.get_secret_value()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _chunk(self, http: httpx.AsyncClient, req: dict[str, Any], text: str) -> bytes:
        resp = await http.post(
            "/v1/audio/speech",
            json=upstream_body(req, self.settings, text),
            headers=self._headers(),
        )
        if resp.status_code != 200:
            try:
                data = resp.json()
            except ValueError:
                data = None
            raise upstream_error(resp.status_code, data)
        wav = join_wav(split_wav_stream(resp.content))
        # audio as long as the token cap means the model never ended the text
        if wav_seconds(wav) >= max_tokens_for(text) / CODEC_HZ - 0.5:
            raise TtsError(
                502, "the TTS sidecar ran a chunk to its token cap without finishing the text"
            )
        return wav

    async def speak(self, req: dict[str, Any]) -> SpeechResult:
        """Synthesize an already-validated request; the caller holds the slot. A failed
        chunk fails the whole request: partial audio is never returned."""
        chunks = split_text(req["input"], self.settings.tts_chunk_chars)
        if not chunks:
            raise TtsError(400, "input has no speakable text")
        total = timeout_for(text_weight(req["input"]), self.settings)
        t0 = time.perf_counter()
        parts: list[bytes] = []
        try:
            async with asyncio.timeout(total):
                async with httpx.AsyncClient(
                    base_url=self.settings.tts_api_base, timeout=total, transport=self._transport
                ) as http:
                    for text in chunks:
                        parts.append(await self._chunk(http, req, text))
        except (TimeoutError, httpx.TimeoutException):
            raise TtsError(504, f"the TTS sidecar did not finish within {total:.0f} s") from None
        except httpx.HTTPError:
            raise TtsError(503, "the TTS sidecar is not reachable") from None
        wav = join_wav(parts)
        seconds = wav_seconds(wav)
        audio = wav
        if req["response_format"] == "mp3":
            audio = await asyncio.to_thread(encode_mp3, wav, self.settings.tts_ffmpeg, 120.0)
        return SpeechResult(
            audio,
            FORMATS[req["response_format"]],
            seconds,
            len(chunks),
            round((time.perf_counter() - t0) * 1000, 1),
        )

    def warmup_sync(self) -> bool:
        """One short synthesis, so a start job only succeeds once speech really works."""
        voice = self.settings.tts_default_voice or (self.settings.tts_voice_list or [""])[0]
        req = {"voice": voice, "response_format": "wav"}
        try:
            with httpx.Client(
                base_url=self.settings.tts_api_base, timeout=60.0, transport=self._transport
            ) as http:
                r = http.post(
                    "/v1/audio/speech",
                    json=upstream_body(req, self.settings, "Hi."),
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        try:
            join_wav(split_wav_stream(r.content))
        except TtsError:
            return False
        return True
