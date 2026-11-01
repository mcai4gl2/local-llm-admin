"""Atomic gateway admission and lifecycle drain barrier.

The lock covers only state changes. Network work and stream cleanup happen outside it.
An admitted request owns its lease through backend resolution and upstream close.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


class AdmissionRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DrainTimeout(Exception):
    """An admitted request remained active past the drain deadline."""


class DrainCancelled(Exception):
    """An operator cancelled the wait before any transition began."""


@dataclass
class _Lease:
    backend: str | None = None


class Admission:
    """One process-local barrier; job ownership is established by JobRunner first."""

    def __init__(self, limits: dict[str, int]) -> None:
        self._limits = dict(limits)
        self._lock = threading.Lock()
        self._leases: dict[str, _Lease] = {}
        self._state = "accepting"
        self._previous_state = "accepting"
        self._owner: str | None = None
        self._action: str | None = None
        self._started: float | None = None
        self._started_mono: float | None = None
        self._deadline: float | None = None
        self._deadline_mono: float | None = None
        self._drained_mono: float | None = None
        self._cancelled = False
        self._unverified_native_cleanup = 0

    def acquire(self, *, busy: bool = False, request_id: str | None = None) -> str:
        with self._lock:
            if self._state != "accepting" or busy:
                raise AdmissionRefused(503, "Inference is temporarily unavailable during an engine operation")
            if len(self._leases) >= max(self._limits.values()):
                raise AdmissionRefused(429, "Shared inference is busy")
            lease = request_id or uuid.uuid4().hex
            if lease in self._leases:
                raise AdmissionRefused(429, "Shared inference is busy")
            self._leases[lease] = _Lease()
            return lease

    def bind(self, lease: str, backend: str) -> None:
        with self._lock:
            item = self._leases[lease]
            limit = self._limits.get(backend, min(self._limits.values()))
            active = sum(i.backend == backend for i in self._leases.values())
            if item.backend != backend and active >= limit:
                raise AdmissionRefused(429, "Selected engine is busy")
            item.backend = backend

    def release(self, lease: str) -> None:
        with self._lock:
            if self._leases.pop(lease, None) is not None and not self._leases:
                self._drained_mono = time.monotonic()

    def reserve(self, action: str, timeout_s: float) -> str:
        with self._lock:
            if self._state not in ("accepting", "unavailable"):
                raise AdmissionRefused(409, "An engine drain is already in progress")
            owner = uuid.uuid4().hex
            now = time.monotonic()
            self._owner = owner
            self._previous_state = self._state
            self._action = action
            self._state = "draining"
            self._started = time.time()
            self._started_mono = now
            self._deadline_mono = now + timeout_s
            self._deadline = self._started + timeout_s
            self._drained_mono = now if not self._leases else None
            self._cancelled = False
            return owner

    async def wait(self, owner: str) -> None:
        while True:
            with self._lock:
                if self._owner != owner or self._state != "draining":
                    raise DrainCancelled()
                if self._cancelled:
                    raise DrainCancelled()
                assert self._deadline_mono is not None
                if self._drained_mono is not None and self._drained_mono <= self._deadline_mono:
                    return
                if time.monotonic() >= self._deadline_mono:
                    raise DrainTimeout()
                remaining = self._deadline_mono - time.monotonic()
            await asyncio.sleep(min(0.05, remaining))

    def transition(self, owner: str) -> None:
        with self._lock:
            if self._owner != owner or self._state != "draining" or self._cancelled:
                raise DrainCancelled()
            assert self._deadline_mono is not None
            if self._leases or self._drained_mono is None or self._drained_mono > self._deadline_mono:
                raise DrainTimeout()
            self._state = "transitioning"

    def cancel(self) -> bool:
        with self._lock:
            if self._state != "draining":
                return False
            self._cancelled = True
            return True

    def finish(self, owner: str, *, safe: bool, restore: bool = False) -> None:
        with self._lock:
            if self._owner != owner:
                return
            if restore or self._state == "draining":
                self._state = self._previous_state
            else:
                self._state = "accepting" if safe else "unavailable"
            self._owner = None
            self._action = None
            self._started = None
            self._started_mono = None
            self._deadline = None
            self._deadline_mono = None
            self._drained_mono = None
            self._cancelled = False

    def mark_native_cleanup_unverified(self) -> None:
        with self._lock:
            self._unverified_native_cleanup += 1

    def mark_unavailable(self) -> None:
        """Restore a recorded unsafe outcome before listeners accept requests."""
        with self._lock:
            if self._owner is None:
                self._state = "unavailable"

    def open_after_startup_reconciliation(self) -> None:
        """Open the listener only after persisted jobs and gate state were checked."""
        with self._lock:
            if self._owner is None:
                self._state = "accepting"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                "state": self._state,
                "action": self._action,
                "active": len(self._leases),
                "started": self._started,
                "elapsed_s": round(now - self._started_mono, 2) if self._started_mono else None,
                "deadline": self._deadline,
                "cancelled": self._cancelled,
                "unverified_native_cleanup": self._unverified_native_cleanup,
                "coverage": "shared gateway requests only; direct native and admin chat are not counted",
            }
