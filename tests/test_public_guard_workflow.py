"""Run the workflow scanner against real branch histories and a synthetic merge."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from .test_check_public import PATTERNS, ROOT, SECRET, _git


def _workflow_command() -> str:
    workflow = (ROOT / ".github/workflows/public-guard.yml").read_text()
    step = workflow.split("      - name: Scan tracked files, commit messages and history", 1)[1]
    run = step.split("        run:", 1)[1].strip("\n ")
    if run.startswith("|"):
        return textwrap.dedent(run[1:]).strip()
    return run


def _commit(repo: Path, label: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "--allow-empty", "-m", label)


def _removed_leak(repo: Path) -> None:
    path = repo / "transient.txt"
    path.write_text(SECRET + "\n")
    _commit(repo, "add synthetic fixture")
    path.unlink()
    _commit(repo, "remove synthetic fixture")


@pytest.mark.parametrize("location", ["synthetic_identity", "head_history", "base_history", "tree"])
def test_public_guard_checks_real_histories_and_merged_tree(tmp_path: Path, location: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "user-a")
    _git(repo, "config", "user.email", "user-a@example.com")
    (repo / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/check_public.py", repo / "scripts/check_public.py")
    (repo / "shared.txt").write_text("clean\n")
    _commit(repo, "initial")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("clean\n")
    _commit(repo, "feature")
    if location == "head_history":
        _removed_leak(repo)
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    (repo / "base.txt").write_text("clean\n")
    _commit(repo, "base")
    if location == "base_history":
        _removed_leak(repo)
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "merge", "--no-ff", "--no-commit", "feature")
    _git(repo, "-c", f"user.email={SECRET}@example.com", "commit", "-q", "-m", "synthetic merge")
    if location == "tree":
        (repo / "shared.txt").write_text(SECRET + "\n")
    result = subprocess.run(
        ["bash", "-e", "-c", _workflow_command()],
        cwd=repo,
        env=dict(os.environ, PUBLIC_DENYLIST=PATTERNS, PUBLIC_HEAD_SHA=head, PUBLIC_BASE_SHA=base),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == (0 if location == "synthetic_identity" else 1), result.stderr
    assert SECRET not in result.stdout + result.stderr
