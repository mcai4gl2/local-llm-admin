"""Build a private, reviewable OCR release archive without touching a running service."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

from .config import Settings
from .resident_manifest import build_manifest
from .sidecars import SidecarRegistry

HOST_PAYLOAD = (
    "bin/resident-services", "bin/engine-guard.sh", "bin/mlx-model",
    "comfyui/bin/comfyui", "strata/bin/strata", "splash/lib/control.py", "embed/bin/qwen-embed",
    "decision/bin/openjev", "tts/bin/qwen-tts",
    "environment/ocr-app.txt", "environment/ocr-vlm.txt",
)


def build_bundle(settings: Settings, output: Path) -> dict[str, Any]:
    registry = SidecarRegistry(settings)
    spec = registry.get_service("paddleocr-vl")
    if spec.footprint_gb <= 0 or not spec.allowed_mlx:
        raise ValueError("An explicit provisional reservation and MLX allow-list are required")
    root = Path(__file__).resolve().parents[2]
    host = root / "host"
    paths = [*HOST_PAYLOAD, *[str(path.relative_to(host)) for path in (host / "ocr").rglob("*")
                             if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"]]
    files = {name: (host / name).read_bytes() for name in sorted(paths)}
    catalog = build_manifest(settings, registry)
    files["etc/resident-services.json"] = (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode()
    files["ocr/etc/runtime.json"] = (host / "ocr/etc/runtime.example.json").read_bytes()
    summary = {"schema_version": 1, "qualified": False, "catalog_hash": catalog["catalog_hash"],
               "reservation_gb": spec.footprint_gb,
               "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["ocr-release.json"] = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600 if name in ("etc/resident-services.json", "ocr/etc/runtime.json") else (
                    0o755 if data.startswith(b"#!") else 0o644)
                archive.addfile(info, io.BytesIO(data))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build_bundle(Settings(_env_file=args.env_file), args.output)  # type: ignore[call-arg]
    print(json.dumps({"catalog_hash": summary["catalog_hash"], "files": len(summary["files"]),
                      "qualified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
