"""Strict, bounded media checks for private trip attachments."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from personal_travel.services.source_parser import SourceParseError, validate_pdf

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TEXT_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_ATTACHMENT_TYPES = {"text/plain", "application/pdf", "image/jpeg", "image/png"}


class AttachmentValidationError(Exception):
    """Safe fixed-code validation failure; never contains document-controlled text."""


def validate_attachment(path: Path, media_type: str, data: bytes) -> None:
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentValidationError("invalid_size")
    if media_type not in ALLOWED_ATTACHMENT_TYPES:
        raise AttachmentValidationError("unsupported_media_type")
    if media_type == "text/plain":
        if len(data) > MAX_ATTACHMENT_TEXT_BYTES or data.startswith(b"%PDF-"):
            raise AttachmentValidationError("invalid_text")
        try:
            if not data.decode("utf-8").strip():
                raise AttachmentValidationError("invalid_text")
        except UnicodeDecodeError as exc:
            raise AttachmentValidationError("invalid_text") from exc
        return
    if media_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise AttachmentValidationError("invalid_pdf")
        try:
            validate_pdf(path)
        except SourceParseError as exc:
            raise AttachmentValidationError("invalid_pdf") from exc
        return
    if media_type == "image/png":
        _validate_png(data)
        return
    _validate_jpeg(data)


def _check_pixels(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise AttachmentValidationError("image_dimensions_exceeded")


def _validate_png(data: bytes) -> None:
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 45 or not data.startswith(signature):
        raise AttachmentValidationError("invalid_png")
    offset = len(signature)
    seen_header = False
    seen_data = False
    seen_end = False
    width = height = 0
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise AttachmentValidationError("invalid_png")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise AttachmentValidationError("invalid_png")
        if not seen_header:
            if kind != b"IHDR" or length != 13:
                raise AttachmentValidationError("invalid_png")
            width, height = struct.unpack_from(">II", payload)
            bit_depth, color_type, compression, filtering, interlace = payload[8:13]
            if (
                bit_depth not in {1, 2, 4, 8, 16}
                or color_type not in {0, 2, 3, 4, 6}
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise AttachmentValidationError("invalid_png")
            _check_pixels(width, height)
            seen_header = True
        elif kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise AttachmentValidationError("animated_image_unsupported")
        elif kind == b"IDAT":
            seen_data = True
        elif kind == b"IEND":
            if length != 0 or end != len(data):
                raise AttachmentValidationError("invalid_png")
            seen_end = True
            break
        elif (
            kind
            and 65 <= kind[0] <= 90
            and kind
            not in {
                b"PLTE",
            }
        ):
            # Unknown critical chunks can change image interpretation.
            raise AttachmentValidationError("invalid_png")
        offset = end
    if not seen_header or not seen_data or not seen_end:
        raise AttachmentValidationError("invalid_png")


def _validate_jpeg(data: bytes) -> None:
    if len(data) < 16 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise AttachmentValidationError("invalid_jpeg")
    offset = 2
    dimensions: tuple[int, int] | None = None
    found_scan = False
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise AttachmentValidationError("invalid_jpeg")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0x01, *range(0xD0, 0xD8)}:
            continue
        if marker == 0xD9:
            break
        if offset + 2 > len(data):
            raise AttachmentValidationError("invalid_jpeg")
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            raise AttachmentValidationError("invalid_jpeg")
        payload_start = offset + 2
        payload_end = offset + segment_length
        if marker in start_of_frame:
            if segment_length < 8:
                raise AttachmentValidationError("invalid_jpeg")
            height, width = struct.unpack_from(">HH", data, payload_start + 1)
            _check_pixels(width, height)
            dimensions = (width, height)
        if marker == 0xDA:
            found_scan = True
            break
        offset = payload_end
    if dimensions is None or not found_scan:
        raise AttachmentValidationError("invalid_jpeg")
