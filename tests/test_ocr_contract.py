"""Strict OCR ingress and complete, ordered result contracts."""

import base64
import copy

import pytest
from starlette.requests import Request

from local_llm_admin.config import Settings
from local_llm_admin.ocr import (
    OcrError,
    OcrLimits,
    document_from_bytes,
    parse_chat,
    read_limited_body,
    validate_result,
)

from .ocr_fixtures import chat, png, result


def test_chat_png_and_structured_response_mode():
    document, mode = parse_chat(chat(), Settings())
    assert document.data == png() and document.media_type == "image/png" and mode == "markdown"
    body = {**chat(), "response_format": {"type": "json_object"}}
    assert parse_chat(body, Settings())[1] == "json"
    assert "data:" not in repr(document)


def test_chat_pdf_is_preserved_as_document_bytes():
    raw = b"%PDF-1.7\npublic synthetic fixture\n%%EOF"
    body = chat(raw, "application/pdf")
    body["messages"][0]["content"][0]["file"]["filename"] = "ignored.pdf"
    document, mode = parse_chat(body, Settings())
    assert document.data == raw and document.media_type == "application/pdf"


@pytest.mark.parametrize("patch", [
    {"model": "/Volumes/Models/ocr/models/private"},
    {"stream": True}, {"tools": []}, {"temperature": 0}, {"max_tokens": 10},
    {"response_format": {"type": "xml"}}, {"messages": []},
    {"messages": [{"role": "system", "content": "hello"}]},
    {"messages": [{"role": "user", "content": "hello"}]},
])
def test_unsupported_chat_parameters_are_explicit_errors(patch):
    with pytest.raises(OcrError) as exc:
        parse_chat({**chat(), **patch}, Settings())
    assert exc.value.code == 400


