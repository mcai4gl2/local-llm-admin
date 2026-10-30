"""Private OCR runtime configuration matches the normalized catalog and pinned snapshots."""

import dataclasses
import json
import shutil

import pytest

from local_llm_admin.config import Settings
from local_llm_admin.ocr import OcrLimits
from local_llm_admin.resident_manifest import build_manifest
from local_llm_admin.sidecars import SidecarRegistry

from .test_host_ocr_pipeline import HOST, load

REVISION = "c5630abae1d940eafe0697512a0325494b02ab42"
LAYOUT = "241f8bdfc77a7c7bee915a5057aaee58c235a8d3"
ORIENTATION = "d3b95a6dff5fe8a94f2748e12b61cb26818a0df8"


@pytest.fixture
def root(tmp_path):
    volume = tmp_path / "Models"
    root = volume / "ocr"
    (root / "etc").mkdir(parents=True)
    (volume / "etc").mkdir()
    (volume / "bin").mkdir()
    shutil.copy2(HOST.parents[1] / "bin/resident-services", volume / "bin/resident-services")
    settings = Settings(mlx_root=volume, ocr_root=root, embed_root=volume / "embed",
                        decision_root=volume / "decision", tts_root=volume / "tts",
                        comfyui_root=volume / "comfyui", strata_root=volume / "strata",
                        splash_root=volume / "splash", ocr_enabled=True,
                        ocr_port=8097, ocr_vlm_port=8098, ocr_footprint_gb=12.0)
    manifest = volume / "etc/resident-services.json"
    manifest.write_text(json.dumps(build_manifest(settings, SidecarRegistry(settings))))
    manifest.chmod(0o600)
    config = root / "etc/runtime.json"
    config.write_text(json.dumps({"schema_version": 1, "revision": REVISION,
                                 "layout_revision": LAYOUT, "orientation_revision": ORIENTATION, "precision": "bf16"}))
    config.chmod(0o600)
    for name, revision in (("PaddleOCR-VL-1.6", REVISION), ("PP-DocLayoutV3", LAYOUT), ("PP-LCNet_x1_0_doc_ori", ORIENTATION)):
        path = root / "models" / (name + "-" + revision[:12])
        path.mkdir(parents=True)
        (path / ".revision").write_text(revision)
        (path / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}))
    return root


def test_config_derives_ports_models_and_limits_from_one_catalog(root):
    module = load("config")
    config = module.load_config(root)
    assert config["orientation_model_path"].endswith("PP-LCNet_x1_0_doc_ori-" + ORIENTATION[:12])
    assert config["api_port"] == 8097 and config["vlm_port"] == 8098
    assert config["model_id"] == "local-ocr" and config["revision"] == REVISION
    assert config["limits"] == dataclasses.asdict(OcrLimits())
    assert config["vlm_base"] == "http://127.0.0.1:8097/vlm/v1/"


@pytest.mark.parametrize("problem", ["permissions", "revision", "precision", "snapshot", "command"])
def test_config_refuses_drift_and_executable_fragments(root, problem):
    module = load("config")
    path = root / "etc/runtime.json"
    body = json.loads(path.read_text())
    if problem == "permissions":
        path.chmod(0o644)
    elif problem in ("revision", "precision"):
        body[problem] = "invalid"
        path.write_text(json.dumps(body))
    elif problem == "command":
        body["command"] = "arbitrary shell fragment"
        path.write_text(json.dumps(body))
    else:
        next((root / "models").glob("PaddleOCR*/.revision")).write_text("a" * 40)
    with pytest.raises(module.ConfigError):
        module.load_config(root)
