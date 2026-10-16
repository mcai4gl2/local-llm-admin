"""The versioned Mac-side files (host/) stay consistent with their recorded history."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "host"


def test_recorded_dc1_script_is_the_original_plus_the_dc1_patch(tmp_path):
    """History: the first change. The production script has moved on since (M1 §2)."""
    work = tmp_path / "mlx-model"
    shutil.copy(ROOT / "tests/fixtures/mac/mlx-model.orig", work)
    subprocess.run(
        [
            "patch",
            "--forward",
            "-s",
            str(work),
            str(ROOT / "ops/patches/mlx-model-comfyui-guard.patch"),
        ],
        check=True,
    )
    assert work.read_text() == (ROOT / "tests/fixtures/mac/mlx-model.dc1").read_text()


def test_host_scripts_are_executable_and_zsh():
    scripts = [
        HOST / "bin" / n
        for n in (
            "mlx-model",
            "mlx-env.sh",
            "chat-qwen.sh",
            "serve-qwen.sh",
            "download-models.sh",
        )
    ]
    scripts.append(HOST / "comfyui/bin/comfyui")
    scripts.append(HOST / "decision/bin/openjev")
    scripts.append(HOST / "tts/bin/qwen-tts")
    scripts.append(HOST / "embed/bin/qwen-embed")
    scripts.append(HOST / "strata/bin/strata")
    for s in scripts:
        assert s.stat().st_mode & 0o111, f"{s} is not executable"
        assert s.read_text().startswith("#!/bin/zsh"), s


def test_host_diff_and_deploy_share_the_file_list():
    """The allow-list lives in ops/host-files.list (see test_host_deploy.py)."""
    for script in ("host-diff.sh", "deploy-host.sh"):
        assert "host-files.list" in (ROOT / "ops" / script).read_text()
