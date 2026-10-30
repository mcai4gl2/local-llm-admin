"""Private OCR settings derived from the validated resident catalog."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

LIMITS: dict[str, Any] = {
    "max_bytes": 10 * 1024 * 1024, "max_pixels": 16_000_000, "max_pages": 16,
    "render_dpi": 144, "page_timeout_s": 45.0, "document_timeout_s": 240.0,
    "max_output_bytes": 2 * 1024 * 1024, "queue_depth": 2, "queue_timeout_s": 30.0,
}


class ConfigError(ValueError):
    pass


def private_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
        raise ConfigError("OCR private configuration must be a regular mode-600 file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ConfigError("Invalid OCR configuration")
    return value


def resident_catalog(root: Path) -> dict[str, Any]:
    helper = root.parent / "bin/resident-services"
    spec = importlib.util.spec_from_loader("ocr_resident_catalog", loader=None)
    if spec is None:
        raise ConfigError("Missing resident catalog helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(helper.read_bytes(), str(helper), "exec"), module.__dict__)
    return dict(module.load_manifest(root.parent / "etc/resident-services.json"))


def load_config(root: Path) -> dict[str, Any]:
    try:
        body = private_json(root / "etc/runtime.json")
        if set(body) != {"schema_version", "revision", "layout_revision", "orientation_revision", "precision"} or body["schema_version"] != 1:
            raise ConfigError("Unsupported OCR runtime settings")
        if any(not isinstance(body[key], str) or not re.fullmatch("[a-f0-9]{40}", body[key])
               for key in ("revision", "layout_revision", "orientation_revision")) or body["precision"] != "bf16":
            raise ConfigError("OCR candidate revision or precision is unsupported")
        catalog = resident_catalog(root)
        owner = next(o for o in catalog["owners"] if o["id"] == "paddleocr-vl")
        if not owner["enabled"] or Path(owner["root"]) != root:
            raise ConfigError("OCR is not enabled at this catalog root")
        model = next(m for m in owner["models"] if m["capability"] == "ocr")
        if model["revision"] != body["revision"] or model["precision"] != body["precision"]:
            raise ConfigError("OCR model settings differ from the resident catalog")
        components = {c["id"]: c for c in owner["components"]}
        api_port, vlm_port = components["api"]["port"], components["vlm"]["port"]
        if not api_port or not vlm_port or api_port == vlm_port:
            raise ConfigError("OCR ports are not configured")
        model_path = root / "models" / ("PaddleOCR-VL-1.6-" + body["revision"][:12])
        layout_path = root / "models" / ("PP-DocLayoutV3-" + body["layout_revision"][:12])
        orientation_path = root / "models" / ("PP-LCNet_x1_0_doc_ori-" + body["orientation_revision"][:12])
        for path, revision in ((model_path, body["revision"]), (layout_path, body["layout_revision"]),
                               (orientation_path, body["orientation_revision"])):
            if path.is_symlink() or not path.is_dir() or (path / ".revision").read_text().strip() != revision:
                raise ConfigError("OCR model snapshot receipt differs")
        model_config = json.loads((model_path / "config.json").read_text())
        dtype = model_config.get("torch_dtype") or model_config.get("dtype")
        if dtype is None:
            dtype = model_config.get("text_config", {}).get("torch_dtype")
        if dtype not in ("bfloat16", "bf16") or model_config.get("quantization"):
            raise ConfigError("OCR snapshot does not match the BF16 candidate")
        return {
            "root": str(root), "model_id": model["id"], "revision": body["revision"],
            "layout_revision": body["layout_revision"], "orientation_revision": body["orientation_revision"],
            "orientation_model_path": str(orientation_path), "precision": body["precision"],
            "api_port": api_port, "vlm_port": vlm_port,
            "vlm_model_path": str(model_path), "layout_model_path": str(layout_path),
            "vlm_base": f"http://127.0.0.1:{api_port}/vlm/v1/",
            "limits": dict(LIMITS), "ready_timeout_s": owner["ready_timeout_s"],
            "catalog_hash": catalog["catalog_hash"],
        }
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        raise ConfigError("OCR runtime configuration, catalog or snapshots are invalid") from None
