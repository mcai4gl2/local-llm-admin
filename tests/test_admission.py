"""The lifecycle barrier and request leases share one atomic state."""

from __future__ import annotations

import asyncio

import pytest

from local_llm_admin.admission import Admission, AdmissionRefused, DrainCancelled, DrainTimeout


async def test_request_racing_drain_cannot_dispatch_after_barrier() -> None:
    gate = Admission({"mlx": 2})
    first = gate.acquire()
    owner = gate.reserve("switch", 0.5)
    with pytest.raises(AdmissionRefused):
        gate.acquire()
    waiting = asyncio.create_task(gate.wait(owner))
    await asyncio.sleep(0)
    assert not waiting.done()
    gate.release(first)
    await waiting
    gate.transition(owner)
    assert gate.snapshot()["state"] == "transitioning"
    with pytest.raises(AdmissionRefused):
        gate.acquire()
    gate.finish(owner, safe=True)
    assert gate.snapshot()["state"] == "accepting"


async def test_resolution_slot_counts_and_backend_limit() -> None:
    gate = Admission({"mlx": 1, "strata": 3})
    first = gate.acquire()
    gate.bind(first, "mlx")
    second = gate.acquire()
    with pytest.raises(AdmissionRefused) as exc:
        gate.bind(second, "mlx")
    assert exc.value.status == 429
    gate.release(second)
    assert gate.snapshot()["active"] == 1
    gate.release(first)
    gate.release(first)
    assert gate.snapshot()["active"] == 0


async def test_timeout_preserves_admission_and_source() -> None:
    gate = Admission({"mlx": 2})
    lease = gate.acquire()
    owner = gate.reserve("stop", 0.01)
    with pytest.raises(DrainTimeout):
        await gate.wait(owner)
    gate.finish(owner, safe=True)
    assert gate.snapshot()["state"] == "accepting"
    gate.release(lease)


async def test_cancel_wait_and_competing_operations() -> None:
    gate = Admission({"mlx": 2})
    lease = gate.acquire()
    owner = gate.reserve("switch", 1)
    with pytest.raises(AdmissionRefused):
        gate.reserve("stop", 1)
    assert gate.cancel() is True
    with pytest.raises(DrainCancelled):
        await gate.wait(owner)
    gate.finish(owner, safe=True)
    gate.release(lease)
    assert gate.cancel() is False


async def test_late_cleanup_cannot_turn_timeout_into_transition() -> None:
    gate = Admission({"mlx": 2})
    lease = gate.acquire()
    owner = gate.reserve("switch", 0.01)
    await asyncio.sleep(0.02)
    gate.release(lease)
    with pytest.raises(DrainTimeout):
        await gate.wait(owner)
    gate.finish(owner, safe=True)


async def test_unavailable_gate_allows_verified_recovery_action() -> None:
    gate = Admission({"mlx": 2})
    first = gate.reserve("switch", 1)
    await gate.wait(first)
    gate.transition(first)
    gate.finish(first, safe=False)
    with pytest.raises(AdmissionRefused):
        gate.acquire()
    recovery = gate.reserve("start", 1)
    await gate.wait(recovery)
    gate.transition(recovery)
    gate.finish(recovery, safe=True)
    gate.release(gate.acquire())


async def test_stale_owner_cannot_transition_or_reopen_another_operation() -> None:
    gate = Admission({"mlx": 2})
    lease = gate.acquire()
    owner = gate.reserve("switch", 1)
    with pytest.raises(DrainTimeout):
        gate.transition(owner)
    assert gate.cancel() is True
    with pytest.raises(DrainCancelled):
        gate.transition(owner)
    gate.finish(owner, safe=True)
    with pytest.raises(DrainCancelled):
        await gate.wait(owner)
    next_owner = gate.reserve("stop", 1)
    gate.finish(owner, safe=True)
    assert gate.snapshot()["state"] == "draining"
    gate.release(lease)
    await gate.wait(next_owner)
    gate.transition(next_owner)
    gate.finish(next_owner, safe=True)


async def test_cancelled_recovery_wait_keeps_prior_unavailable_state() -> None:
    gate = Admission({"mlx": 2})
    owner = gate.reserve("switch", 1)
    await gate.wait(owner)
    gate.transition(owner)
    gate.finish(owner, safe=False)
    recovery = gate.reserve("start", 1)
    assert gate.cancel() is True
    with pytest.raises(DrainCancelled):
        await gate.wait(recovery)
    gate.finish(recovery, safe=True, restore=True)
    assert gate.snapshot()["state"] == "unavailable"


async def test_startup_restores_unavailable_gate() -> None:
    gate = Admission({"mlx": 2})
    in_progress = gate.reserve("switch", 1)
    gate.mark_unavailable()
    gate.open_after_startup_reconciliation()
    assert gate.snapshot()["state"] == "draining"
    gate.finish(in_progress, safe=True)
    gate.mark_unavailable()
    with pytest.raises(AdmissionRefused):
        gate.acquire()
    gate.open_after_startup_reconciliation()
    gate.release(gate.acquire())
    gate.mark_unavailable()
    owner = gate.reserve("start", 1)
    await gate.wait(owner)
    gate.transition(owner)
    gate.finish(owner, safe=True)
    assert gate.snapshot()["state"] == "accepting"
