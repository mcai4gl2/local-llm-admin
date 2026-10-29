"""Independent Splash readiness/telemetry task; supervisor consumes only its cache."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any, Protocol

from .config import Settings
from .engine_telemetry import EngineTelemetryStore


class StatusClient(Protocol):
    async def status(self) -> dict[str, Any]: ...


class SplashMonitor:
    def __init__(
        self,
        settings: Settings,
        client: StatusClient,
        store: EngineTelemetryStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings, self.client, self.store, self.clock = settings, client, store, clock
        self._status: dict[str, Any] = {}
        self._checked: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_prune = 0.0
        self._pending: asyncio.Task[None] | None = None
        self._read_finished = False

    def snapshot(self) -> dict[str, Any]:
        if not self.settings.splash_enabled:
            return {"state": "absent", "ready": None, "enabled": False}
        now = self.clock()
        fresh = (
            self._checked is not None
            and 0 <= now - self._checked <= self.settings.splash_poll_interval_s * 3
        )
        return {
            **self._status,
            "enabled": True,
            "checked_at": self._checked,
            "state": self._status.get("state", "unknown") if fresh else "unknown",
            "ready": self._status.get("ready") if fresh else None,
            "stale": not fresh,
        }

    async def poll(self) -> None:
        if not self.settings.splash_enabled:
            self._status, self._checked = {}, None
            return
        if self._pending is not None and not self._pending.done():
            return  # one collection/worker only, including after a timeout
        self._read_finished = False
        self._pending = asyncio.create_task(self._collect())
        try:
            await asyncio.wait_for(asyncio.shield(self._pending), self.settings.probe_timeout_s * 2)
        except TimeoutError:
            if self._read_finished:
                self._status["telemetry_error"] = "timeout"
            else:
                self._status = {"state": "unknown", "ready": None}
                self._checked = self.clock()

    async def _collect(self) -> None:
        now = self.clock()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.probe_timeout_s * 2
        try:
            status = await self.client.status()
            if loop.time() > deadline:
                raise TimeoutError("status collection expired")
        except Exception:
            self._status = {"state": "unknown", "ready": None}
            self._checked = now
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.store.failure, "unavailable", now=now)
            return
        self._read_finished = True
        self._status = {k: v for k, v in status.items() if k != "telemetry"}
        self._checked = now
        reading = status.get("telemetry")
        if isinstance(reading, dict):
            self._status["epoch"] = reading["epoch"]
            self._status["identity"] = reading["identity"]
        try:
            if isinstance(reading, dict):
                await asyncio.to_thread(
                    self.store.record,
                    reading,
                    now=now,
                    gap_after_s=self.settings.splash_poll_interval_s * 3,
                )
            else:
                await asyncio.to_thread(
                    self.store.failure,
                    "unattributed" if status.get("running") else "absent",
                    now=now,
                )
            if now - self._last_prune >= 86400:
                self._last_prune = now
                for _ in range(10):
                    removed = await asyncio.to_thread(
                        self.store.prune, now - self.settings.observability_retention_days * 86400
                    )
                    if removed < 5000:
                        break
        except Exception:
            self._status["telemetry_error"] = "storage_error"
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.store.failure, "storage_error", now=now)

    async def _loop(self) -> None:
        while True:
            await self.poll()
            await asyncio.sleep(self.settings.splash_poll_interval_s)

    def start(self) -> None:
        if self.settings.splash_enabled and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
            self._pending = None
