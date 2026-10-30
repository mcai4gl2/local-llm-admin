"""Generated public OCR fixtures; no real documents or model output."""

import base64
import struct
import zlib

REVISION = "c5630abae1d940eafe0697512a0325494b02ab42"


def png(width=8, height=8):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + b"\xff\xff\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def chat(data=None, media_type="image/png"):
    encoded = base64.b64encode(data if data is not None else png()).decode()
    url = f"data:{media_type};base64,{encoded}"
    part = ({"type": "file", "file": {"file_data": url}} if media_type == "application/pdf"
            else {"type": "image_url", "image_url": {"url": url}})
    return {"model": "local-ocr", "messages": [{"role": "user", "content": [part]}]}


def result(pages=1):
    items = [{
        "number": n, "width_px": 8, "height_px": 8, "empty": False,
        "text": f"Page {n}", "markdown": f"Page {n}", "complete": True,
        "blocks": [{"kind": "text", "text": f"Page {n}", "bbox": [0, 0, 8, 8]}],
    } for n in range(1, pages + 1)]
    return {
        "schema_version": 1, "model": "local-ocr", "revision": REVISION, "precision": "bf16",
        "coordinate_units": "pixels", "pages": items, "complete": True, "truncated": False,
        "text": "\n\n".join(p["text"] for p in items),
        "markdown": "\n\n".join(p["markdown"] for p in items),
    }
