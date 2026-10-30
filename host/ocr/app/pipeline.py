"""Isolated PaddleOCR layout/region pipeline and incremental rasterization.

Native packages are imported only here, inside the OCR pipeline environment.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class DocumentError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self.parts.append(" ")
        elif tag in ("tr", "p"):
            self.parts.append("\n")


def plain_block(block: dict[str, Any]) -> str:
    if block["kind"] != "table":
        text = str(block["text"])
        stripped = text.strip()
        delimiter = "$$" if stripped.startswith("$$") else "$"
        if (stripped.startswith(delimiter) and stripped.endswith(delimiter)
                and len(stripped) > 2 * len(delimiter)):
            expression = stripped[len(delimiter):-len(delimiter)].strip()
            if "$" not in expression and (block["kind"] in ("display_formula", "inline_formula")
                                          or re.search(r"[=<>^_+*/\\]", expression)):
                return expression
        return text
    parser = TableText()
    parser.feed(block["text"])
    parser.close()
    return "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines()).strip()


def build_pipeline(config: dict[str, Any]) -> Any:
    from paddleocr import DocImgOrientationClassification, PaddleOCRVL

    layout = Path(config["layout_model_path"])
    vlm = Path(config["vlm_model_path"])
    if not layout.is_dir() or not vlm.is_dir():
        raise DocumentError(503, "OCR model snapshots are missing")
    orientation = DocImgOrientationClassification(
        model_name="PP-LCNet_x1_0_doc_ori", model_dir=config["orientation_model_path"], device="cpu",
    )
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6", device="cpu",
        layout_detection_model_name="PP-DocLayoutV3", layout_detection_model_dir=str(layout),
        vl_rec_model_name="PaddleOCR-VL-1.6-0.9B", vl_rec_model_dir=str(vlm),
        vl_rec_backend="mlx-vlm-server", vl_rec_server_url=config["vlm_base"],
        vl_rec_api_model_name=str(vlm), vl_rec_api_key=config["pipeline_token"],
        vl_rec_max_concurrency=1, use_queues=False,
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_layout_detection=True, format_block_content=False,
    )
    return DocumentPipeline(pipeline, orientation)


class OrientedPrediction:
    def __init__(self, prediction: Any, angle: int) -> None:
        self.prediction, self.input_rotation = prediction, angle

    @property
    def json(self) -> Any:
        return self.prediction.json

    @property
    def markdown(self) -> Any:
        return self.prediction.markdown

    def __getitem__(self, key: str) -> Any:
        return self.prediction[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.prediction[key] = value


class DocumentPipeline:
    def __init__(self, pipeline: Any, orientation: Any) -> None:
        self.pipeline, self.orientation = pipeline, orientation

    def predict(self, pixels: Any, **kwargs: Any) -> list[OrientedPrediction]:
        # Classify a padded content view, then retain the full page for layout.
        # Cropping the layout input itself changes column grouping on sparse pages.
        view, _, _ = trim_white_margins(pixels)
        orientations = list(self.orientation.predict(view))
        if len(orientations) != 1:
            raise DocumentError(502, "Document orientation is unavailable")
        raw = orientations[0].json
        angle = int(raw.get("res", raw)["label_names"][0])
        if angle not in (0, 90, 180, 270):
            raise DocumentError(502, "Document orientation is unsupported")
        if angle:
            import numpy as np
            pixels = np.rot90(pixels, k=angle // 90).copy()
        return [OrientedPrediction(value, angle) for value in self.pipeline.predict(pixels, **kwargs)]


def reading_order(blocks: list[dict[str, Any]], width: int) -> list[int]:
    """Repair only clear, overlapping text columns; retain upstream order otherwise."""
    original = list(range(len(blocks)))
    if len(blocks) < 4 or any(block.get("block_label") not in ("text", "paragraph_title", "doc_title")
                              or not isinstance(block.get("block_bbox"), (list, tuple))
                              or len(block["block_bbox"]) != 4 for block in blocks):
        return original
    boxes = [block["block_bbox"] for block in blocks]
    if any(not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) for box in boxes):
        return original
    intervals = sorted((box[0], box[2]) for box in boxes)
    end = intervals[0][1]
    for start, stop in intervals[1:]:
        if start - end >= width * 0.08:
            left = [i for i, box in enumerate(boxes) if box[2] <= end]
            right = [i for i, box in enumerate(boxes) if box[0] >= start]
            if len(left) >= 2 and len(right) >= 2 and len(left) + len(right) == len(blocks):
                spans = [(min(boxes[i][1] for i in group), max(boxes[i][3] for i in group))
                         for group in (left, right)]
                overlap = min(s[1] for s in spans) - max(s[0] for s in spans)
                if overlap >= min(s[1] - s[0] for s in spans) * 0.5:
                    return [left[i] for i in reading_order([blocks[i] for i in left], width)] + [
                        right[i] for i in reading_order([blocks[i] for i in right], width)]
        end = max(end, stop)
    return original


def original_bbox(box: list[float], angle: int, width: int, height: int) -> list[float]:
    """Invert Paddle's counterclockwise document rotation into input pixel coordinates."""
    x0, y0, x1, y1 = box
    if angle in (-1, 0):
        return box
    if angle == 90:
        return [width - y1, x0, width - y0, x1]
    if angle == 180:
        return [width - x1, height - y1, width - x0, height - y0]
    if angle == 270:
        return [y0, height - x1, y1, height - x0]
    raise ValueError("Unsupported document rotation")


