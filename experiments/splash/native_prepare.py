#!/usr/bin/env python3
"""Prepare isolated, source-pinned native instrumentation; never install or start it."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import native_capture

PINS = {
    "mlx": "6c0b663d7adafbc686c6049649953fd78bea1572d2a6280532fc063dda511b77",
    "splash": "0ce85023177d4029942e9505a4b0f8c67dddb29a7fef37ac9c0fd15ce1b0bcd6",
}


def replace_once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise ValueError("Pinned source anchor is missing or ambiguous.")
    return source.replace(anchor, replacement, 1)


def instrument(backend: str, source: str) -> str:
    if backend == "mlx":
        replacements = [
            (
                "        self.requests.put(queued_request)",
                "        _phase0_native.begin_mlx(rqueue, prompt, args, images, audio, videos, "
                "self.draft_model, is_diffusion_model(self.model))\n        self.requests.put(queued_request)",
            ),
            (
                '        """Convert one generated token into a streaming text segment."""',
                '        """Convert one generated token into a streaming text segment."""\n'
                '        _phase0_native.mlx_batch(info["rqueue"], token)',
            ),
            (
                "        if item is None:\n            self._ended = True",
                "        if item is None:\n            _phase0_native.abort(self._rqueue)\n"
                "            self._ended = True",
            ),
            (
                "        if isinstance(item, Exception):\n            self._ended = True",
                "        if isinstance(item, Exception):\n"
                "            _phase0_native.abort(self._rqueue)\n            self._ended = True",
            ),
            (
                '        if getattr(item, "finish_reason", None):',
                "        _phase0_native.observe_mlx(self._rqueue, item)\n"
                '        if getattr(item, "finish_reason", None):',
            ),
            (
                "        if not self._ended:\n            self._cancel_fn(self._uid)",
                "        if not self._ended:\n            _phase0_native.abort(self._rqueue)\n"
                "            self._cancel_fn(self._uid)",
            ),
        ]
    elif backend == "splash":
        replacements = [
            (
                "        request = self._generation_request(job)",
                "        request = self._generation_request(job)\n"
                "        _phase0_native.begin_splash(job)",
            ),
            (
                "    def _on_event(self, state, event):\n        job = state.job",
                "    def _on_event(self, state, event):\n"
                "        _phase0_at_ns = time.monotonic_ns()\n        job = state.job",
            ),
            (
                "        elif isinstance(event, wire.TokensEvent):",
                "        elif isinstance(event, wire.TokensEvent):\n"
                "            _phase0_native.splash_batch(job, event.tokens, _phase0_at_ns)",
            ),
            (
                "        self._record(job, result=result, error=error)",
                "        _phase0_native.finish_splash(state, result, error)\n"
                "        self._record(job, result=result, error=error)",
            ),
        ]
    else:
        raise ValueError("Unknown native backend.")
    for anchor, replacement in replacements:
        source = replace_once(source, anchor, replacement)
    compile(source, "<isolated-native-module>", "exec")
    return source


def prepare(backend: str, source: Path, out: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    if backend not in PINS or source_sha != PINS[backend]:
        raise ValueError("Pinned runtime source differs; re-audit before instrumentation.")
    patched = instrument(backend, raw.decode())
    collector = Path(native_capture.__file__).read_bytes()
    adapter_sha = native_capture.digest(
        {
            "instrumented_body_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "collector_sha256": hashlib.sha256(collector).hexdigest(),
            "preparer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    # Preserve the module docstring and any future imports.
    nodes = ast.parse(patched).body
    line = 0
    for node in nodes:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            line = node.end_lineno or 0
        else:
            break
    lines = patched.splitlines(keepends=True)
    lines.insert(
        line,
        "\nfrom . import phase0_native_capture as _phase0_native\n"
        f'_phase0_native.configure("{backend}", "{source_sha}", "{adapter_sha}")\n',
    )
    patched = "".join(lines)
    compile(patched, "<isolated-native-module>", "exec")
    filename = "generation.py" if backend == "mlx" else "backend.py"
    files = {filename: patched.encode(), "phase0_native_capture.py": collector}
    receipt = {
        "contract": "splash-native-adapter-bundle-1",
        "status": "prepared_only",
        "backend": backend,
        "source_sha256": source_sha,
        "adapter_sha256": adapter_sha,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "native_gate": "unassessed",
        "requires": [
            "isolated source copy",
            "full runtime pin review",
            "fresh maintenance preflight",
            "live callback/accounting validation",
            "equivalent-boundary and overhead review",
        ],
    }
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in files.items():
        with (out / name).open("xb") as f:
            (out / name).chmod(0o600)
            f.write(data)
    with (out / "receipt.json").open("x") as f:
        (out / "receipt.json").chmod(0o600)
        json.dump(receipt, f, indent=2)
        f.write("\n")
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["mlx", "splash"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        prepare(args.backend, args.source, args.out)
    except (ValueError, OSError, SyntaxError):
        print("Preparation refused; inspect source pins and new private output path.")
        return 2
    print("Isolated adapter bundle prepared; native gate remains unassessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