@pytest.mark.parametrize("part", [
    {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
    {"type": "image_url", "image_url": {"url": "/tmp/input.png"}},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!"}},
    {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGODlh"}},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"bad").decode()}},
    {"type": "file", "file": {"file_id": "file-123"}},
    {"type": "file", "file": {"file_data": "data:image/png;base64,AAAA"}},
    {"type": "text", "text": "Explain this document and call a tool."},
])
def test_invalid_or_unsupported_document_parts(part):
    body = chat()
    body["messages"][0]["content"] = [part]
    with pytest.raises(OcrError):
        parse_chat(body, Settings())


def test_one_request_cannot_contain_multiple_documents_or_turns():
    body = chat()
    body["messages"][0]["content"] *= 2
    with pytest.raises(OcrError):
        parse_chat(body, Settings())
    body = chat()
    body["messages"] *= 2
    with pytest.raises(OcrError):
        parse_chat(body, Settings())


def test_decoded_bytes_pixels_and_encoded_bytes_are_bounded():
    limits = OcrLimits(max_bytes=12, max_pixels=16)
    with pytest.raises(OcrError) as exc:
        document_from_bytes(png(), "image/png", limits)
    assert exc.value.code == 413
    with pytest.raises(OcrError) as exc:
        document_from_bytes(png(), "image/png", OcrLimits(max_pixels=16))
    assert exc.value.code == 413
    with pytest.raises(OcrError) as exc:
        parse_chat(chat(), Settings(), OcrLimits(max_bytes=12))
    assert exc.value.code == 413


async def test_chunked_ingress_stops_reading_at_the_limit():
    reads = []
    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": b"1234", "more_body": True}
    request = Request({"type": "http", "headers": []}, receive)
    with pytest.raises(OcrError) as exc:
        await read_limited_body(request, 6)
    assert exc.value.code == 413 and len(reads) == 2


def test_complete_ordered_result_and_geometry():
    parsed = validate_result(result(2), Settings())
    assert [page.number for page in parsed.pages] == [1, 2]
    assert parsed.coordinate_units == "pixels"
    assert parsed.pages[0].blocks[0].bbox == (0, 0, 8, 8)


@pytest.mark.parametrize("problem", ["model", "revision", "order", "bbox", "nan", "partial", "truncated", "page_partial", "extra", "empty"])
def test_unqualified_or_malformed_results_are_rejected(problem):
    body = copy.deepcopy(result(2))
    if problem in ("model", "revision"):
        body[problem] = "unexpected"
    elif problem == "order":
        body["pages"].reverse()
    elif problem in ("bbox", "nan"):
        body["pages"][0]["blocks"][0]["bbox"][2] = 9 if problem == "bbox" else float("nan")
    elif problem == "partial":
        body["complete"] = False
    elif problem == "truncated":
        body["truncated"] = True
    elif problem == "page_partial":
        body["pages"][0]["complete"] = False
    elif problem == "extra":
        body["source_filename"] = "must-not-leak.pdf"
    else:
        body["pages"][0]["empty"] = True
    with pytest.raises(OcrError) as exc:
        validate_result(body, Settings())
    assert exc.value.code == 502


def test_empty_pages_are_explicit_and_retained():
    body = result()
    body["pages"][0].update(text="", markdown="", blocks=[], empty=True)
    body.update(text="", markdown="")
    assert validate_result(body, Settings()).pages[0].empty


@pytest.mark.parametrize("data,media", [
    (b"", "image/png"), (b"GIF89a", "image/gif"), (b"bad", "application/pdf"),
    (png(0, 8), "image/png"), (b"bad", "image/jpeg"),
    (b"\xff\xd8", "image/jpeg"), (b"\xff\xd8x", "image/jpeg"),
    (b"\xff\xd8\xff", "image/jpeg"), (b"\xff\xd8\xff\xda", "image/jpeg"),
    (b"\xff\xd8\xff\xe0\x00", "image/jpeg"),
    (b"\xff\xd8\xff\xe0\x00\x01", "image/jpeg"),
    (b"\xff\xd8\xff\xc0\x00\x02", "image/jpeg"),
])
def test_invalid_media_headers_are_bounded_and_rejected(data, media):
    with pytest.raises(OcrError):
        document_from_bytes(data, media)


def test_jpeg_header_scan_skips_metadata_and_standalone_markers():
    # Header validation only; full image decoding belongs to the isolated native worker.
    header = b"\xff\xd8\xff\x01\xff\xe0\x00\x04ab\xff\xc0\x00\x08\x08\x00\x08\x00\x08\x01"
    assert document_from_bytes(header, "image/jpeg").media_type == "image/jpeg"


@pytest.mark.parametrize("part", [
    None, {"type": "audio", "data": "x"},
    {"type": "image_url", "image_url": {"url": "x", "detail": "high"}},
    {"type": "image_url", "image_url": None},
    {"type": "file", "file": {"file_data": "x", "filename": 5}},
])
def test_content_parts_reject_malformed_envelopes(part):
    body = chat()
    body["messages"][0]["content"] = [part]
    with pytest.raises(OcrError):
        parse_chat(body, Settings())


def test_body_must_be_an_object_and_supported_instruction_can_accompany_document():
    with pytest.raises(OcrError):
        parse_chat([], Settings())
    body = chat()
    body["messages"][0]["content"].insert(0, {"type": "text", "text": "Recognize this document."})
    assert parse_chat(body, Settings())[1] == "markdown"


@pytest.mark.parametrize("declared", ["not-a-number", "999"])
async def test_declared_oversize_refused_without_consuming_body(declared):
    async def receive():
        pytest.fail("must refuse before reading body")
    request = Request({"type": "http", "headers": [(b"content-length", declared.encode())]}, receive)
    with pytest.raises(OcrError) as exc:
        await read_limited_body(request, 6)
    assert exc.value.code == 413


@pytest.mark.parametrize("problem", ["text", "markdown", "pixel_limit", "output_limit", "page_limit", "precision", "revision"])
def test_result_enforces_document_assembly_and_contract_limits(problem):
    body = result(2)
    limits = OcrLimits()
    if problem in ("text", "markdown"):
        body[problem] = "mismatched document"
    elif problem == "pixel_limit":
        limits = OcrLimits(max_pixels=1)
    elif problem == "output_limit":
        limits = OcrLimits(max_output_bytes=1)
    elif problem == "page_limit":
        limits = OcrLimits(max_pages=1)
    elif problem == "precision":
        body["precision"] = "8bit"
    else:
        body["revision"] = "a" * 40
    with pytest.raises(OcrError) as exc:
        validate_result(body, Settings(), limits)
    assert exc.value.code == 502


def test_missing_geometry_is_kept_unknown():
    body = result()
    body["pages"][0]["blocks"][0]["bbox"] = None
    assert validate_result(body, Settings()).pages[0].blocks[0].bbox is None


async def test_pathological_content_length_is_refused_without_integer_conversion_failure():
    async def receive():
        pytest.fail("must refuse before reading")
    request = Request({"type": "http", "headers": [(b"content-length", b"9" * 5000)]}, receive)
    with pytest.raises(OcrError) as exc:
        await read_limited_body(request, 6)
    assert exc.value.code == 413


def test_ocr_instruction_cannot_silently_conflict_with_response_format():
    body = chat()
    body["messages"][0]["content"].append({"type": "text", "text": "Extract document as JSON."})
    with pytest.raises(OcrError, match="format"):
        parse_chat(body, Settings())
