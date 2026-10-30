"""OCR persistent worker protocol keeps documents off logs and preserves page order."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from .test_host_ocr_pipeline import HOST

STUB = """
import json
from pathlib import Path
class DocumentError(ValueError):
    code = 422
    message = "invalid document"
def build_pipeline(config):
    print("native startup output that must not reach protocol")
    return object()
def functional_warmup(pipeline):
    return True
def image_page(path, max_pixels):
    if path.read_bytes() == b"corrupt":
        raise DocumentError("private filename")
    return 1, 8, 8, "pixels"
def pdf_pages(path, **kwargs):
    yield 1, 8, 8, "pixels"
    yield 2, 8, 8, "pixels"
def recognize_page(pipeline, pixels, number, width, height):
    print("recognized text must not become a routine log")
    return {"number":number,"width_px":width,"height_px":height,"text":str(number),"markdown":str(number),"complete":True,"empty":False,"blocks":[]}
def assemble(pages, config, max_output_bytes):
    return {"pages":pages}
"""


def test_worker_stays_loaded_processes_documents_and_emits_only_protocol(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy2(HOST / "worker.py", app / "worker.py")
    (app / "pipeline.py").write_text(STUB)
    private = tmp_path / "tmp/request-test"
    private.mkdir(parents=True)
    image = private / "input"
    image.write_bytes(b"image")
    init = {"root": str(tmp_path), "model_id": "local-ocr", "revision": "a" * 40,
            "precision": "bf16", "limits": {"max_pixels": 16000000, "max_pages": 16,
            "render_dpi": 144, "max_output_bytes": 2097152}}
    requests = [init,
        {"request_id": "a" * 32, "path": str(image), "media_type": "image/png"},
        {"request_id": "b" * 32, "path": str(image), "media_type": "application/pdf"}]
    result = subprocess.run([sys.executable, "-I", str(app / "worker.py")],
                            input="\n".join(json.dumps(r) for r in requests) + "\n",
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0] == {"event": "ready", "warmup": True}
    documents = [event for event in events if event["event"] == "result"]
    assert [len(event["result"]["pages"]) for event in documents] == [1, 2]
    assert result.stderr == "" and "routine log" not in result.stdout


def test_worker_rejects_paths_outside_private_request_root(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy2(HOST / "worker.py", app / "worker.py")
    (app / "pipeline.py").write_text(STUB)
    config = {"root": str(tmp_path), "limits": {"max_pixels": 100, "max_pages": 1,
              "render_dpi": 144, "max_output_bytes": 10000}}
    request = {"request_id": "a" * 32, "path": str(tmp_path / "secret"), "media_type": "image/png"}
    result = subprocess.run([sys.executable, "-I", str(app / "worker.py")],
                            input=json.dumps(config) + "\n" + json.dumps(request) + "\n",
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    event = json.loads(result.stdout.splitlines()[-1])
    assert event["event"] == "error" and event["code"] == 400
    assert "secret" not in result.stdout


def test_failed_page_is_never_announced_as_completed(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy2(HOST / "worker.py", app / "worker.py")
    (app / "pipeline.py").write_text(STUB.replace(
        'print("recognized text must not become a routine log")', 'raise DocumentError("failed page")'))
    private = tmp_path / "tmp/request-test"
    private.mkdir(parents=True)
    image = private / "input"
    image.write_bytes(b"image")
    config = {"root": str(tmp_path), "limits": {"max_pixels": 100, "max_pages": 1,
              "render_dpi": 144, "max_output_bytes": 10000}}
    request = {"request_id": "a" * 32, "path": str(image), "media_type": "image/png"}
    result = subprocess.run([sys.executable, "-I", str(app / "worker.py")],
                            input=json.dumps(config) + "\n" + json.dumps(request) + "\n",
                            capture_output=True, text=True, timeout=5)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == ["ready", "error"]
