import asyncio
import os
import subprocess

import pytest

from local_llm_admin import metrics
from local_llm_admin.config import Settings

IOREG = """+-o AGXAcceleratorG16X  <class AGXAcceleratorG16X>
    {
      "PerformanceStatistics" = {"In use system memory"=123,"Device Utilization %"=37,"Renderer Utilization %"=35}
    }
"""


def test_parse_ioreg_gpu():
    assert metrics.parse_ioreg_gpu(IOREG) == 37.0
    assert metrics.parse_ioreg_gpu("nothing here") is None


@pytest.mark.parametrize(
    ("raw", "out"), [("1", "normal"), ("2", "warn"), ("4", "critical"), ("x", None), ("9", None)]
)
def test_parse_pressure(raw, out):
    assert metrics.parse_pressure(raw) == out


def test_ring_buffer_keeps_last_n_and_windows():
    rb = metrics.RingBuffer(3)
    for t in range(5):
        rb.append({"ts": float(t)})
    assert [p["ts"] for p in rb.items()] == [2.0, 3.0, 4.0]
    assert [p["ts"] for p in rb.since(3.0)] == [3.0, 4.0]
    assert rb.latest() == {"ts": 4.0}
    assert metrics.RingBuffer(2).latest() is None


def test_window_seconds():
    assert metrics.window_seconds("15m") == 900
    assert metrics.window_seconds("1h") == 3600
    with pytest.raises(ValueError):
        metrics.window_seconds("7d")


def _fake_run(outputs):
    def run(argv, **kw):
        out = outputs.get(argv[0], "")
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return run


def _sampler(tmp_path, platform="darwin", pid=None, outputs=None, clock=None):
    s = Settings(mlx_root=tmp_path, metrics_history_points=10)
    t = iter(clock or [100.0, 105.0, 110.0, 115.0])
    return metrics.Sampler(
        s,
        mlx_pid=lambda: pid,
        run=_fake_run(outputs or {"sysctl": "2\n", "ioreg": IOREG}),
        platform=platform,
        clock=lambda: next(t),
    )


def test_sample_has_core_fields_and_mac_extras(tmp_path):
    smp = _sampler(tmp_path, pid=os.getpid())
    p = smp.sample()
    assert p["ts"] == 100.0
    assert 0 <= p["cpu_percent"] <= 100
    assert isinstance(p["cpu_per_core"], list) and p["cpu_per_core"]
    assert len(p["load"]) == 3
    assert p["mem_total"] > 0 and p["mem_available"] > 0
    assert p["pressure"] == "warn"
    assert p["gpu_util"] == 37.0
    assert {d["path"] for d in p["disks"]} == {"/", str(tmp_path)}
    root = next(d for d in p["disks"] if d["path"] == "/")
    assert root["total"] > 0
    assert p["mlx"]["rss"] > 0
    # first sample has no deltas
    assert p["net_rx_bps"] is None and p["disk_read_bps"] is None
    p2 = smp.sample()
    assert p2["net_rx_bps"] is not None and p2["net_rx_bps"] >= 0
    assert p2["disk_read_bps"] is None or p2["disk_read_bps"] >= 0


def test_sample_off_mac_skips_sysctl_and_ioreg(tmp_path):
    called = []

    def run(argv, **kw):
        called.append(argv)
        raise AssertionError("should not run")

    s = Settings(mlx_root=tmp_path / "missing")
    smp = metrics.Sampler(s, mlx_pid=lambda: None, run=run, platform="linux", clock=lambda: 1.0)
    p = smp.sample()
    assert called == []
    assert p["pressure"] is None and p["gpu_util"] is None and p["mlx"] is None
    missing = next(d for d in p["disks"] if d["path"] == str(tmp_path / "missing"))
    assert missing == {
        "path": str(tmp_path / "missing"),
        "mounted": False,
        "total": None,
        "used": None,
        "percent": None,
    }


def test_sample_tolerates_failing_commands_and_dead_pid(tmp_path):
    def run(argv, **kw):
        raise FileNotFoundError(argv[0])

    s = Settings(mlx_root=tmp_path)
    smp = metrics.Sampler(
        s, mlx_pid=lambda: 999999999, run=run, platform="darwin", clock=lambda: 1.0
    )
    p = smp.sample()
    assert p["pressure"] is None and p["gpu_util"] is None and p["mlx"] is None


async def test_start_collects_into_history_and_stop(tmp_path):
    s = Settings(mlx_root=tmp_path, metrics_interval_s=0.01, metrics_history_points=50)
    smp = metrics.Sampler(s, mlx_pid=lambda: None, platform="linux")
    smp.start()
    smp.start()  # idempotent
    await asyncio.sleep(0.08)
    await smp.stop()
    n = len(smp.buffer.items())
    assert n >= 2
    assert smp.history("15m") == smp.buffer.items()
    await asyncio.sleep(0.03)
    assert len(smp.buffer.items()) == n
    await smp.stop()


def test_current_samples_when_empty(tmp_path):
    smp = _sampler(tmp_path, platform="linux")
    assert smp.current()["ts"] == 100.0
    assert smp.current()["ts"] == 100.0  # served from buffer


def test_sampler_primes_cpu_percent_so_first_sample_is_real(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(metrics.psutil, "cpu_percent", lambda *a, **kw: calls.append(kw) or 0.0)
    metrics.Sampler(Settings(mlx_root=tmp_path), mlx_pid=lambda: None, platform="linux")
    assert {"percpu": True} in calls and len(calls) == 2


def test_recorded_ioreg_output():
    from pathlib import Path

    text = (Path(__file__).parent / "fixtures/mac/ioreg-accelerator.txt").read_text()
    assert metrics.parse_ioreg_gpu(text) == 0.0
