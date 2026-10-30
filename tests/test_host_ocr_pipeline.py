"""Full-pipeline assembly adapter tested without importing native OCR packages."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

HOST = Path(__file__).resolve().parents[1] / "host/ocr/app"


def load(name: str) -> Any:
    path = HOST / (name + ".py")
    spec = importlib.util.spec_from_loader("host_ocr_" + name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[spec.name] = module
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


class Pipeline:
    def __init__(self, blocks: Any = None, markdown: Any = "Hello") -> None:
        self.blocks = blocks if blocks is not None else [
            {"block_label": "text", "block_content": "Hello", "block_bbox": [0, 0, 100, 100]},
        ]
        self.markdown = markdown
        self.calls = []

    def predict(self, image: Any, **kwargs: Any) -> list[Any]:
        self.calls.append((image, kwargs))
        return [SimpleNamespace(json={"res": {"parsing_res_list": self.blocks}},
                                markdown={"markdown_texts": self.markdown})]


def test_pipeline_uses_layout_and_reading_order_without_inventing_confidence():
    module = load("pipeline")
    pipeline = Pipeline([
        {"block_label": "title", "block_content": "Heading", "block_bbox": [0, 0, 100, 20]},
        {"block_label": "table", "block_content": "<table>...</table>", "block_bbox": [0, 20, 100, 100]},
    ], "# Heading\n<table>...</table>")
    page = module.recognize_page(pipeline, "pixels", 2, 100, 100)
    assert page["number"] == 2 and page["text"] == "Heading\n..."
    assert [block["kind"] for block in page["blocks"]] == ["title", "table"]
    assert "confidence" not in page["blocks"][0]
    assert pipeline.calls[0][1]["use_queues"] is False
    assert pipeline.calls[0][1]["use_layout_detection"] is True


def test_empty_page_is_retained():
    module = load("pipeline")
    page = module.recognize_page(Pipeline([], ""), "pixels", 1, 100, 100)
    assert page["empty"] is True and page["complete"] is True and page["blocks"] == []


def test_table_text_has_rows_and_cells_while_structured_output_keeps_html():
    module = load("pipeline")
    html = "<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Tea &amp; rice</td><td>12</td></tr></table>"
    page = module.recognize_page(Pipeline([
        {"block_label": "table", "block_content": html, "block_bbox": [0, 0, 100, 100]},
    ], html), "pixels", 1, 100, 100)
    assert page["text"] == "Item Qty\nTea & rice 12"
    assert page["blocks"][0]["text"] == html and page["markdown"] == html


@pytest.mark.parametrize("blocks,markdown", [
    ([{"block_label": "text", "block_content": "x", "block_bbox": [-1, 0, 1, 1]}], "x"),
    ([{"block_label": "text", "block_content": "x", "block_bbox": [0, 0, 101, 1]}], "x"),
    ([{"block_label": "text", "block_content": None, "block_bbox": [0, 0, 1, 1]}], ""),
    ([{"block_label": "text", "block_content": "x", "block_bbox": [0, 0, float("nan"), 1]}], "x"),
    ([], None),
])
def test_pipeline_refuses_malformed_or_unqualified_output(blocks, markdown):
    module = load("pipeline")
    with pytest.raises(module.DocumentError):
        module.recognize_page(Pipeline(blocks, markdown), "pixels", 1, 100, 100)


def test_document_assembly_preserves_all_pages_and_bounds_output():
    module = load("pipeline")
    pages = [module.recognize_page(Pipeline(), "pixels", n, 100, 100) for n in (1, 2)]
    config = {"model_id": "local-ocr", "revision": "a" * 40, "precision": "bf16"}
    result = module.assemble(pages, config, 10000)
    assert len(result["pages"]) == 2 and result["text"] == "Hello\n\nHello"
    with pytest.raises(module.DocumentError):
        module.assemble(pages, config, 10)
    with pytest.raises(module.DocumentError):
        module.assemble(list(reversed(pages)), config, 10000)


def test_pdf_renderer_visits_pages_incrementally_and_closes_resources(tmp_path):
    module = load("pipeline")
    events = []
    class Bitmap:
        def to_pil(self):
            return SimpleNamespace(convert=lambda _: "pixels")
        def close(self):
            events.append("bitmap-close")
    class Page:
        def get_size(self):
            return (100, 200)
        def render(self, **kwargs):
            events.append("render")
            return Bitmap()
        def close(self):
            events.append("page-close")
    class Pdf:
        def __len__(self):
            return 2
        def __getitem__(self, i):
            events.append(i)
            return Page()
        def close(self):
            events.append("document-close")
    iterator = module.pdf_pages(tmp_path / "input.pdf", max_pages=3, max_pixels=1000000,
                                dpi=144, pdf_factory=lambda _: Pdf(), pixels=lambda image: image)
    assert next(iterator)[:3] == (1, 200, 400)
    assert events == [0, "render"]
    assert next(iterator)[:3] == (2, 200, 400)
    iterator.close()
    assert events[-3:] == ["bitmap-close", "page-close", "document-close"]


def test_encrypted_corrupt_or_over_limit_pdf_has_no_partial_success(tmp_path):
    module = load("pipeline")
    def encrypted(_):
        raise ValueError("private password/path")
    with pytest.raises(module.DocumentError) as error:
        list(module.pdf_pages(tmp_path / "input.pdf", max_pages=16, max_pixels=1000,
                              dpi=144, pdf_factory=encrypted, pixels=lambda p: p))
    assert error.value.code == 422 and "private" not in str(error.value)


def test_pipeline_keeps_raw_block_text_and_formats_markdown_separately(tmp_path, monkeypatch):
    module = load("pipeline")
    model = tmp_path / "model"
    layout = tmp_path / "layout"
    model.mkdir()
    layout.mkdir()
    captured = {}
    def constructor(**kwargs):
        captured.update(kwargs)
        return Pipeline()
    orientation = {}
    def classifier(**kwargs):
        orientation.update(kwargs)
        return SimpleNamespace(predict=lambda _: [])
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(
        PaddleOCRVL=constructor, DocImgOrientationClassification=classifier))
    module.build_pipeline({"layout_model_path": str(layout), "vlm_model_path": str(model),
                           "vlm_base": "http://127.0.0.1:8097/vlm/v1/", "pipeline_token": "test-token", "orientation_model_path": str(layout)})
    assert captured["use_doc_orientation_classify"] is False
    assert orientation["model_dir"] == str(layout)
    assert captured["format_block_content"] is False
    assert captured["vl_rec_server_url"].endswith("/vlm/v1/")


@pytest.mark.parametrize("text,expected", [
    (" $ x = (a + b) / 2 $", "x = (a + b) / 2"),
    ("$$1 + 2 = 3$$", "1 + 2 = 3"),
    ("$5", "$5"),
    ("$5 and $10", "$5 and $10"),
    ("The result is $x=2$.", "The result is $x=2$."),
])
def test_plain_text_removes_whole_math_presentation_without_dropping_currency(text, expected):
    assert load("pipeline").plain_block({"kind": "text", "text": text}) == expected


def test_clear_column_gutters_read_down_each_column_and_preserve_other_layouts():
    module = load("pipeline")
    blocks = [
        {"block_label": "text", "block_bbox": box}
        for box in ([20, 10, 200, 40], [400, 10, 580, 40],
                    [20, 70, 200, 100], [400, 70, 580, 100])
    ]
    assert module.reading_order(blocks, 600) == [0, 2, 1, 3]
    # A spanning heading, non-text anchor or absent geometry keeps upstream ordering.
    assert module.reading_order([{"block_label": "doc_title", "block_bbox": [20, 0, 580, 8]}, *blocks], 600) == list(range(5))
    assert module.reading_order([*blocks[:3], {**blocks[3], "block_label": "table"}], 600) == list(range(4))
    assert module.reading_order([*blocks[:3], {"block_label": "text"}], 600) == list(range(4))
    # Vertically separate text sections do not become columns merely because x differs.
    blocks[1]["block_bbox"] = [400, 200, 580, 230]
    blocks[3]["block_bbox"] = [400, 250, 580, 280]
    assert module.reading_order(blocks, 600) == list(range(4))


@pytest.mark.parametrize("angle,box,expected", [
    (0, [10, 20, 40, 60], [10, 20, 40, 60]),
    (90, [10, 20, 40, 60], [140, 10, 180, 40]),
    (180, [10, 20, 40, 60], [160, 40, 190, 80]),
    (270, [10, 20, 40, 60], [20, 60, 60, 90]),
])
def test_oriented_geometry_maps_to_original_input_pixels(angle, box, expected):
    assert load("pipeline").original_bbox(box, angle, 200, 100) == expected


def test_column_repair_preserves_matching_markdown_order():
    class Prediction(dict):
        @property
        def json(self):
            return {"res": {"parsing_res_list": self["parsing_res_list"]}}
        @property
        def markdown(self):
            return {"markdown_texts": "\n\n".join(b["block_content"] for b in self["parsing_res_list"])}
    blocks = [
        {"block_label": "text", "block_content": text, "block_bbox": box}
        for text, box in [("A", [20, 10, 200, 40]), ("C", [400, 10, 580, 40]),
                          ("B", [20, 70, 200, 100]), ("D", [400, 70, 580, 100])]
    ]
    prediction = Prediction(parsing_res_list=blocks)
    pipeline = SimpleNamespace(predict=lambda *a, **k: [prediction])
    page = load("pipeline").recognize_page(pipeline, "pixels", 1, 600, 200)
    assert page["text"] == "A\nB\nC\nD"
    assert page["markdown"] == "A\n\nB\n\nC\n\nD"


def test_recognition_maps_rotated_regions_and_keeps_original_page_dimensions():
    pipeline = Pipeline([{
        "block_label": "text", "block_content": "A", "block_bbox": [10, 20, 40, 60],
    }], "A")
    original = pipeline.predict
    def predict(*args, **kwargs):
        values = original(*args, **kwargs)
        values[0].json["res"]["doc_preprocessor_res"] = {"angle": 90}
        return values
    pipeline.predict = predict
    page = load("pipeline").recognize_page(pipeline, "pixels", 1, 200, 100)
    assert (page["width_px"], page["height_px"]) == (200, 100)
    assert page["blocks"][0]["bbox"] == [140, 10, 180, 40]
