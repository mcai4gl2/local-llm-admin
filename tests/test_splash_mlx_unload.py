"""Resource lifetime regression: free arrays before purging allocator cache."""

from __future__ import annotations

import gc
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops/splash-phase0"))
import mlx_unload  # noqa: E402

# Small ownership reproduction, not an emulation of GPU arithmetic.
SOURCE = b"""def unload_model_sync():
    registry = get_registry()
    unloaded_any = False
    for cache_group, _ in list(registry.items()):
        unloaded_any = unload_group(cache_group) or unloaded_any
    gc.collect()
    mx.clear_cache()
    return unloaded_any
"""


class Allocator:
    def __init__(self) -> None:
        self.active = 0
        self.cached = 0

    def clear_cache(self) -> None:
        self.cached = 0


class Buffer:
    def __init__(self, allocator: Allocator) -> None:
        self.allocator = allocator
        allocator.active += 100

    def __del__(self) -> None:
        self.allocator.active -= 100
        self.allocator.cached += 100


def execute(
    source: bytes, count: int = 1, retain_worker: bool = False
) -> tuple[int, int, list[bool]]:
    allocator = Allocator()
    registry = {str(i): {"model": Buffer(allocator)} for i in range(count)}
    worker = list(registry.values()) if retain_worker else None
    namespace: dict[str, Any] = {
        "get_registry": lambda: registry,
        "unload_group": lambda k: bool(registry.pop(k)),
        "gc": gc,
        "mx": allocator,
    }
    exec(compile(source, "<unload-lifetime-repro>", "exec"), namespace)
    outcomes = [namespace["unload_model_sync"](), namespace["unload_model_sync"]()]
    # Keep the optional worker reference alive until after measuring allocations.
    result = allocator.active, allocator.cached, outcomes
    del worker
    return result


@pytest.mark.parametrize("count", [1, 2, 4])
def test_original_reproduces_and_fixed_reclaims(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    assert execute(SOURCE, count)[1] == 0  # a later duplicate purge hides the bug
    # Measure immediately after a single unload as the actual endpoint does.
    allocator = Allocator()
    registry = {str(i): {"model": Buffer(allocator)} for i in range(count)}
    namespace = {
        "get_registry": lambda: registry,
        "unload_group": lambda k: bool(registry.pop(k)),
        "gc": gc,
        "mx": allocator,
    }
    exec(compile(SOURCE, "<original>", "exec"), namespace)
    namespace["unload_model_sync"]()
    assert not registry and allocator.active == 0 and allocator.cached == 100
    monkeypatch.setattr(mlx_unload, "APP_SHA256", hashlib.sha256(SOURCE).hexdigest())
    fixed = mlx_unload.patch_app(SOURCE)
    # One immediate unload must suffice; the following test observes before idempotence.
    allocator = Allocator()
    registry = {str(i): {"model": Buffer(allocator)} for i in range(count)}
    namespace = {
        "get_registry": lambda: registry,
        "unload_group": lambda k: bool(registry.pop(k)),
        "gc": gc,
        "mx": allocator,
    }
    exec(compile(fixed, "<fixed>", "exec"), namespace)
    assert namespace["unload_model_sync"]() is True
    assert allocator.active == allocator.cached == 0
    assert namespace["unload_model_sync"]() is False


def test_empty_and_live_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mlx_unload, "APP_SHA256", hashlib.sha256(SOURCE).hexdigest())
    fixed = mlx_unload.patch_app(SOURCE)
    assert execute(fixed, 0) == (0, 0, [False, False])
    assert execute(fixed, 1, True) == (100, 0, [True, False])


def test_refuse_source_drift() -> None:
    with pytest.raises(ValueError, match="source"):
        mlx_unload.patch_app(SOURCE)


@pytest.mark.parametrize(
    "source",
    [b"pass\n", SOURCE + SOURCE, SOURCE.replace(b"def unload_model_sync():", b"invalid syntax")],
)
def test_refuse_missing_ambiguous_invalid_source(
    monkeypatch: pytest.MonkeyPatch, source: bytes
) -> None:
    monkeypatch.setattr(mlx_unload, "APP_SHA256", hashlib.sha256(source).hexdigest())
    with pytest.raises((ValueError, SyntaxError)):
        mlx_unload.patch_app(source)


def test_prepare_new_bundle_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mlx_unload, "APP_SHA256", hashlib.sha256(SOURCE).hexdigest())
    source = tmp_path / "original.py"
    source.write_bytes(SOURCE)
    out = tmp_path / "isolated"
    manifest = mlx_unload.prepare(source, out)
    assert source.read_bytes() == SOURCE
    assert (out / "app.py").read_bytes() == mlx_unload.patch_app(SOURCE)
    assert json.loads((out / "manifest.json").read_text()) == manifest
    assert manifest["source_sha256"] != manifest["patched_sha256"]
    assert str(tmp_path) not in json.dumps(manifest)
    with pytest.raises(FileExistsError):
        mlx_unload.prepare(source, out)
