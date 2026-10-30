"""Shared host guard runs without native OCR/admin imports or a live Mac."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.resident_manifest import build_manifest
from local_llm_admin.sidecars import SidecarRegistry

HOST = Path(__file__).resolve().parents[1] / "host"


def guard_module() -> Any:
    path = HOST / "bin/resident-services"
    spec = importlib.util.spec_from_loader("resident_guard_test", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def catalog(tmp_path):
    settings = Settings(
        mlx_root=tmp_path, embed_root=tmp_path / "embed", ocr_root=tmp_path / "ocr",
        decision_root=tmp_path / "decision", tts_root=tmp_path / "tts",
        comfyui_root=tmp_path / "comfyui", strata_root=tmp_path / "strata",
        splash_root=tmp_path / "splash", embed_enabled=True, embed_allowed_mlx="small",
        ocr_enabled=True, ocr_port=8097, ocr_vlm_port=8098, ocr_footprint_gb=12.0,
        ocr_allowed_mlx="small",
    )
    path = tmp_path / "etc/resident-services.json"
    path.parent.mkdir()
    path.write_text(json.dumps(build_manifest(settings, SidecarRegistry(settings))))
    path.chmod(0o600)
    return path


def test_catalog_cli_reports_hash_and_detects_tampering(catalog):
    command = [sys.executable, "-I", str(HOST / "bin/resident-services"), str(catalog), "validate"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["catalog_hash"]
    data = json.loads(catalog.read_text())
    data["owners"][0]["reservation_gb"] = 999
    catalog.write_text(json.dumps(data))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and "manifest" in result.stderr.lower()


def test_guard_sees_orphaned_dependency_and_disabled_owners(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    ocr = next(o for o in manifest["owners"] if o["id"] == "paddleocr-vl")
    monkeypatch.setattr(module, "processes", lambda: [(123, ocr["root"] + "/venv-vlm/bin/python -m mlx_vlm.server")])
    monkeypatch.setattr(module, "port_open", lambda _: False)
    with pytest.raises(module.GuardError, match="paddleocr-vl"):
        module.guard(manifest, "exclusive")
    ocr["enabled"] = False
    with pytest.raises(module.GuardError, match="paddleocr-vl"):
        module.guard(manifest, "mlx", "small")


def test_running_residents_obey_model_policy(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    ocr = next(o for o in manifest["owners"] if o["id"] == "paddleocr-vl")
    ocr["generation_checked"] = False  # isolate compatibility/budget policy from generation validation
    record = Path(ocr["components"][0]["pid_file"])
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("123")
    monkeypatch.setattr(module, "processes", lambda: [(123, ocr["root"] + "/app/server.py")])
    monkeypatch.setattr(module, "port_open", lambda _: False)
    monkeypatch.setattr(module, "_mlx_state", lambda *args: ("small", 2.0))
    monkeypatch.setattr(module, "_sysctl", lambda key: {
        "hw.memsize": 64 * 1024**3, "iogpu.wired_limit_mb": 0,
        "kern.memorystatus_vm_pressure_level": 1,
    }[key])
    module.guard(manifest, "mlx", "small")
    with pytest.raises(module.GuardError, match="not allowed"):
        module.guard(manifest, "mlx", "large")


def test_direct_model_start_counts_target_and_all_reservations(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    monkeypatch.setattr(module, "processes", lambda: [])
    monkeypatch.setattr(module, "_running", lambda owner, _: (owner["category"] == "service", False))
    monkeypatch.setattr(module, "_mlx_state", lambda *args: ("small", 50.0))
    monkeypatch.setattr(module, "_sysctl", lambda key: {
        "hw.memsize": 64 * 1024**3, "iogpu.wired_limit_mb": 0,
        "kern.memorystatus_vm_pressure_level": 1,
    }[key])
    with pytest.raises(module.GuardError, match="memory"):
        module.guard(manifest, "mlx", "small")


def test_shared_engine_port_is_attributed_to_its_actual_listener(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    mlx = manifest["engines"][0]
    monkeypatch.setattr(module, "processes", lambda: [(123, mlx["root"] + "/bin/python -m mlx_vlm.server")])
    monkeypatch.setattr(module, "_running", lambda *_: (False, False))
    monkeypatch.setattr(module, "listener_pids", lambda port: {123} if port == 8080 else set())
    monkeypatch.setattr(module, "_mlx_state", lambda *args: ("small", 2.0))
    monkeypatch.setattr(module, "_sysctl", lambda key: {
        "hw.memsize": 64 * 1024**3, "iogpu.wired_limit_mb": 0,
        "kern.memorystatus_vm_pressure_level": 1,
    }[key])
    module.guard(manifest, "resident", "paddleocr-vl")


def test_port_only_or_unreadable_process_inventory_fails_closed(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    monkeypatch.setattr(module, "processes", lambda: [])
    monkeypatch.setattr(module, "port_open", lambda p: p == 8098)
    with pytest.raises(module.GuardError, match="ownership"):
        module.guard(manifest, "mlx", "small")


def test_missing_manifest_refuses_when_ocr_was_installed(tmp_path):
    module = guard_module()
    path = tmp_path / "etc/resident-services.json"
    (tmp_path / "ocr/run").mkdir(parents=True)
    with pytest.raises(module.GuardError, match="manifest"):
        module.load_manifest(path)


@pytest.mark.parametrize("wrapper,args", [
    ("bin/mlx-model", ["start", "a"]),
    ("comfyui/bin/comfyui", ["start"]),
    ("strata/bin/strata", ["start"]),
    ("embed/bin/qwen-embed", ["start"]),
    ("decision/bin/openjev", ["start"]),
    ("tts/bin/qwen-tts", ["start"]),
])
def test_direct_wrappers_refuse_orphaned_ocr_before_launch(tmp_path, wrapper, args):
    from .host_harness import make_host, redirect
    host = make_host(tmp_path)
    try:
        for rel in ("comfyui/bin/comfyui", "strata/bin/strata", "embed/bin/qwen-embed",
                    "decision/bin/openjev", "tts/bin/qwen-tts"):
            redirect(HOST / rel, host.root / rel, host.root)
        settings = Settings(
            mlx_root=host.root, embed_root=host.root / "embed", ocr_root=host.root / "ocr",
            decision_root=host.root / "decision", tts_root=host.root / "tts",
            comfyui_root=host.root / "comfyui", strata_root=host.root / "strata",
            splash_root=host.root / "splash", embed_enabled=True, decision_enabled=True,
            tts_enabled=True, ocr_port=8097, ocr_vlm_port=8098,
        )
        catalog = host.root / "etc/resident-services.json"
        catalog.write_text(json.dumps(build_manifest(settings, SidecarRegistry(settings))))
        catalog.chmod(0o600)
        child = subprocess.Popen(["bash", "-c", "while :; do sleep 0.1; done",
                                  str(host.root / "ocr/app/worker.py")])
        host.procs.append(child)
        result = subprocess.run(["zsh", str(host.root / wrapper), *args],
                                env=host.env, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert "paddleocr-vl" in result.stderr or "ownership" in result.stderr.lower()
        assert not (host.root / "run/mlx-server.pid").exists()
        assert child.poll() is None
    finally:
        host.cleanup()


def test_direct_slot_start_counts_all_residents_and_refuses_slot_conflicts(catalog, monkeypatch):
    module = guard_module()
    manifest = module.load_manifest(catalog)
    candidate = next(o for o in manifest["owners"] if o["id"] == "openjev")
    candidate["enabled"] = True
    candidate["allowed_mlx"] = ["small"]
    mlx = manifest["engines"][0]
    monkeypatch.setattr(module, "processes", lambda: [(123, mlx["root"] + "/bin/python -m mlx_vlm.server")])
    monkeypatch.setattr(module, "_running", lambda owner, _: (owner["category"] == "service", False))
    monkeypatch.setattr(module, "listener_pids", lambda _: set())
    monkeypatch.setattr(module, "_mlx_state", lambda *args: ("small", 30.0))
    monkeypatch.setattr(module, "_sysctl", lambda key: {
        "hw.memsize": 64 * 1024**3, "iogpu.wired_limit_mb": 0,
        "kern.memorystatus_vm_pressure_level": 1,
    }[key])
    with pytest.raises(module.GuardError, match="memory"):
        module.guard(manifest, "slot", "openjev")
    monkeypatch.setattr(module, "_running", lambda owner, _: (owner["id"] == "qwen-tts", False))
    with pytest.raises(module.GuardError, match="slot"):
        module.guard(manifest, "slot", "openjev")
