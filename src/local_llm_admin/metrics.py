"""Background system sampler with an in-memory ring buffer (no DB in v1)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import psutil

from .config import Settings

log = logging.getLogger(__name__)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
_GPU_RE = re.compile(r'"Device Utilization %"\s*=\s*(\d+(?:\.\d+)?)')
_PRESSURE = {1: "normal", 2: "warn", 4: "critical"}
_WINDOWS = {"15m": 900, "1h": 3600}


def parse_ioreg_gpu(text: str) -> float | None:
    m = _GPU_RE.search(text)
    return float(m.group(1)) if m else None


def parse_pressure(raw: str) -> str | None:
    try:
        return _PRESSURE.get(int(raw.strip()))
    except ValueError:
        return None


def window_seconds(window: str) -> int:
    if window not in _WINDOWS:
        raise ValueError(f"window must be one of {sorted(_WINDOWS)}")
    return _WINDOWS[window]


class RingBuffer:
    def __init__(self, size: int) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=size)

    def append(self, item: dict[str, Any]) -> None:
        self._items.append(item)

    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def since(self, ts: float) -> list[dict[str, Any]]:
        return [p for p in self._items if p["ts"] >= ts]

    def latest(self) -> dict[str, Any] | None:
        return self._items[-1] if self._items else None


class Sampler:
    def __init__(
        self,
        settings: Settings,
        mlx_pid: Callable[[], int | None],
        run: Runner = subprocess.run,
        platform: str = sys.platform,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.buffer = RingBuffer(settings.metrics_history_points)
        self._mlx_pid = mlx_pid
        self._run = run
        self._mac = platform == "darwin"
        self._clock = clock
        self._prev: tuple[float, Any, Any] | None = None
        self._proc: psutil.Process | None = None
        self._task: asyncio.Task[None] | None = None
        # cpu_percent(None) measures since the previous call; the first call returns 0.0
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)

    def _cmd(self, argv: list[str]) -> str | None:
        try:
            proc = self._run(argv, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout if proc.returncode == 0 else None

    def _disks(self) -> list[dict[str, Any]]:
        out = []
        for path in ("/", str(self.settings.mlx_root)):
            entry: dict[str, Any] = {"path": path, "mounted": os.path.ismount(path)}
            try:
                du = psutil.disk_usage(path)
                entry.update(total=du.total, used=du.used, percent=du.percent)
            except OSError:
                entry.update(total=None, used=None, percent=None)
            out.append(entry)
        return out

    def _mlx_proc(self) -> dict[str, Any] | None:
        pid = self._mlx_pid()
        if pid is None:
            self._proc = None
            return None
        try:
            if self._proc is None or self._proc.pid != pid:
                self._proc = psutil.Process(pid)
                self._proc.cpu_percent(None)
            with self._proc.oneshot():
                return {
                    "pid": pid,
                    "rss": self._proc.memory_info().rss,
                    "cpu_percent": self._proc.cpu_percent(None),
                }
        except psutil.Error:
            self._proc = None
            return None

    def sample(self) -> dict[str, Any]:
        ts = self._clock()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        disk_io = psutil.disk_io_counters()
        net_io = psutil.net_io_counters()
        rates: dict[str, float | None] = dict.fromkeys(
            ("disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps")
        )
        if self._prev is not None and ts > self._prev[0]:
            dt = ts - self._prev[0]
            pd, pn = self._prev[1], self._prev[2]
            if disk_io and pd:
                rates["disk_read_bps"] = max(0.0, (disk_io.read_bytes - pd.read_bytes) / dt)
                rates["disk_write_bps"] = max(0.0, (disk_io.write_bytes - pd.write_bytes) / dt)
            if net_io and pn:
                rates["net_rx_bps"] = max(0.0, (net_io.bytes_recv - pn.bytes_recv) / dt)
                rates["net_tx_bps"] = max(0.0, (net_io.bytes_sent - pn.bytes_sent) / dt)
        self._prev = (ts, disk_io, net_io)
        pressure = gpu = None
        if self._mac:
            raw = self._cmd(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
            pressure = parse_pressure(raw) if raw else None
            io = self._cmd(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"])
            gpu = parse_ioreg_gpu(io) if io else None
        return {
            "ts": ts,
            "cpu_percent": psutil.cpu_percent(None),
            "cpu_per_core": psutil.cpu_percent(None, percpu=True),
            "load": list(os.getloadavg()),
            "mem_total": vm.total,
            "mem_used": vm.used,
            "mem_available": vm.available,
            "mem_percent": vm.percent,
            "mem_wired": getattr(vm, "wired", None),
            "swap_used": sw.used,
            "swap_percent": sw.percent,
            "pressure": pressure,
            "gpu_util": gpu,
            "disks": self._disks(),
            **rates,
            "mlx": self._mlx_proc(),
        }

    def current(self) -> dict[str, Any]:
        latest = self.buffer.latest()
        if latest is None:
            latest = self.sample()
            self.buffer.append(latest)
        return latest

    def history(self, window: str) -> list[dict[str, Any]]:
        return self.buffer.since(self._clock() - window_seconds(window))

    async def _loop(self) -> None:
        while True:
            try:
                self.buffer.append(await asyncio.to_thread(self.sample))
            except Exception:  # keep sampling; one bad read must not stop the loop
                log.exception("metrics sample failed")
            await asyncio.sleep(self.settings.metrics_interval_s)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
