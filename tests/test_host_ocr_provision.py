"""Provision previews and ownership-limited rollback never change neighboring runtimes."""

import json
import shutil

import pytest

from .test_host_ocr_pipeline import HOST, load


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "Models/ocr"
    shutil.copytree(HOST, root)
    (root.parent / "environment").mkdir()
    for lock in ("ocr-app.txt", "ocr-vlm.txt"):
        (root.parent / "environment" / lock).write_text("example==1.0 --hash=sha256:abc\n")
    return root


def test_preview_pins_two_isolated_environments_without_creating_them(root):
    module = load("provision")
    plan = module.preview(root)
    assert plan["python"] == "3.12" and plan["precision"] == "bf16"
    assert len(plan["locks"]) == 2 and len(plan["snapshots"]) == 3
    assert not (root / "venv").exists()


def test_layout_snapshot_validation_uses_paddle_artifacts(root):
    module = load("provision")
    path = root / "models/layout"
    path.mkdir(parents=True)
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (path / name).write_text("fixture")
    module.verify_snapshot(path, "PP-DocLayoutV3")
    module.verify_snapshot(path, "PP-LCNet_x1_0_doc_ori")
    with pytest.raises(module.ProvisionError):
        module.verify_snapshot(path, "PaddleOCR-VL-1.6")


def test_rollback_removes_only_directories_created_by_its_receipt(root, tmp_path, monkeypatch):
    module = load("provision")
    monkeypatch.setattr(module, "ensure_stopped", lambda _: None)
    sibling = root.parent / "embed/venv"
    sibling.mkdir(parents=True)
    (root / "venv").mkdir()
    (root / "venv/.provision-owner").write_text("test-owner")
    receipt = {**module.preview(root), "state": "failed", "owner": "test-owner", "created": ["venv"]}
    snapshot = tmp_path / "rollback.json"
    module.save(snapshot, receipt, create=True)
    module.rollback(root, snapshot)
    assert not (root / "venv").exists() and sibling.is_dir()
    assert json.loads(snapshot.read_text())["state"] == "rolled_back"


def test_changed_owner_prevents_rollback(root, tmp_path, monkeypatch):
    module = load("provision")
    monkeypatch.setattr(module, "ensure_stopped", lambda _: None)
    (root / "venv").mkdir()
    (root / "venv/.provision-owner").write_text("different-owner")
    snapshot = tmp_path / "rollback.json"
    module.save(snapshot, {**module.preview(root), "state": "failed", "owner": "test-owner", "created": ["venv"]}, create=True)
    with pytest.raises(module.ProvisionError, match="ownership"):
        module.rollback(root, snapshot)
    assert (root / "venv").is_dir()
