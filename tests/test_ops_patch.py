import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
ORIG = HERE / "fixtures/mac/mlx-model.orig"
PATCH = HERE.parent / "ops/patches/mlx-model-comfyui-guard.patch"


def test_patch_applies_and_adds_8188_guard(tmp_path):
    target = tmp_path / "mlx-model"
    shutil.copy(ORIG, target)
    r = subprocess.run(
        ["patch", "--forward", str(target), str(PATCH)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    text = target.read_text()
    assert "lsof -nP -iTCP:8188 -sTCP:LISTEN" in text
    assert "ComfyUI" in text
    assert subprocess.run(["bash", "-n", str(target)]).returncode == 0
    # applying twice is refused (idempotency guard in the apply script relies on this)
    again = subprocess.run(
        ["patch", "--forward", "--dry-run", str(target), str(PATCH)], capture_output=True, text=True
    )
    assert again.returncode != 0


def test_apply_script_is_bsd_patch_and_zsh_compatible():
    """The Mac ships Apple's BSD patch 2.0 (no GNU long options); mlx-model is a zsh script."""
    script = (HERE.parent / "ops/apply-mlx-model-guard.sh").read_text()
    assert "--no-backup-if-mismatch" not in script
    assert 'zsh -n "$F"' in script and 'bash -n "$F"' not in script


def test_apply_script_final_check_cannot_sigpipe():
    """`status | head -1` under pipefail exits 141 after a successful apply (seen on the Mac)."""
    script = (HERE.parent / "ops/apply-mlx-model-guard.sh").read_text()
    assert "| head" not in script
