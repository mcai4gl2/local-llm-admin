"""Real CPU rasterization with the candidate image/PDF pins; no GPU models required."""

import io

import pytest

from tests.test_host_ocr_pipeline import load

Image = pytest.importorskip("PIL.Image")
np = pytest.importorskip("numpy")
pytest.importorskip("pypdfium2")


def test_real_image_decode_bgr_pixels_corruption_and_pixel_limit(tmp_path):
    module = load("pipeline")
    path = tmp_path / "input.png"
    with Image.new("RGB", (8, 4), "red") as image:
        image.save(path)
    number, width, height, pixels = module.image_page(path, 32)
    assert (number, width, height) == (1, 8, 4)
    assert pixels[0, 0].tolist() == [0, 0, 255]
    with pytest.raises(module.DocumentError) as error:
        module.image_page(path, 31)
    assert error.value.code == 413
    path.write_bytes(b"corrupt PNG")
    with pytest.raises(module.DocumentError) as error:
        module.image_page(path, 32)
    assert error.value.code == 422


def test_real_scanned_pdf_renders_in_order_and_refuses_page_pixel_and_corrupt_limits(tmp_path):
    module = load("pipeline")
    path = tmp_path / "input.pdf"
    with Image.new("RGB", (100, 200), "white") as first, Image.new("RGB", (100, 200), "black") as second:
        first.save(path, "PDF", resolution=72, save_all=True, append_images=[second])
    pages = list(module.pdf_pages(path, max_pages=2, max_pixels=80000, dpi=144))
    assert [page[:3] for page in pages] == [(1, 200, 400), (2, 200, 400)]
    assert pages[0][3][100, 100].tolist() == [255, 255, 255]
    assert pages[1][3][100, 100].tolist() == [0, 0, 0]
    for limits in ({"max_pages": 1, "max_pixels": 80000}, {"max_pages": 2, "max_pixels": 79999}):
        with pytest.raises(module.DocumentError) as error:
            list(module.pdf_pages(path, dpi=144, **limits))
        assert error.value.code == 413
    path.write_bytes(b"%PDF-1.7\ninvalid encrypted/corrupt stream\n%%EOF")
    with pytest.raises(module.DocumentError) as error:
        list(module.pdf_pages(path, max_pages=2, max_pixels=80000, dpi=144))
    assert error.value.code == 422


def test_real_animated_png_is_not_silently_reduced_to_first_frame(tmp_path):
    module = load("pipeline")
    path = tmp_path / "animated.png"
    with Image.new("RGB", (8, 8), "white") as first, Image.new("RGB", (8, 8), "black") as second:
        output = io.BytesIO()
        first.save(output, "PNG", save_all=True, append_images=[second], duration=100)
    path.write_bytes(output.getvalue())
    with pytest.raises(module.DocumentError) as error:
        module.image_page(path, 100)
    assert error.value.code == 422


def test_exact_white_margin_trim_preserves_faint_marks_and_original_coordinates():
    module = load("pipeline")
    pixels = np.full((400, 600, 3), 255, dtype=np.uint8)
    pixels[150:180, 200:350] = 0
    # Even a very faint mark is content, rather than disposable background.
    pixels[100, 100] = 254
    cropped, left, top = module.trim_white_margins(pixels)
    assert left == 68 and top == 68
    assert cropped.shape == (144, 314, 3)
    assert (cropped[32, 32] == 254).all()
    assert module.trim_white_margins(np.full((100, 100, 3), 255, dtype=np.uint8))[1:] == (0, 0)
    pixels[0, 0] = 254
    _, left, top = module.trim_white_margins(pixels)
    assert left == 0 and top == 0


def test_crop_and_orientation_keep_original_document_geometry():
    from types import SimpleNamespace
    module = load("pipeline")
    pixels = np.full((400, 600, 3), 255, dtype=np.uint8)
    pixels[100, 100] = 254
    pixels[150:180, 200:350] = 0
    prediction = SimpleNamespace(
        json={"res": {"doc_preprocessor_res": {"angle": 90}, "parsing_res_list": [
            {"block_label": "text", "block_content": "A", "block_bbox": [10, 20, 40, 60]}]}},
        markdown={"markdown_texts": "A"},
    )
    seen = {}
    def classify(view):
        seen["orientation_shape"] = view.shape
        return [SimpleNamespace(json={"res": {"label_names": ["90"]}})]
    def recognize(view, **kwargs):
        seen["layout_shape"] = view.shape
        return [prediction]
    pipeline = module.DocumentPipeline(SimpleNamespace(predict=recognize),
                                       SimpleNamespace(predict=classify))
    page = module.recognize_page(pipeline, pixels, 1, 600, 400)
    assert seen["orientation_shape"] == (144, 314, 3)
    assert seen["layout_shape"] == (600, 400, 3)
    assert (page["width_px"], page["height_px"]) == (600, 400)
    assert page["blocks"][0]["bbox"] == [540, 10, 580, 40]
