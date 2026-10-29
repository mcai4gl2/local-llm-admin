"""Prepare a minimal isolated MLX unload fix; never install or launch it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

APP_SHA256 = "fa9a69353929219af82e71f7c8aa7c9ef8b4b1b2d93568fb43cac384c256afb1"
ANCHOR = "    for cache_group, _ in list(registry.items()):"
REPLACEMENT = "    for cache_group in [kind for kind, _ in registry.items()]:"


def patch_app(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != APP_SHA256:
        raise ValueError("Pinned MLX app source differs; re-audit before patching.")
    text = source.decode()
    if text.count(ANCHOR) != 1:
        raise ValueError("Unload loop anchor is missing or ambiguous.")
    patched = text.replace(ANCHOR, REPLACEMENT, 1)
    compile(patched, "<isolated-mlx-unload>", "exec")
    return patched.encode()


def prepare(source: Path, out: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    patched = patch_app(raw)
    manifest = {
        "contract": "splash-mlx-unload-fix-1",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
        "patch_scope": "snapshot cache-group names before unloading resources",
    }
    # Existing directories, including installed package directories, are refused.
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    with (out / "app.py").open("xb") as stream:
        stream.write(patched)
    with (out / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    return manifest
