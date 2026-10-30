#!/usr/bin/python3
"""Provision only isolated OCR environments and pinned snapshots; preview by default."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

REVISION = "c5630abae1d940eafe0697512a0325494b02ab42"
LAYOUT = "241f8bdfc77a7c7bee915a5057aaee58c235a8d3"
ORIENTATION = "d3b95a6dff5fe8a94f2748e12b61cb26818a0df8"
SNAPSHOTS = (("PaddleOCR-VL-1.6", REVISION), ("PP-DocLayoutV3", LAYOUT),
             ("PP-LCNet_x1_0_doc_ori", ORIENTATION))


class ProvisionError(ValueError):
    pass


def local_module(root: Path, name: str) -> Any:
    path = root / "app" / (name + ".py")
    spec = importlib.util.spec_from_file_location("ocr_provision_" + name, path)
    if spec is None or spec.loader is None:
        raise ProvisionError("Missing provision helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def safe_root(root: Path) -> None:
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root or root.name != "ocr":
        raise ProvisionError("Use the installed absolute OCR root without symlinks")


def ensure_stopped(root: Path) -> None:
    owned = local_module(root, "owned")
    markers = (str(root / "app/server.py"), str(root / "app/worker.py"), "mlx_vlm.server")
    if any(str(root) + "/" in row["command"] and any(m in row["command"] for m in markers)
           for row in owned.inventory()):
        raise ProvisionError("Stop OCR and verify ownership before provisioning or rollback")
    if any((root / "run" / (role + suffix)).exists()
           for role in ("paddleocr-vl", "pipeline", "vlm") for suffix in (".json", ".pid")):
        raise ProvisionError("OCR process records remain; verify and stop them first")


def preview(root: Path) -> dict[str, Any]:
    safe_root(root)
    locks = {}
    for name in ("ocr-app.txt", "ocr-vlm.txt"):
        path = root.parent / "environment" / name
        if not path.is_file():
            raise ProvisionError("Frozen environment locks are missing")
        locks[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema_version": 1, "target": hashlib.sha256(str(root).encode()).hexdigest(),
            "locks": locks, "python": "3.12", "precision": "bf16",
            "snapshots": [{"model": name, "revision": revision} for name, revision in SNAPSHOTS],
            "creates": ["venv", "venv-vlm", *["models/" + name + "-" + revision[:12]
                                             for name, revision in SNAPSHOTS]]}


def save(path: Path, body: dict[str, Any], *, create: bool = False) -> None:
    if path.is_symlink():
        raise ProvisionError("Receipt cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(body, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


DOWNLOAD = """
import pathlib,sys
from huggingface_hub import snapshot_download
name,revision,target=sys.argv[1:]
snapshot_download('PaddlePaddle/'+name,revision=revision,local_dir=target)
path=pathlib.Path(target)
"""


def verify_snapshot(path: Path, name: str) -> None:
    if name in ("PP-DocLayoutV3", "PP-LCNet_x1_0_doc_ori"):
        complete = all((path / file).is_file() for file in
                       ("inference.json", "inference.pdiparams", "inference.yml"))
    else:
        complete = (path / "config.json").is_file() and any(path.glob("*.safetensors"))
    if not complete:
        raise ProvisionError("Incomplete pinned model snapshot")


def apply(root: Path, receipt_path: Path, *, download_models: bool = False) -> None:
    plan = preview(root)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ProvisionError("Native OCR provisioning requires Apple Silicon")
    ensure_stopped(root)
    if any((root / name).exists() or (root / name).is_symlink() for name in plan["creates"]):
        raise ProvisionError("Candidate paths already exist; verify them or roll back the prior receipt")
    if not download_models:
        raise ProvisionError("First provisioning requires explicit --download-models")
    uv = shutil.which("uv")
    if not uv:
        raise ProvisionError("Install uv before provisioning")
    receipt = {**plan, "state": "prepared", "owner": uuid.uuid4().hex, "created": []}
    save(receipt_path, receipt, create=True)
    try:
        for name in plan["creates"]:
            path = root / name
            path.mkdir(parents=True, mode=0o700)
            (path / ".provision-owner").write_text(receipt["owner"])
            receipt["created"].append(name)
            save(receipt_path, receipt)
        for name, lock in (("venv", "ocr-app.txt"), ("venv-vlm", "ocr-vlm.txt")):
            subprocess.run([uv, "venv", "--python", "3.12", "--allow-existing", str(root / name)], check=True)
            subprocess.run([uv, "pip", "sync", "--require-hashes", "--only-binary", ":all:",
                            "--python", str(root / name / "bin/python"),
                            str(root.parent / "environment" / lock)], check=True)
        for name, revision in SNAPSHOTS:
            path = root / "models" / (name + "-" + revision[:12])
            subprocess.run([str(root / "venv-vlm/bin/python"), "-I", "-c", DOWNLOAD,
                            name, revision, str(path)], check=True)
            verify_snapshot(path, name)
            (path / ".revision").write_text(revision + "\n")
        local_module(root, "lifecycle").validate_environments(root)
        local_module(root, "config").load_config(root)
        receipt["state"] = "provisioned_unqualified"
        save(receipt_path, receipt)
    except Exception:
        receipt["state"] = "failed"
        save(receipt_path, receipt)
        raise ProvisionError("Provisioning failed; preserve the receipt and use its isolated rollback") from None


def rollback(root: Path, receipt_path: Path) -> None:
    expected = preview(root)
    ensure_stopped(root)
    if receipt_path.is_symlink() or receipt_path.stat().st_mode & 0o077:
        raise ProvisionError("Rollback receipt must be private")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("target") != expected["target"] or receipt.get("state") == "rolled_back":
        raise ProvisionError("Receipt belongs to a different target or is already rolled back")
    for name in receipt["created"]:
        if name not in expected["creates"]:
            raise ProvisionError("Receipt contains an unexpected path")
        path = root / name
        if path.is_symlink() or (path / ".provision-owner").read_text() != receipt["owner"]:
            raise ProvisionError("A provisioned directory changed ownership")
    for name in reversed(receipt["created"]):
        shutil.rmtree(root / name)
    receipt["state"] = "rolled_back"
    save(receipt_path, receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--download-models", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    try:
        if args.apply or args.rollback:
            if not args.receipt:
                raise ProvisionError("A private receipt path is required")
            if args.apply:
                apply(args.root, args.receipt, download_models=args.download_models)
            else:
                rollback(args.root, args.receipt)
        else:
            print(json.dumps(preview(args.root), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("OCR provisioning refused or incomplete; inspect the private receipt and runbook.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
