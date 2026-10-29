"""Independent bounded collection and reuse of serving-readiness observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.engine_telemetry import EngineTelemetryStore
from local_llm_admin.splash_monitor import SplashMonitor

from .test_splash_ingestion import reading


class Client:
    def __init__(self, status: dict[str, Any]) -> None:
        self.value = status
        self.calls = 0

    async def status(self) -> dict[str, Any]:
        self.calls += 1
        return self.value


async def test_disabled_monitor_schedules_no_http_and_empty_cache(tmp_path: Path) -> None:
    s = Settings(admin_data_dir=tmp_path)
    client = Client({})
    monitor = SplashMonitor(
        s, client, EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: 100
    )
    await monitor.poll()
    assert client.calls == 0 and monitor.snapshot()["state"] == "absent"
    assert monitor.store.readings(0, 200) == []


async def test_poll_stores_bound_data_only_and_readiness_expires(tmp_path: Path) -> None:
    s = Settings(splash_enabled=True)
    now = [100.0]
    client = Client(
        {
            "state": "ready",
            "ready": True,
            "running": True,
            "profile": "stock",
            "fingerprint": "fp-a",
            "telemetry": reading(),
        }
    )
    monitor = SplashMonitor(
        s, client, EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: now[0]
    )
    await monitor.poll()
    assert len(monitor.store.readings(0, 200)) == 1
    assert monitor.snapshot()["ready"] is True
    now[0] += 1
    await monitor.poll()
    assert len(monitor.store.readings(0, 200)) == 1
    now[0] = 1000
    assert monitor.snapshot()["ready"] is None and monitor.snapshot()["state"] == "unknown"
    assert client.calls == 2  # reading cached status adds no HTTP


async def test_unavailable_and_unattributed_readings_never_stop_monitor(tmp_path: Path) -> None:
    s = Settings(splash_enabled=True)
    client = Client({"state": "starting", "ready": False, "running": True})
    monitor = SplashMonitor(
        s, client, EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: 100
    )
    await monitor.poll()
    assert monitor.store.coverage(True, now=101)["error"] == "unattributed"
    assert monitor.store.readings(0, 200) == []

    async def failed() -> dict[str, Any]:
        raise ValueError("private response must not be saved")

    client.status = failed
    monitor.clock = lambda: 102
    await monitor.poll()
    assert monitor.store.coverage(True, now=103)["error"] == "unavailable"
    assert "private" not in str(monitor.snapshot())


async def test_timeout_never_overlaps_workers_and_stop_cancels_background(tmp_path: Path) -> None:
    import asyncio

    settings = Settings(splash_enabled=True, probe_timeout_s=0.005)
    event = asyncio.Event()
    calls = []

    class Slow:
        async def status(self) -> dict[str, Any]:
            calls.append(1)
            await event.wait()
            return {"running": False, "ready": False, "state": "absent"}

    monitor = SplashMonitor(
        settings, Slow(), EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: 100_000
    )
    await monitor.poll()
    assert monitor.snapshot()["ready"] is None
    await monitor.poll()
    assert len(calls) == 1
    monitor.start()
    monitor.start()
    await asyncio.sleep(0)
    await monitor.stop()
    await monitor.stop()
    assert monitor._pending is monitor._task is None
    settings.splash_enabled = False
    monitor.start()
    assert monitor._task is None


async def test_storage_failure_does_not_change_loaded_readiness_and_pruning_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Settings(splash_enabled=True)
    client = Client({"state": "ready", "ready": True, "running": True, "telemetry": reading()})
    monitor = SplashMonitor(
        s, client, EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: 100_000
    )
    await monitor.poll()
    assert monitor._last_prune == 100_000

    def broken(*args: Any, **kw: Any) -> int:
        raise OSError("private path")

    monkeypatch.setattr(monitor.store, "record", broken)
    monitor.clock = lambda: 100_001
    await monitor.poll()
    assert monitor.snapshot()["ready"] is True
    assert monitor.snapshot()["telemetry_error"] == "storage_error"
    assert monitor.store.coverage(True, now=100_001)["error"] == "storage_error"
    await monitor.stop()


async def test_retention_work_is_bounded_even_when_backlog_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(splash_enabled=True)
    client = Client({"state": "ready", "ready": True, "running": True, "telemetry": reading()})
    monitor = SplashMonitor(
        settings, client, EngineTelemetryStore(tmp_path / "a.sqlite"), clock=lambda: 100_000
    )
    calls = []

    def backlog(before: float) -> int:
        calls.append(before)
        return 5000

    monkeypatch.setattr(monitor.store, "prune", backlog)
    await monitor.poll()
    assert len(calls) == 10
    await monitor.stop()


async def test_late_http_result_cannot_turn_timeout_into_success(tmp_path: Path) -> None:
    import asyncio

    release = asyncio.Event()

    class Late:
        async def status(self) -> dict[str, Any]:
            await release.wait()
            return {"state": "ready", "ready": True, "running": True, "telemetry": reading()}

    monitor = SplashMonitor(
        Settings(splash_enabled=True, probe_timeout_s=0.005),
        Late(),
        EngineTelemetryStore(tmp_path / "a.sqlite"),
        clock=lambda: 100,
    )
    await monitor.poll()
    release.set()
    await monitor._pending
    assert monitor.snapshot()["ready"] is None
    assert monitor.store.readings(0, 200) == []
    await monitor.stop()


async def test_storage_timeout_keeps_valid_readiness_and_single_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    release = threading.Event()
    client = Client({"state": "ready", "ready": True, "running": True, "telemetry": reading()})
    monitor = SplashMonitor(
        Settings(splash_enabled=True, probe_timeout_s=0.005),
        client,
        EngineTelemetryStore(tmp_path / "a.sqlite"),
        clock=lambda: 100,
    )

    def blocked(*args: Any, **kwargs: Any) -> int:
        release.wait(2)
        return 0

    monkeypatch.setattr(monitor.store, "record", blocked)
    try:
        await monitor.poll()
        assert monitor.snapshot()["ready"] is True
        assert monitor.snapshot()["telemetry_error"] == "timeout"
        await monitor.poll()
        assert client.calls == 1
    finally:
        release.set()
        await monitor._pending
        await monitor.stop()
