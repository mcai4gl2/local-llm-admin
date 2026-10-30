"""One catalog drives private host ownership metadata and drift validation."""

from pathlib import Path

from local_llm_admin.config import Settings
from local_llm_admin.resident_manifest import build_manifest
from local_llm_admin.sidecars import SidecarRegistry


def test_manifest_contains_disabled_owners_and_dependency_processes(tmp_path):
    settings = Settings(mlx_root=tmp_path, embed_root=tmp_path / "embed", ocr_root=tmp_path / "ocr",
                        decision_root=tmp_path / "decision", tts_root=tmp_path / "tts",
                        comfyui_root=tmp_path / "comfyui", strata_root=tmp_path / "strata",
                        splash_root=tmp_path / "splash")
    manifest = build_manifest(settings, SidecarRegistry(settings))
    assert manifest["schema_version"] == 1
    owners = {owner["id"]: owner for owner in manifest["owners"]}
    assert owners["qwen-embed"]["enabled"] is False
    assert owners["qwen-embed"]["intent_key"] == "embed_intent"
    assert [c["id"] for c in owners["paddleocr-vl"]["components"]] == ["api", "vlm", "pipeline"]
    assert all(Path(c["pid_file"]).is_absolute() for c in owners["paddleocr-vl"]["components"])
    assert {entry["id"] for entry in manifest["engines"]} == {"mlx", "comfyui", "strata", "splash"}


def test_catalog_digest_changes_when_a_memory_or_policy_setting_changes(tmp_path):
    settings = Settings()
    original = build_manifest(settings, SidecarRegistry(settings))["catalog_hash"]
    settings.embed_footprint_gb = 12.0
    assert build_manifest(settings, SidecarRegistry(settings))["catalog_hash"] != original
    settings.embed_footprint_gb = 9.0
    settings.embed_allowed_mlx = "example-model"
    assert build_manifest(settings, SidecarRegistry(settings))["catalog_hash"] != original


def test_manifest_cli_writes_private_atomic_payload_and_checks_drift(tmp_path):
    import json
    import subprocess
    import sys
    path = tmp_path / "private/resident-services.json"
    env = tmp_path / "env"
    env.write_text("EMBED_FOOTPRINT_GB=9\n")
    command = [sys.executable, "-m", "local_llm_admin.resident_manifest", "--env-file", str(env)]
    result = subprocess.run([*command, "--output", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["schema_version"] == 1
    assert subprocess.run([*command, "--check", str(path)], capture_output=True).returncode == 0
    env.write_text("EMBED_FOOTPRINT_GB=12\n")
    result = subprocess.run([*command, "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 1 and "differs" in result.stderr
def test_admin_refuses_a_different_host_catalog(tmp_path):
    import json
    settings = Settings(mlx_root=tmp_path)
    registry = SidecarRegistry(settings)
    path = tmp_path / "etc/resident-services.json"
    path.parent.mkdir()
    manifest = build_manifest(settings, registry)
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    assert registry.catalog_error() is None
    manifest["headroom_gb"] += 1
    path.write_text(json.dumps(manifest))
    assert "differs" in registry.catalog_error()
def test_manifest_cli_renders_private_output_and_checks_drift(tmp_path, monkeypatch):
    import json

    from local_llm_admin.resident_manifest import main
    path = tmp_path / "resident.json"
    env = tmp_path / "settings.env"
    env.write_text("OCR_ENABLED=false\n")
    monkeypatch.setattr("sys.argv", ["manifest", "--env-file", str(env), "--output", str(path)])
    assert main() == 0 and path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr("sys.argv", ["manifest", "--env-file", str(env), "--check", str(path)])
    assert main() == 0
    body = json.loads(path.read_text())
    body["headroom_gb"] += 1
    path.write_text(json.dumps(body))
    assert main() == 1
    path.unlink()
    path.symlink_to(env)
    monkeypatch.setattr("sys.argv", ["manifest", "--env-file", str(env), "--output", str(path)])
    assert main() == 1 and env.read_text() == "OCR_ENABLED=false\n"
