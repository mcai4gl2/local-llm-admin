"""Dependency launch ordering, failed-start rollback and stopped generation checks."""

import subprocess
import sys

import pytest

from .test_host_ocr_config import root as root  # noqa: F401
from .test_host_ocr_pipeline import load


def test_launches_vlm_before_api_and_stop_verifies_both(root, monkeypatch):
    module = load("lifecycle")
    launches = []
    children = []
    def launch(root, role, argv, env):
        launches.append(role)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(root)],
                                 start_new_session=True)
        children.append(child)
        module.owned.write_record(root / "run" / (role + ".json"), child.pid, str(root))
        return child
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(module, "validate_environments", lambda _: None)
    monkeypatch.setattr(module, "wait_vlm", lambda *_: None)
    try:
        module.start(root)
        assert launches == ["vlm", "paddleocr-vl"]
        assert module.stop(root)
        assert all(child.wait(timeout=3) is not None for child in children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_api_launch_failure_rolls_back_only_created_dependency(root, monkeypatch):
    module = load("lifecycle")
    children = []
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    def launch(root, role, argv, env):
        if role == "paddleocr-vl":
            raise OSError("API launch failed")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(root)],
                                 start_new_session=True)
        children.append(child)
        module.owned.write_record(root / "run" / (role + ".json"), child.pid, str(root))
        return child
    monkeypatch.setattr(module, "launch", launch)
    monkeypatch.setattr(module, "validate_environments", lambda _: None)
    monkeypatch.setattr(module, "wait_vlm", lambda *_: None)
    try:
        with pytest.raises(module.LifecycleError):
            module.start(root)
        assert other.poll() is None
        assert children[0].wait(timeout=3) is not None
    finally:
        other.kill()
        other.wait()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_missing_environment_lock_cannot_start_runtime(root):
    module = load("lifecycle")
    with pytest.raises(module.LifecycleError):
        module.start(root)
    assert not (root / "run/vlm.json").exists()


def test_conditional_cleanup_preserves_another_start_transaction(root):
    import json
    module = load("lifecycle")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(root)],
                             start_new_session=True)
    record = root / "run/vlm.json"
    try:
        data = module.owned.write_record(record, child.pid, str(root))
        data["owner_token"] = "b" * 32
        record.write_text(json.dumps(data))
        assert not module.stop(root, owner_token="a" * 32)
        assert child.poll() is None
        assert module.stop(root, owner_token="b" * 32)
        child.wait(timeout=3)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_environment_gate_uses_canonical_package_names_and_refuses_version_drift(root, monkeypatch):
    import json
    from types import SimpleNamespace
    module = load("lifecycle")
    environment = root.parent / "environment"
    environment.mkdir()
    for lock in ("ocr-app.txt", "ocr-vlm.txt"):
        (environment / lock).write_text("ruamel-yaml==0.19.1 --hash=sha256:fixture\n")
    installed = {"python": [3, 12], "system": "Darwin", "machine": "arm64",
                 "packages": {"ruamel.yaml": "0.19.1"}}
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout=json.dumps(installed)))
    module.validate_environments(root)
    installed["packages"]["ruamel.yaml"] = "0.19.2"
    with pytest.raises(module.LifecycleError, match="differs"):
        module.validate_environments(root)
