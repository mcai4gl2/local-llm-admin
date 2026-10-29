from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import psutil

from local_llm_admin.metrics import BundleSampler

from .test_splash_ingestion import observation


def test_bundle_metrics_verify_generations_and_never_sum_reused_or_denied_pids() -> None:
    table = {100: [1.0, 1000, "/runtime/python"], 101: [2.0, 3000, "/runtime/native"]}

    def process(pid: int) -> Any:
        if table[pid][1] is None:
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(
            pid=pid,
            create_time=lambda: table[pid][0],
            exe=lambda: table[pid][2],
            memory_info=lambda: SimpleNamespace(rss=table[pid][1]),
            cpu_percent=lambda *_: 10.0,
        )

    sampler = BundleSampler(process=process)
    result = sampler.sample(observation())
    assert result["rss"] == 4000 and result["scope"] == "sum_process_rss"
    assert result["cpu_percent"] is None  # priming is not a measured zero
    assert sampler.sample(observation())["cpu_percent"] == 20
    table[101][0] = 3.0  # same PID, replacement child
    partial = sampler.sample(observation())
    assert partial["state"] == "partial" and partial["rss"] is None
    assert partial["observed_rss"] == 1000
    table[101][1] = None
    assert sampler.sample(observation())["state"] == "partial"
    assert sampler.sample(None)["state"] == "absent"


def test_duplicate_pids_unknown_ownership_and_replacement_during_measurement() -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    obs = observation()
    version = [1.0]

    def process(pid: int) -> Any:
        def memory() -> Any:
            version[0] = 9.0
            return SimpleNamespace(rss=1000)

        return SimpleNamespace(
            pid=pid,
            create_time=lambda: version[0],
            exe=lambda: "/runtime/python",
            memory_info=memory,
            cpu_percent=lambda _: 0,
        )

    sampler = BundleSampler(process=process)
    assert sampler.sample(replace(obs, ownership="unknown"))["state"] == "unknown"
    replaced = sampler.sample(replace(obs, processes=obs.processes[:1]))
    assert replaced["rss"] is None and replaced["state"] == "partial"

    def stable(pid: int) -> Any:
        return SimpleNamespace(
            pid=pid,
            create_time=lambda: 1.0,
            exe=lambda: "/runtime/python",
            memory_info=lambda: SimpleNamespace(rss=1000),
            cpu_percent=lambda _: 0,
        )

    duplicate = BundleSampler(process=stable).sample(
        replace(obs, processes=(obs.processes[0], obs.processes[0]))
    )
    assert duplicate["observed_rss"] == 1000 and duplicate["rss"] is None


def test_sampler_preserves_primary_and_bundle_scopes_and_unknown_states(tmp_path: Any) -> None:
    from dataclasses import replace

    from local_llm_admin.config import Settings
    from local_llm_admin.metrics import Sampler

    obs = [observation()]

    def process(pid: int) -> Any:
        return SimpleNamespace(
            pid=pid,
            create_time=lambda: 1.0 if pid == 100 else 2.0,
            exe=lambda: "/runtime/python" if pid == 100 else "/runtime/native",
            memory_info=lambda: SimpleNamespace(rss=1000),
            cpu_percent=lambda _: 0,
        )

    sampler = Sampler(Settings(mlx_root=tmp_path), lambda: None, engine_observation=lambda: obs[0])
    sampler._bundle = BundleSampler(process=process)
    point = sampler.sample()
    assert point["mlx"]["rss"] == 1000
    assert point["engine_bundle"]["rss"] == 2000
    obs[0] = replace(observation(), ownership="unknown")
    point = sampler.sample()
    assert point["mlx"] is None and point["engine_bundle"]["state"] == "unknown"
