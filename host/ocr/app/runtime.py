"""Asynchronous supervisor for one persistent, terminable OCR worker."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


class RuntimeError_(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def owned_module() -> Any:
    path = Path(__file__).with_name("owned.py")
    spec = importlib.util.spec_from_file_location("ocr_owned_processes", path)
    if spec is None or spec.loader is None:
        raise ValueError("missing ownership helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


async def no_vlm() -> bool:
    return True


class Runtime:
    def __init__(
        self, root: Path, config: dict[str, Any], *,
        worker_argv: list[str] | None = None,
        stop_vlm: Callable[[], Awaitable[bool]] = no_vlm,
    ) -> None:
        self.root, self.config = root, config
        self.worker_argv = worker_argv or [sys.executable, "-I", str(root / "app/worker.py")]
        self.stop_vlm = stop_vlm
        self.worker: asyncio.subprocess.Process | None = None
        self.ready = False
        self.active: str | None = None
        self._uncertain = False
        self._aborted = False
        self._lock = asyncio.Lock()
        self._abort_lock = asyncio.Lock()
        self._requests: dict[str, asyncio.Event] = {}
        self.owned = owned_module()

    @property
    def busy(self) -> bool:
        return bool(self._requests) or self._uncertain

    async def start(self) -> None:
        self.worker = await asyncio.create_subprocess_exec(
            *self.worker_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
            limit=self.config["limits"]["max_output_bytes"] + 65536,
        )
        try:
            try:
                await asyncio.to_thread(self.owned.write_record, self.root / "run/pipeline.json",
                                        self.worker.pid, str(self.root))
            except Exception:
                # Our direct, unreaped child has not received any work or spawned native children.
                self.worker.terminate()
                try:
                    await asyncio.wait_for(self.worker.wait(), 3)
                except TimeoutError:
                    self.worker.kill()
                    await asyncio.wait_for(self.worker.wait(), 3)
                self.worker = None
                raise
            assert self.worker.stdin is not None and self.worker.stdout is not None
            self.worker.stdin.write(json.dumps(self.config).encode() + b"\n")
            await self.worker.stdin.drain()
            event = await asyncio.wait_for(self.worker.stdout.readline(),
                                           self.config.get("ready_timeout_s", 240.0))
            body = json.loads(event)
            if body != {"event": "ready", "warmup": True}:
                raise ValueError("warmup failed")
            self.ready = True
        except Exception:
            await self.abort()
            raise RuntimeError_(503, "OCR pipeline initialization failed") from None

    async def _stop_worker(self) -> bool:
        if self.worker is None:
            return True
        stopped = await asyncio.to_thread(self.owned.stop_record, self.root / "run/pipeline.json")
        if stopped:
            try:
                await asyncio.wait_for(self.worker.wait(), 3.0)
            except TimeoutError:
                return False
            self.worker = None
        return bool(stopped)

    async def abort(self) -> bool:
        async with self._abort_lock:
            if self._aborted:
                return not self._uncertain
            self.ready = False
            self._uncertain = True
            worker_stopped = await self._stop_worker()
            vlm_stopped = await self.stop_vlm()
            self._uncertain = not (worker_stopped and vlm_stopped)
            self._aborted = True
            return not self._uncertain

    async def close(self) -> None:
        await self.abort()

    async def cancel(self, request_id: str) -> bool:
        event = self._requests.get(request_id)
        if event is not None:
            event.set()
        if self.active == request_id or self._uncertain:
            return await self.abort()
        return True  # this ID owns no active generation

    async def _acquire(self, cancelled: asyncio.Event) -> None:
        lock = asyncio.create_task(self._lock.acquire())
        cancellation = asyncio.create_task(cancelled.wait())
        acquired = False
        try:
            done, _ = await asyncio.wait(
                (lock, cancellation), timeout=self.config["limits"]["queue_timeout_s"],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lock in done and not cancelled.is_set():
                acquired = True
                return
            if cancelled.is_set():
                raise RuntimeError_(499, "OCR request cancelled")
            raise RuntimeError_(429, "OCR queue wait deadline exceeded")
        finally:
            if not acquired:
                if lock.done() and not lock.cancelled() and lock.exception() is None:
                    self._lock.release()
                else:
                    lock.cancel()
            cancellation.cancel()
            await asyncio.gather(lock, cancellation, return_exceptions=True)

    async def recognize(self, request_id: str, path: Path, media_type: str) -> dict[str, Any]:
        if not self.ready or self._uncertain:
            raise RuntimeError_(503, "OCR pipeline is unavailable")
        if request_id in self._requests or len(self._requests) >= self.config["limits"]["queue_depth"] + 1:
            raise RuntimeError_(429, "OCR queue is full")
        cancelled = asyncio.Event()
        self._requests[request_id] = cancelled
        acquired = False
        try:
            await self._acquire(cancelled)
            acquired = True
            if not self.ready or self._uncertain:
                raise RuntimeError_(503, "OCR pipeline is unavailable")
            self.active = request_id
            try:
                assert self.worker is not None and self.worker.stdin is not None and self.worker.stdout is not None
                self.worker.stdin.write(json.dumps(
                    {"request_id": request_id, "path": str(path), "media_type": media_type},
                ).encode() + b"\n")
                await self.worker.stdin.drain()
                deadline = time.monotonic() + self.config["limits"]["document_timeout_s"]
                page_deadline = time.monotonic() + self.config["limits"]["page_timeout_s"]
                pages = 0
                while True:
                    remaining = min(deadline, page_deadline) - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    line = await asyncio.wait_for(self.worker.stdout.readline(), remaining)
                    event = json.loads(line)
                    if event.get("request_id") != request_id:
                        raise ValueError("unexpected worker response")
                    if event.get("event") == "page" and event.get("number") == pages + 1:
                        pages += 1
                        page_deadline = time.monotonic() + self.config["limits"]["page_timeout_s"]
                    elif event.get("event") == "result":
                        result = event["result"]
                        if result.get("complete") is not True or not pages or len(result["pages"]) != pages:
                            raise ValueError("incomplete document")
                        return dict(result)
                    elif event.get("event") == "error":
                        code = event.get("code")
                        raise RuntimeError_(code if code in (400, 413, 422) else 502,
                                            "OCR document processing failed")
                    else:
                        raise ValueError("invalid worker event")
            except TimeoutError:
                await self.abort()
                raise RuntimeError_(504, "OCR document deadline exceeded") from None
            except asyncio.CancelledError:
                await self.abort()
                raise
            except RuntimeError_ as exc:
                if exc.code >= 500:
                    await self.abort()
                raise
            except Exception:
                await self.abort()
                raise RuntimeError_(502, "OCR worker failed or returned invalid output") from None
        finally:
            self._requests.pop(request_id, None)
            if acquired:
                self.active = None
                self._lock.release()
