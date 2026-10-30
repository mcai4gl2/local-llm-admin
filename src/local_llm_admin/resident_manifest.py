"""Normalize the registered catalog into private, non-executable host guard metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .sidecars import SidecarRegistry, SidecarSpec


def catalog_digest(body: dict[str, Any]) -> str:
    content = {key: value for key, value in body.items() if key != "catalog_hash"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _owner(spec: SidecarSpec) -> dict[str, Any]:
    return {
        "id": spec.name, "category": spec.kind, "enabled": spec.enabled,
        "root": str(spec.pid_file.parent.parent), "intent_key": spec.intent_key,
        "allowed_mlx": sorted(spec.allowed_mlx), "reservation_gb": spec.footprint_gb,
        "models": [{"id": model.id, "capability": model.capability,
                    "revision": model.revision, "precision": model.precision} for model in spec.models],
        "ready_timeout_s": spec.ready_timeout_s,
        "generation_checked": spec.generation_checked,
        "components": [
            {"id": "api", "port": spec.port, "pid_file": str(spec.pid_file),
             "marker": spec.cmdline_marker, "requires": [c.name for c in spec.components]},
            *[{"id": c.name, "port": c.port, "pid_file": str(c.pid_file),
               "marker": c.cmdline_marker, "requires": list(c.requires)} for c in spec.components],
        ],
    }


def build_manifest(settings: Settings, registry: SidecarRegistry) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1, "root": str(settings.mlx_root),
        "headroom_gb": settings.decision_headroom_gb,
        "owners": [_owner(spec) for spec in [
            *registry.specs(enabled_only=False), *registry.services(enabled_only=False),
        ]],
        "engines": [
            {"id": "mlx", "exclusive": False, "root": str(settings.mlx_root / "venvs/mlx"),
             "pid_file": str(settings.mlx_root / "run/mlx-server.pid"), "marker": "mlx_vlm.server",
             "ports": sorted({settings.mlx_port, settings.chat_ui_port})},
            {"id": "comfyui", "exclusive": True, "root": str(settings.comfyui_root),
             "pid_file": str(settings.comfyui_root / "run/comfyui.pid"), "marker": "main.py",
             "ports": [settings.comfyui_port]},
            {"id": "strata", "exclusive": True, "root": str(settings.strata_root),
             "pid_file": str(settings.strata_root / "run/strata.pid"), "marker": "serve/server.py",
             "ports": [settings.strata_port]},
            {"id": "splash", "exclusive": True, "root": str(settings.splash_root),
             "pid_file": str(settings.splash_root / "run/splash.json"), "marker": "runtime/",
             "ports": [settings.splash_port]},
        ],
    }
    body["catalog_hash"] = catalog_digest(body)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Render/check private resident ownership metadata.")
    parser.add_argument("--env-file", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path)
    output.add_argument("--check", type=Path)
    args = parser.parse_args()
    try:
        settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
        body = build_manifest(settings, SidecarRegistry(settings))
        if args.check is not None:
            if json.loads(args.check.read_text()) != body:
                print("Resident catalog differs from the configured registrations.", file=sys.stderr)
                return 1
        if args.output is not None:
            if args.output.is_symlink():
                raise ValueError("output must not be a symlink")
            args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".resident-", dir=args.output.parent)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(body, stream, sort_keys=True, indent=2)
                    stream.write("\n")
                os.replace(temporary, args.output)
            finally:
                Path(temporary).unlink(missing_ok=True)
        print(json.dumps({"catalog_hash": body["catalog_hash"], "owners": len(body["owners"])}))
        return 0
    except (OSError, ValueError):
        print("Resident manifest could not be rendered or checked; verify private settings and paths.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
