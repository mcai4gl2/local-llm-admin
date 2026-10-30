#!/usr/bin/python3
"""Persistent layout/PDF worker with a private JSON pipe, owned by the OCR API."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def load_pipeline() -> Any:
    path = Path(__file__).with_name("pipeline.py")
    spec = importlib.util.spec_from_file_location("isolated_ocr_pipeline", path)
    if spec is None or spec.loader is None:
        raise ValueError("missing pipeline adapter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


@contextlib.contextmanager
def silence_native() -> Iterator[None]:
    """C/native prints and Python logging must not put document content in routine logs."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    try:
        with open(os.devnull, "w") as quiet:
            os.dup2(quiet.fileno(), 1)
            os.dup2(quiet.fileno(), 2)
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for descriptor, original in zip((1, 2), saved, strict=True):
            os.dup2(original, descriptor)
            os.close(original)


def main() -> int:
    protocol = os.fdopen(os.dup(1), "w", buffering=1)

    def emit(event: dict[str, Any]) -> None:
        protocol.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        protocol.flush()

    try:
        config = json.loads(sys.stdin.readline(65537))
        root = Path(config["root"]).resolve()
        limits = config["limits"]
        with silence_native():
            adapter = load_pipeline()
            pipeline = adapter.build_pipeline(config)
            warmup = adapter.functional_warmup(pipeline)
        if warmup is not True:
            emit({"event": "error", "code": 503, "message": "recognition warmup failed"})
            return 1
        emit({"event": "ready", "warmup": True})
    except Exception:
        emit({"event": "error", "code": 503, "message": "OCR pipeline initialization failed"})
        return 1
    while True:
        line = sys.stdin.readline(65537)
        if not line:
            return 0
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["request_id"]
            path = Path(request["path"])
            if (not isinstance(request_id, str) or not re.fullmatch("[a-f0-9]{32}", request_id)
                    or path.is_symlink() or not path.resolve().is_relative_to(root / "tmp")):
                raise ValueError("invalid document input")
            media = request["media_type"]
            if media not in ("image/png", "image/jpeg", "application/pdf"):
                raise ValueError("unsupported document input")
        except (ValueError, KeyError, TypeError):
            emit({"event": "error", "request_id": request_id, "code": 400,
                  "message": "invalid private document input"})
            continue
        pages = []
        iterator = None
        try:
            with silence_native():
                if media == "application/pdf":
                    iterator = adapter.pdf_pages(
                        path, max_pages=limits["max_pages"], max_pixels=limits["max_pixels"],
                        dpi=limits["render_dpi"],
                    )
                else:
                    iterator = iter([adapter.image_page(path, limits["max_pixels"])])
                for number, width, height, pixels in iterator:
                    page = adapter.recognize_page(pipeline, pixels, number, width, height)
                    pages.append(page)
                    if len(json.dumps(pages, ensure_ascii=False).encode()) > limits["max_output_bytes"]:
                        raise adapter.DocumentError(502, "OCR output exceeds the byte limit")
                    emit({"event": "page", "request_id": request_id, "number": number})
                result = adapter.assemble(pages, config, limits["max_output_bytes"])
            emit({"event": "result", "request_id": request_id, "result": result})
        except adapter.DocumentError as exc:
            emit({"event": "error", "request_id": request_id,
                  "code": exc.code, "message": exc.message})
        except Exception:
            emit({"event": "error", "request_id": request_id, "code": 502,
                  "message": "OCR document processing failed"})
        finally:
            close = getattr(iterator, "close", None)
            if close:
                with silence_native():
                    close()


if __name__ == "__main__":
    raise SystemExit(main())