def trim_white_margins(pixels: Any) -> tuple[Any, int, int]:
    """Remove only exact white margins, keeping padding and every non-white pixel."""
    shape = getattr(pixels, "shape", ())
    if len(shape) != 3:
        return pixels, 0, 0
    foreground = (pixels != 255).any(axis=2)
    rows = foreground.any(axis=1).nonzero()[0]
    columns = foreground.any(axis=0).nonzero()[0]
    if len(rows) == 0 or len(columns) == 0:
        return pixels, 0, 0
    height, width = shape[:2]
    left, top = max(0, int(columns[0]) - 32), max(0, int(rows[0]) - 32)
    right, bottom = min(width, int(columns[-1]) + 33), min(height, int(rows[-1]) + 33)
    if (right - left) * (bottom - top) >= width * height * 0.75:
        return pixels, 0, 0
    return pixels[top:bottom, left:right].copy(), left, top


def recognize_page(pipeline: Any, pixels: Any, number: int, width: int, height: int) -> dict[str, Any]:
    try:
        predictions = pipeline.predict(pixels, use_queues=False, use_layout_detection=True)
        if len(predictions) != 1:
            raise ValueError("missing page")
        prediction = predictions[0]
        raw = prediction.json
        result = raw.get("res", raw)
        blocks = result["parsing_res_list"]
        angle = getattr(prediction, "input_rotation", result.get("doc_preprocessor_res", {}).get("angle", 0))
        if type(angle) is not int or angle not in (-1, 0, 90, 180, 270):
            raise ValueError("Unsupported document rotation")
        oriented_width, oriented_height = ((height, width) if angle in (90, 270) else (width, height))
        if isinstance(blocks, list):
            order = reading_order(blocks, oriented_width)
            if order != list(range(len(blocks))):
                native_blocks = prediction["parsing_res_list"]
                prediction["parsing_res_list"] = [native_blocks[i] for i in order]
                raw = prediction.json
                blocks = raw.get("res", raw)["parsing_res_list"]
        markdown = prediction.markdown["markdown_texts"]
        if not isinstance(blocks, list) or len(blocks) > 4096 or not isinstance(markdown, str):
            raise ValueError("invalid page")
        parsed = []
        for block in blocks:
            kind, text = block["block_label"], block["block_content"]
            if not isinstance(kind, str) or not kind or not isinstance(text, str):
                raise ValueError("invalid block")
            bbox = block.get("block_bbox")
            if bbox is not None:
                bbox = list(bbox)
                if len(bbox) != 4 or any(not math.isfinite(float(value)) for value in bbox):
                    raise ValueError("invalid geometry")
                x0, y0, x1, y1 = bbox
                if not (0 <= x0 <= x1 <= oriented_width and 0 <= y0 <= y1 <= oriented_height):
                    raise ValueError("geometry outside page")
                bbox = original_bbox([float(value) for value in bbox], angle, width, height)
            parsed.append({"kind": kind, "text": text, "bbox": bbox})
        text = "\n".join(plain_block(block) for block in parsed)
        return {
            "number": number, "width_px": width, "height_px": height,
            "empty": not text.strip() and not markdown.strip() and not parsed,
            "complete": True, "text": text, "markdown": markdown, "blocks": parsed,
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DocumentError(502, "OCR pipeline returned an invalid page") from None


def assemble(pages: list[dict[str, Any]], config: dict[str, Any], max_output_bytes: int) -> dict[str, Any]:
    if not pages or [page["number"] for page in pages] != list(range(1, len(pages) + 1)) or any(
        page["complete"] is not True for page in pages
    ):
        raise DocumentError(502, "OCR document is incomplete")
    result = {
        "schema_version": 1, "model": config["model_id"], "revision": config["revision"],
        "precision": config["precision"], "coordinate_units": "pixels", "pages": pages,
        "complete": True, "truncated": False, "text": "\n\n".join(p["text"] for p in pages),
        "markdown": "\n\n".join(p["markdown"] for p in pages),
    }
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > max_output_bytes:
        raise DocumentError(502, "OCR output exceeds the byte limit")
    return result


def image_pixels(image: Any) -> Any:
    import numpy as np
    return np.asarray(image)[..., ::-1].copy()  # Paddle image inputs use BGR order.


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if close is not None:
        close()


def pdf_pages(
    path: Path, *, max_pages: int, max_pixels: int, dpi: int,
    pdf_factory: Callable[[str], Any] | None = None,
    pixels: Callable[[Any], Any] = image_pixels,
) -> Iterator[tuple[int, int, int, Any]]:
    if pdf_factory is None:
        import pypdfium2 as pdfium
        pdf_factory = pdfium.PdfDocument
    try:
        document = pdf_factory(str(path))
    except Exception:
        raise DocumentError(422, "PDF is corrupt or encrypted") from None
    try:
        count = len(document)
        if not 1 <= count <= max_pages:
            raise DocumentError(413, "PDF page count exceeds the limit")
        for index in range(count):
            page = document[index]
            bitmap = image = None
            try:
                w, h = page.get_size()
                width, height = math.ceil(w * dpi / 72), math.ceil(h * dpi / 72)
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise DocumentError(413, "rendered PDF page exceeds the pixel limit")
                bitmap = page.render(scale=dpi / 72, rev_byteorder=True)
                image = bitmap.to_pil().convert("RGB")
                yield index + 1, width, height, pixels(image)
            except DocumentError:
                raise
            except Exception:
                raise DocumentError(422, "PDF page could not be decoded") from None
            finally:
                _close(image)
                _close(bitmap)
                page.close()
    finally:
        document.close()


def image_page(path: Path, max_pixels: int) -> tuple[int, int, int, Any]:
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise DocumentError(413, "image exceeds the pixel limit")
            if getattr(image, "n_frames", 1) != 1:
                raise DocumentError(422, "animated images are unsupported")
            with ImageOps.exif_transpose(image) as oriented:
                with oriented.convert("RGB") as rgb:
                    return 1, rgb.width, rgb.height, image_pixels(rgb)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError(422, "image is corrupt or unsupported") from None


def functional_warmup(pipeline: Any) -> bool:
    from PIL import Image, ImageDraw, ImageFont
    with Image.new("RGB", (480, 160), "white") as image:
        draw = ImageDraw.Draw(image)
        draw.text((30, 45), "12345", fill="black", font=ImageFont.load_default(size=48))
        page = recognize_page(pipeline, image_pixels(image), 1, image.width, image.height)
        return "".join(page["text"].split()) == "12345"
