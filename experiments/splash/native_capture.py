"""Private, bounded native token capture; standard library only for isolated runtimes.

Timestamp at the caller's engine-output boundary, buffer in memory, and write once
after independent final accounting. This module never owns an inference process.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn


class CaptureError(RuntimeError):
    """A failed or unsupported trace; no successful timing artifact is emitted."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _ids(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or not value:
        raise CaptureError("Explicit nonempty token IDs are required.")
    result = list(value)
    if any(type(token) is not int or token < 0 for token in result):
        raise CaptureError("Explicit integer token IDs are required.")
    return result


@dataclasses.dataclass
class _Capture:
    # Hold the object so its id cannot be reused before finalization.
    key: object
    backend: str
    request_sha256: str
    request_json: str
    limit: int
    file_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    batches: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    finalized_ids: list[int] = dataclasses.field(default_factory=list)
    emitted_count: int = 0


class Collector:
    """One sequential native request per isolated process; refuses output reuse."""

    def __init__(self, directory: Path, runtime_sha256: str, adapter_sha256: str) -> None:
        if not all(re.fullmatch(r"[a-f0-9]{64}", h) for h in (runtime_sha256, adapter_sha256)):
            raise ValueError("Pinned digests are required.")
        directory = directory.absolute()
        missing = []
        parent = directory.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700)
        directory.mkdir(mode=0o700, exist_ok=False)
        self.directory = directory
        self.runtime_sha256 = runtime_sha256
        self.adapter_sha256 = adapter_sha256
        self.active: dict[int, _Capture] = {}
        self.lock = threading.RLock()

    def _write(self, record: _Capture, suffix: str, payload: dict[str, Any]) -> None:
        path = self.directory / (record.file_id + suffix)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(data + "\n")
            f.flush()
            os.fsync(f.fileno())

    def abort(self, key: object, reason: str = "incomplete") -> None:
        with self.lock:
            record = self.active.pop(id(key), None)
            if record is not None:
                # No exception strings, prompts or IDs in failure diagnostics.
                self._write(
                    record,
                    ".failed.json",
                    {
                        "contract": "splash-native-capture-failure-1",
                        "status": "failed",
                        "backend": record.backend,
                        "request_sha256": record.request_sha256,
                        "reason": reason
                        if reason in {"cancelled", "incomplete"}
                        else "invalid_capture",
                    },
                )

    def _fail(self, key: object) -> NoReturn:
        self.abort(key, "invalid_capture")
        raise CaptureError("Native capture is incomplete or inconsistent.")

    def begin(self, key: object, backend: str, request: Any, limit: int) -> None:
        with self.lock:
            if self.active:
                # Preserve the first request's evidence; do not silently evict it.
                raise CaptureError("Native capture supports sequential requests only.")
            if (
                backend not in {"mlx", "splash"}
                or type(limit) is not int
                or not 2 <= limit <= 32768
            ):
                raise CaptureError("Unsupported backend or completion bound.")
            request_json = json.dumps(
                request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            encoded = request_json.encode()
            if len(encoded) > 4 * 1024**2:
                raise CaptureError("Native request descriptor exceeds the capture bound.")
            self.active[id(key)] = _Capture(
                key, backend, hashlib.sha256(encoded).hexdigest(), request_json, limit
            )

    def batch(self, key: object, tokens: Any, *, at_ns: int) -> None:
        with self.lock:
            record = self.active.get(id(key))
            if record is None:
                raise CaptureError("No native request was registered.")
            try:
                ids = _ids(tokens)
            except CaptureError:
                self._fail(key)
            if (
                type(at_ns) is not int
                or not 0 <= at_ns <= 2**63 - 1
                or (record.batches and at_ns < record.batches[-1]["at_ns"])
                or record.emitted_count + len(ids) > record.limit
            ):
                self._fail(key)
            record.batches.append({"at_ns": at_ns, "token_ids": ids})
            record.emitted_count += len(ids)

    def observe_mlx(self, key: object, item: Any) -> None:
        with self.lock:
            record = self.active.get(id(key))
            if record is None:
                raise CaptureError("No native request was registered.")
            count = getattr(item, "token_count", None)
            finish = getattr(item, "finish_reason", None)
            if type(count) is not int or count not in (0, 1) or (count == 0 and not finish):
                self._fail(key)
            if count == 1:
                token = getattr(item, "token", None)
                if type(token) is not int or token < 0:
                    self._fail(key)
                record.finalized_ids.append(token)
                if len(record.finalized_ids) > record.limit:
                    self._fail(key)
            if finish is not None:
                self.finish(key, record.finalized_ids, len(record.finalized_ids), finish)

    def finish(self, key: object, finalized_ids: Any, count: Any, reason: Any) -> None:
        with self.lock:
            record = self.active.get(id(key))
            if record is None:
                raise CaptureError("No native request was registered.")
            try:
                ids = _ids(finalized_ids)
            except CaptureError:
                self._fail(key)
            emitted = [token for batch in record.batches for token in batch["token_ids"]]
            if (
                type(count) is not int
                or count != len(ids)
                or ids != emitted
                or reason not in {"length", "stop"}
                or len(record.batches) < 2
                or record.batches[-1]["at_ns"] <= record.batches[0]["at_ns"]
            ):
                self._fail(key)
            self._write(
                record,
                ".request.json",
                {
                    "contract": "splash-native-request-1",
                    "backend": record.backend,
                    "request_sha256": record.request_sha256,
                    "native_request": json.loads(record.request_json),
                },
            )
            self._write(
                record,
                ".trace.json",
                {
                    "contract": "splash-native-emission-1",
                    "backend": record.backend,
                    "runtime_sha256": self.runtime_sha256,
                    "adapter_sha256": self.adapter_sha256,
                    "request_sha256": record.request_sha256,
                    "source": "engine_callback_entry",
                    "clock": "monotonic_ns",
                    "completion_tokens": count,
                    "completion_ids_sha256": digest(ids),
                    "finish_reason": reason,
                    "batches": record.batches,
                },
            )
            del self.active[id(key)]


_collector: Collector | None = None


def configure(backend: str, runtime_sha256: str, adapter_sha256: str) -> None:
    global _collector
    if _collector is not None or backend not in {"mlx", "splash"}:
        raise CaptureError("Capture must be configured once per isolated runtime.")
    output = os.environ.get("PHASE0_NATIVE_OUT")
    if not output or not Path(output).is_absolute():
        raise CaptureError("Set an absolute, new private capture directory.")
    _collector = Collector(Path(output), runtime_sha256, adapter_sha256)


def current() -> Collector:
    if _collector is None:
        raise CaptureError("Isolated capture was not configured.")
    return _collector


def begin_mlx(
    key: object,
    prompt: str,
    args: Any,
    images: Any,
    audio: Any,
    videos: Any,
    draft_model: Any,
    diffusion: bool,
) -> None:
    if (
        diffusion
        or images
        or audio
        or videos
        or draft_model is not None
        or args.logits_processors is not None
        or args.diffusion_kwargs()
    ):
        raise CaptureError("Only ordinary text generation without an MLX draft is supported.")
    current().begin(
        key,
        "mlx",
        {"rendered_prompt": prompt, "arguments": dataclasses.asdict(args)},
        args.max_tokens,
    )


def begin_splash(job: Any) -> None:
    if (
        job.image_pixels
        or job.image_spans
        or job.score_tokens
        or job.stop_sequences
        or job.constraint is not None
    ):
        raise CaptureError("Native timing requires unmasked text without stop sequences.")
    current().begin(
        job,
        "splash",
        {
            "prompt_tokens": list(job.prompt_tokens),
            "max_new_tokens": job.max_new_tokens,
            "seed": job.seed,
            "sampling": dataclasses.asdict(job.sampling),
            "thinking": job.thinking,
            "thinking_token_budget": job.thinking_token_budget,
            "thinking_display": job.thinking_display,
            "cache_salt": job.cache_salt,
            "flags": int(job.flags),
        },
        job.max_new_tokens,
    )


def mlx_batch(key: object, token: int) -> None:
    at_ns = time.monotonic_ns()
    current().batch(key, [token], at_ns=at_ns)


def splash_batch(key: object, tokens: Any, at_ns: int) -> None:
    current().batch(key, tokens, at_ns=at_ns)


def observe_mlx(key: object, item: Any) -> None:
    current().observe_mlx(key, item)


def abort(key: object) -> None:
    try:
        current().abort(key)
    except OSError:
        # Incomplete evidence stays ineligible; preserve the runtime cancel path.
        return


def finish_splash(state: Any, result: Any, error: Any) -> None:
    try:
        if error is not None or result is None or state.job.cancelled.is_set() or result.bounded:
            current().abort(state.job)
            return
        current().finish(
            state.job, list(state.streamer.token_ids), result.completion_tokens, result.reason
        )
    except (CaptureError, OSError):
        # Missing or failed artifacts invalidate capture. Preserve response delivery
        # and the runtime finalizer even when private storage becomes unavailable.
        return
