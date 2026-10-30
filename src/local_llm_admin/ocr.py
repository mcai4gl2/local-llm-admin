"""OCR contracts and loopback adapter; native OCR packages stay in the host runtime."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import struct
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Self

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)
from starlette.requests import Request

from .config import Settings


class OcrError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OcrLimits:
    """Conservative contract ceilings; deployment must qualify the peak reservation."""
    max_bytes: int = 10 * 1024 * 1024
    max_pixels: int = 16_000_000
    max_pages: int = 16
    render_dpi: int = 144
    page_timeout_s: float = 45.0
    document_timeout_s: float = 240.0
    max_output_bytes: int = 2 * 1024 * 1024
    queue_depth: int = 2
    queue_timeout_s: float = 30.0

    @property
    def max_body_bytes(self) -> int:
        return ((self.max_bytes + 2) // 3) * 4 + 64 * 1024


DEFAULT_LIMITS = OcrLimits()


@dataclass(frozen=True)
class OcrDocument:
    data: bytes = field(repr=False)
    media_type: str


def _image_dimensions(data: bytes, media_type: str) -> tuple[int, int]:
    if media_type == "image/png":
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise OcrError(400, "invalid PNG document")
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        raise OcrError(400, "invalid JPEG document")
    index = 2
    while index < len(data):
        if data[index] != 0xFF:
            break
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA) or index + 2 > len(data):
            break
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8:
                break
            height, width = struct.unpack(">HH", data[index + 3:index + 7])
            return width, height
        index += length
    raise OcrError(400, "invalid or unsupported JPEG document")


def document_from_bytes(
    data: bytes, media_type: str, limits: OcrLimits = DEFAULT_LIMITS,
) -> OcrDocument:
    if len(data) > limits.max_bytes:
        raise OcrError(413, "document exceeds the decoded byte limit")
    if not data:
        raise OcrError(400, "document is empty")
    if media_type not in ("image/png", "image/jpeg", "application/pdf"):
        raise OcrError(400, "supported documents are PNG, JPEG and PDF")
    if media_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise OcrError(400, "invalid PDF document")
    else:
        width, height = _image_dimensions(data, media_type)
        if not width or not height:
            raise OcrError(400, "invalid image dimensions")
        if width * height > limits.max_pixels:
            raise OcrError(413, "document exceeds the image pixel limit")
    return OcrDocument(data, media_type)


def _data_url(value: Any, media: tuple[str, ...], limits: OcrLimits) -> OcrDocument:
    if not isinstance(value, str) or not value.startswith("data:"):
        raise OcrError(400, "only base64 document data URLs are supported")
    prefix, separator, encoded = value.partition(",")
    media_type = prefix[5:].removesuffix(";base64")
    if not separator or not prefix.endswith(";base64") or media_type not in media:
        raise OcrError(400, "unsupported document data URL")
    if len(encoded) > ((limits.max_bytes + 2) // 3) * 4:
        raise OcrError(413, "document exceeds the encoded byte limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise OcrError(400, "document contains invalid base64") from None
    return document_from_bytes(raw, media_type, limits)


def parse_chat(
    body: Any, settings: Settings, limits: OcrLimits = DEFAULT_LIMITS,
) -> tuple[OcrDocument, str]:
    if not isinstance(body, dict):
        raise OcrError(400, "body must be a JSON object")
    if body.keys() - {"model", "messages", "stream", "response_format"}:
        raise OcrError(400, "unsupported OCR completion parameter")
    if body.get("model") != settings.ocr_model_id:
        raise OcrError(400, "unknown OCR model")
    if body.get("stream", False) is not False:
        raise OcrError(400, "OCR supports non-streaming completions only")
    response_format = body.get("response_format", {"type": "text"})
    if response_format not in ({"type": "text"}, {"type": "json_object"}):
        raise OcrError(400, "supported response formats are text and json_object")
    mode = "json" if response_format == {"type": "json_object"} else "markdown"
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        raise OcrError(400, "OCR accepts one user message")
    message = messages[0]
    if not isinstance(message, dict) or message.keys() != {"role", "content"} or message["role"] != "user":
        raise OcrError(400, "OCR accepts one user message")
    parts = message["content"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= 2:
        raise OcrError(400, "OCR accepts one document and an optional OCR instruction")
    documents = []
    for part in parts:
        if not isinstance(part, dict):
            raise OcrError(400, "invalid OCR content part")
        if part.get("type") == "text" and part.keys() == {"type", "text"}:
            if part["text"] not in ("OCR", "Recognize this document.", "Extract document as Markdown.", "Extract document as JSON."):
                raise OcrError(400, "only document recognition instructions are supported")
            requested = {"Extract document as Markdown.": "markdown", "Extract document as JSON.": "json"}.get(part["text"])
            if requested and requested != mode:
                raise OcrError(400, "OCR instruction conflicts with the response format")
        elif part.get("type") == "image_url" and part.keys() == {"type", "image_url"}:
            image = part["image_url"]
            if not isinstance(image, dict) or image.keys() != {"url"}:
                raise OcrError(400, "image_url must contain only a data URL")
            documents.append(_data_url(image["url"], ("image/png", "image/jpeg"), limits))
        elif part.get("type") == "file" and part.keys() == {"type", "file"}:
            file = part["file"]
            if not isinstance(file, dict) or file.keys() - {"file_data", "filename"}:
                raise OcrError(400, "PDF input requires inline file_data")
            if "filename" in file and (not isinstance(file["filename"], str) or len(file["filename"]) > 255):
                raise OcrError(400, "invalid PDF filename metadata")
            documents.append(_data_url(file.get("file_data"), ("application/pdf",), limits))
        else:
            raise OcrError(400, "unsupported OCR content part")
    if len(documents) != 1:
        raise OcrError(400, "OCR accepts exactly one document")
    return documents[0], mode


async def read_limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (len(declared) > 20 or not declared.isdigit() or int(declared) > limit):
        raise OcrError(413, "request exceeds the byte limit")
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > limit:
            raise OcrError(413, "request exceeds the byte limit")
        chunks.extend(chunk)
    return bytes(chunks)


class _ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OcrBlock(_ResultModel):
    kind: Annotated[str, Field(min_length=1, max_length=128)]
    text: str
    bbox: tuple[float, float, float, float] | None = None


class OcrPage(_ResultModel):
    number: Annotated[StrictInt, Field(ge=1)]
    width_px: Annotated[StrictInt, Field(ge=1)]
    height_px: Annotated[StrictInt, Field(ge=1)]
    empty: StrictBool
    complete: Literal[True]
    text: str
    markdown: str
    blocks: list[OcrBlock]

    @model_validator(mode="after")
    def check_geometry(self) -> Self:
        if self.empty != (not self.text.strip() and not self.markdown.strip() and not self.blocks):
            raise ValueError("inconsistent empty page")
        for block in self.blocks:
            if block.bbox is not None:
                x0, y0, x1, y1 = block.bbox
                if not 0 <= x0 <= x1 <= self.width_px or not 0 <= y0 <= y1 <= self.height_px:
                    raise ValueError("block geometry is outside the rendered page")
        return self


class OcrResult(_ResultModel):
    schema_version: Literal[1]
    model: str
    revision: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    precision: Literal["bf16", "8bit"]
    coordinate_units: Literal["pixels"]
    pages: Annotated[list[OcrPage], Field(min_length=1)]
    complete: Literal[True]
    truncated: Literal[False]
    text: str
    markdown: str

    @model_validator(mode="after")
    def check_order(self) -> Self:
        if [p.number for p in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("pages must be complete and in source order")
        if self.text != "\n\n".join(p.text for p in self.pages):
            raise ValueError("document text does not match its pages")
        if self.markdown != "\n\n".join(p.markdown for p in self.pages):
            raise ValueError("document Markdown does not match its pages")
        return self


def validate_result(
    body: Any, settings: Settings, limits: OcrLimits = DEFAULT_LIMITS,
) -> OcrResult:
    try:
        if len(json.dumps(body, ensure_ascii=False).encode()) > limits.max_output_bytes:
            raise ValueError("output too large")
        result = OcrResult.model_validate(body)
        if (result.model != settings.ocr_model_id or result.revision != settings.ocr_model_revision
                or result.precision != settings.ocr_precision or len(result.pages) > limits.max_pages):
            raise ValueError("unexpected model or page count")
        if any(p.width_px * p.height_px > limits.max_pixels for p in result.pages):
            raise ValueError("rendered page too large")
        return result
    except (ValueError, TypeError, ValidationError):
        # Never include Pydantic's input values, filenames or recognized content.
        raise OcrError(502, "OCR runtime returned an invalid or incomplete document") from None


class OcrClient:
    """One shared document slot and bounded waiting queue for every admin/gateway caller.

    Failed cancellation freezes admission until a new, functionally ready host generation
    is observed. A client timeout never proves the VLM stopped.
    """

    def __init__(
        self, settings: Settings, *, transport: httpx.MockTransport | None = None,
        limits: OcrLimits = DEFAULT_LIMITS,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.limits = limits
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[OcrResult]] = set()
        self._active: str | None = None
        self._uncertain = False
        self._instance: str | None = None

    @property
    def busy(self) -> bool:
        return bool(self._tasks) or self._uncertain

    @property
    def admission(self) -> dict[str, Any]:
        return {"active": self._active is not None, "queued": max(
            0, len(self._tasks) - int(self._active is not None),
        ), "uncertain": self._uncertain, "queue_limit": self.limits.queue_depth}

    async def recognize(self, document: OcrDocument) -> OcrResult:
        if self._uncertain:
            raise OcrError(503, "OCR work may still be executing; restart the service")
        if len(self._tasks) >= 1 + self.limits.queue_depth:
            raise OcrError(429, "OCR document queue is full")
        task = asyncio.create_task(self._execute(document))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The task retains admission while cancellation is acknowledged upstream.
            task.cancel()
            raise

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _abort(self, request_id: str) -> None:
        self._uncertain = True
        try:
            async with asyncio.timeout(5.0):
                async with httpx.AsyncClient(
                    base_url=self.settings.ocr_api_base, timeout=5.0, transport=self.transport,
                ) as client:
                    response = await client.post(f"/requests/{request_id}/cancel")
                    body = response.json()
                    if (response.status_code == 200 and isinstance(body, dict)
                            and body.get("request_id") == request_id and body.get("stopped") is True):
                        self._uncertain = False
        except (TimeoutError, httpx.HTTPError, ValueError):
            pass

    async def _execute(self, document: OcrDocument) -> OcrResult:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self.limits.queue_timeout_s)
        except TimeoutError:
            raise OcrError(429, "OCR queue wait deadline exceeded") from None
        request_id = uuid.uuid4().hex
        try:
            if self._uncertain:
                raise OcrError(503, "OCR work may still be executing; restart the service")
            self._active = request_id
            try:
                async with asyncio.timeout(self.limits.document_timeout_s):
                    return await self._forward(request_id, document)
            except asyncio.CancelledError:
                await self._abort(request_id)
                raise
            except (TimeoutError, httpx.TimeoutException):
                await self._abort(request_id)
                raise OcrError(504, "OCR document deadline exceeded") from None
            except httpx.HTTPError:
                await self._abort(request_id)
                raise OcrError(503, "OCR runtime is unavailable") from None
        finally:
            self._active = None
            self._lock.release()

    async def _forward(self, request_id: str, document: OcrDocument) -> OcrResult:
        payload = {
            "request_id": request_id, "media_type": document.media_type,
            "data": base64.b64encode(document.data).decode("ascii"),
        }
        async with httpx.AsyncClient(
            base_url=self.settings.ocr_api_base, transport=self.transport,
            timeout=self.limits.document_timeout_s,
        ) as client:
            async with client.stream("POST", "/recognize", json=payload) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > self.limits.max_output_bytes:
                        raise OcrError(502, "OCR output exceeds the byte limit")
                    raw.extend(chunk)
                if response.status_code != 200:
                    messages = {
                        400: "invalid OCR document", 413: "OCR document exceeds runtime limits",
                        415: "unsupported OCR document", 422: "corrupt or encrypted OCR document",
                        429: "OCR runtime is busy", 503: "OCR runtime is unavailable",
                    }
                    code = response.status_code if response.status_code in messages else 502
                    raise OcrError(code, messages.get(code, "OCR runtime failed"))
                try:
                    body = json.loads(raw)
                except ValueError:
                    raise OcrError(502, "OCR runtime returned invalid JSON") from None
                return validate_result(body, self.settings, self.limits)

    def warmup_sync(self, *, restarted: bool = False) -> bool:
        """The host's readiness requires its own recognition warmup to have passed."""
        try:
            with httpx.Client(
                base_url=self.settings.ocr_api_base, transport=self.transport, timeout=2.0,
            ) as client:
                response = client.get("/ready")
                body = response.json()
                if response.status_code != 200 or not isinstance(body, dict):
                    return False
                instance = body.get("instance")
                if body.get("ready") is not True or body.get("warmup") is not True or not isinstance(instance, str) or not instance:
                    return False
                if self._uncertain and not restarted and (self._instance is None or instance == self._instance):
                    return False
                self._instance = instance
                self._uncertain = False
                return True
        except (httpx.HTTPError, ValueError):
            return False

    def unavailable_reason_sync(self) -> str | None:
        if self._uncertain:
            return "failed"
        try:
            with httpx.Client(base_url=self.settings.ocr_api_base, transport=self.transport, timeout=2) as client:
                response = client.get("/ready")
                body = response.json()
                if response.status_code == 200 and isinstance(body, dict) and body.get("ready") is True:
                    return None
                if isinstance(body, dict) and body.get("state") == "loading":
                    return "loading"
        except (httpx.HTTPError, ValueError):
            pass
        return "failed"
