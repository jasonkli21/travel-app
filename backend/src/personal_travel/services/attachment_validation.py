"""Strict, bounded media checks for private trip attachments."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

from personal_travel.services.source_parser import SourceParseError, validate_image, validate_pdf

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TEXT_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DECOMPRESSED_PNG_BYTES = 128 * 1024 * 1024
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
        image_media_type = media_type
    else:
        image_media_type = "image/jpeg"
    try:
        validate_image(path, image_media_type)
    except SourceParseError as exc:
        code = str(exc.args[0]) if exc.args else "invalid_image"
        if code in {
            "animated_image_unsupported",
            "image_decompression_exceeded",
            "image_dimensions_exceeded",
            "unsupported_jpeg_encoding",
            "unsupported_jpeg_restart",
        }:
            raise AttachmentValidationError(code) from exc
        raise AttachmentValidationError("invalid_image") from exc


def _check_pixels(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise AttachmentValidationError("image_dimensions_exceeded")


def _validate_png(data: bytes) -> None:
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 45 or not data.startswith(signature):
        raise AttachmentValidationError("invalid_png")
    offset = len(signature)
    seen_header = seen_palette = seen_idat = seen_end = False
    idat_closed = False
    decoder: Any | None = None
    row_specs: list[tuple[int, int]] = []
    pass_index = row_index = 0
    row_remaining = 0
    row_needs_filter = False
    decoded_bytes = 0
    expected_bytes = 0
    compressed_bytes = 0
    bit_depth = color_type = 0

    def consume_scanlines(output: bytes) -> None:
        nonlocal pass_index, row_index, row_remaining, row_needs_filter, decoded_bytes
        position = 0
        decoded_bytes += len(output)
        if decoded_bytes > expected_bytes:
            raise AttachmentValidationError("invalid_png")
        while position < len(output):
            while pass_index < len(row_specs) and row_index >= row_specs[pass_index][1]:
                pass_index += 1
                row_index = 0
                row_remaining = 0
                row_needs_filter = False
            if pass_index >= len(row_specs):
                raise AttachmentValidationError("invalid_png")
            if row_remaining == 0:
                row_remaining = row_specs[pass_index][0] + 1
                row_needs_filter = True
            if row_needs_filter:
                if output[position] > 4:
                    raise AttachmentValidationError("invalid_png")
                position += 1
                row_remaining -= 1
                row_needs_filter = False
                if row_remaining == 0:
                    row_index += 1
                continue
            consumed = min(row_remaining, len(output) - position)
            position += consumed
            row_remaining -= consumed
            if row_remaining == 0:
                row_index += 1
        while pass_index < len(row_specs) and row_index >= row_specs[pass_index][1]:
            pass_index += 1
            row_index = 0
            row_remaining = 0
            row_needs_filter = False

    def expected_passes(
        width: int, height: int, channels: int, depth: int, interlace: int
    ) -> list[tuple[int, int]]:
        if interlace == 0:
            passes = [(width, height)]
        else:
            starts_x = (0, 4, 0, 2, 0, 1, 0)
            starts_y = (0, 0, 4, 0, 2, 0, 1)
            steps_x = (8, 8, 4, 4, 2, 2, 1)
            steps_y = (8, 8, 8, 4, 4, 2, 2)
            passes = []
            for x0, y0, dx, dy in zip(starts_x, starts_y, steps_x, steps_y, strict=True):
                pass_width = max(0, (width - x0 + dx - 1) // dx)
                pass_height = max(0, (height - y0 + dy - 1) // dy)
                if pass_width and pass_height:
                    passes.append((pass_width, pass_height))
        result = []
        for pass_width, pass_height in passes:
            row_bytes = (pass_width * channels * depth + 7) // 8
            result.append((row_bytes, pass_height))
        return result

    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data) or not all(65 <= char <= 90 or 97 <= char <= 122 for char in kind):
            raise AttachmentValidationError("invalid_png")
        if 97 <= kind[2] <= 122:  # The reserved third chunk-name bit must be zero.
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
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                color_type not in valid_depths
                or bit_depth not in valid_depths[color_type]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise AttachmentValidationError("invalid_png")
            _check_pixels(width, height)
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            row_specs = expected_passes(width, height, channels, bit_depth, interlace)
            expected_bytes = sum((row_bytes + 1) * rows for row_bytes, rows in row_specs)
            if expected_bytes > MAX_DECOMPRESSED_PNG_BYTES:
                raise AttachmentValidationError("image_decompression_exceeded")
            row_remaining = row_specs[0][0] + 1
            row_needs_filter = True
            seen_header = True
        else:
            if kind == b"IHDR":
                raise AttachmentValidationError("invalid_png")
            if seen_idat and kind != b"IDAT":
                idat_closed = True
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise AttachmentValidationError("animated_image_unsupported")
        if kind == b"PLTE":
            if seen_palette or seen_idat or color_type in {0, 4} or length == 0 or length % 3:
                raise AttachmentValidationError("invalid_png")
            entries = length // 3
            if entries > 256 or (color_type == 3 and entries > (1 << bit_depth)):
                raise AttachmentValidationError("invalid_png")
            seen_palette = True
        elif kind == b"IDAT":
            if idat_closed or (color_type == 3 and not seen_palette):
                raise AttachmentValidationError("invalid_png")
            if decoder is None:
                decoder = zlib.decompressobj()
            seen_idat = True
            compressed_bytes += length
            pending = payload
            while pending:
                try:
                    output = decoder.decompress(pending, 64 * 1024)
                except zlib.error as exc:
                    raise AttachmentValidationError("invalid_png") from exc
                consume_scanlines(output)
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise AttachmentValidationError("invalid_png")
        elif kind == b"IEND":
            if (
                length != 0
                or end != len(data)
                or decoder is None
                or compressed_bytes == 0
                or not decoder.eof
                or decoder.unused_data
                or decoded_bytes != expected_bytes
                or pass_index != len(row_specs)
                or row_remaining != 0
            ):
                raise AttachmentValidationError("invalid_png")
            seen_end = True
            break
        elif kind not in {b"IHDR", b"PLTE", b"IDAT"} and 65 <= kind[0] <= 90:
            # Unknown critical chunks can change how the image is interpreted.
            raise AttachmentValidationError("invalid_png")
        offset = end
    if not seen_header or not seen_idat or not seen_end:
        raise AttachmentValidationError("invalid_png")


def _validate_jpeg(data: bytes) -> None:
    if len(data) < 16 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise AttachmentValidationError("invalid_jpeg")
    offset = 2
    width = height = 0
    components: dict[int, tuple[int, int, int]] = {}
    quant_tables: set[int] = set()
    huffman_tables: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
    scanned_components: set[int] = set()
    scan_count = 0
    frame_marker: int | None = None
    frame_markers = {*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0)}
    while offset + 2 <= len(data):
        if data[offset] != 0xFF:
            raise AttachmentValidationError("invalid_jpeg")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise AttachmentValidationError("invalid_jpeg")
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            if offset != len(data) or not scan_count or scanned_components != set(components):
                raise AttachmentValidationError("invalid_jpeg")
            return
        if marker in {0xD8, *range(0xD0, 0xD8)}:
            raise AttachmentValidationError("invalid_jpeg")
        if marker == 0x01:  # TEM is standalone and carries no payload.
            continue
        if offset + 2 > len(data):
            raise AttachmentValidationError("invalid_jpeg")
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            raise AttachmentValidationError("invalid_jpeg")
        payload_start = offset + 2
        payload_end = offset + segment_length
        payload = data[payload_start:payload_end]
        if marker in frame_markers:
            if marker != 0xC0:
                raise AttachmentValidationError("unsupported_jpeg_encoding")
            if frame_marker is not None or len(payload) < 6:
                raise AttachmentValidationError("invalid_jpeg")
            precision = payload[0]
            height, width = struct.unpack_from(">HH", payload, 1)
            component_count = payload[5]
            if precision != 8 or component_count not in {1, 3, 4}:
                raise AttachmentValidationError("invalid_jpeg")
            if len(payload) != 6 + 3 * component_count:
                raise AttachmentValidationError("invalid_jpeg")
            _check_pixels(width, height)
            for index in range(component_count):
                component_id, sampling, quant_table = payload[6 + index * 3 : 9 + index * 3]
                horizontal, vertical = sampling >> 4, sampling & 0x0F
                if (
                    component_id in components
                    or not 1 <= horizontal <= 4
                    or not 1 <= vertical <= 4
                    or quant_table > 3
                ):
                    raise AttachmentValidationError("invalid_jpeg")
                components[component_id] = (horizontal, vertical, quant_table)
            frame_marker = marker
        elif marker == 0xDB:  # Define Quantization Table.
            cursor = 0
            while cursor < len(payload):
                selector = payload[cursor]
                cursor += 1
                precision, table_id = selector >> 4, selector & 0x0F
                if precision != 0 or table_id > 3:
                    raise AttachmentValidationError("invalid_jpeg")
                table_bytes = 64
                if cursor + table_bytes > len(payload):
                    raise AttachmentValidationError("invalid_jpeg")
                values = payload[cursor : cursor + table_bytes]
                if 0 in values:
                    raise AttachmentValidationError("invalid_jpeg")
                quant_tables.add(table_id)
                cursor += table_bytes
        elif marker == 0xC4:  # Define Huffman Table.
            cursor = 0
            while cursor < len(payload):
                selector = payload[cursor]
                cursor += 1
                table_class, table_id = selector >> 4, selector & 0x0F
                if table_class not in {0, 1} or table_id > 3 or cursor + 16 > len(payload):
                    raise AttachmentValidationError("invalid_jpeg")
                counts = payload[cursor : cursor + 16]
                cursor += 16
                symbol_count = sum(counts)
                slots = 1
                for count in counts:
                    slots = slots * 2 - count
                    if slots < 0:
                        raise AttachmentValidationError("invalid_jpeg")
                if symbol_count == 0 or cursor + symbol_count > len(payload):
                    raise AttachmentValidationError("invalid_jpeg")
                symbols = payload[cursor : cursor + symbol_count]
                cursor += symbol_count
                table: dict[tuple[int, int], int] = {}
                code = 0
                symbol_index = 0
                for bit_length, count in enumerate(counts, start=1):
                    for _ in range(count):
                        if code >= 1 << bit_length:
                            raise AttachmentValidationError("invalid_jpeg")
                        table[(bit_length, code)] = symbols[symbol_index]
                        symbol_index += 1
                        code += 1
                    code <<= 1
                huffman_tables[(table_class, table_id)] = table
        elif marker == 0xE2 and payload.startswith(b"MPF\x00"):
            raise AttachmentValidationError("multi_picture_jpeg_unsupported")
        if marker == 0xDA:
            if frame_marker is None or len(payload) < 4:
                raise AttachmentValidationError("invalid_jpeg")
            scan_components = payload[0]
            if scan_components < 1 or scan_components > len(components):
                raise AttachmentValidationError("invalid_jpeg")
            if len(payload) != 1 + scan_components * 2 + 3:
                raise AttachmentValidationError("invalid_jpeg")
            selected: set[int] = set()
            selected_order: list[int] = []
            selected_tables: dict[int, tuple[int, int]] = {}
            for index in range(scan_components):
                component_id = payload[1 + index * 2]
                selector = payload[2 + index * 2]
                dc_table, ac_table = selector >> 4, selector & 0x0F
                if (
                    component_id not in components
                    or component_id in selected
                    or max(dc_table, ac_table) > 3
                    or component_id in scanned_components
                ):
                    raise AttachmentValidationError("invalid_jpeg")
                selected.add(component_id)
                selected_order.append(component_id)
                selected_tables[component_id] = (dc_table, ac_table)
            spectral_start, spectral_end, approximation = payload[-3:]
            if spectral_start != 0 or spectral_end != 63 or approximation != 0:
                raise AttachmentValidationError("invalid_jpeg")
            for component_id in selected_order:
                _horizontal, _vertical, quant_table = components[component_id]
                dc_table, ac_table = selected_tables[component_id]
                if quant_table not in quant_tables:
                    raise AttachmentValidationError("invalid_jpeg")
                if (0, dc_table) not in huffman_tables or (1, ac_table) not in huffman_tables:
                    raise AttachmentValidationError("invalid_jpeg")

            entropy, offset = _jpeg_entropy_data(data, payload_end)
            _validate_jpeg_scan(
                entropy,
                width=width,
                height=height,
                components=components,
                selected_order=selected_order,
                selected_tables=selected_tables,
                huffman_tables=huffman_tables,
            )
            scan_count += 1
            scanned_components.update(selected)
            continue
        offset = payload_end
    raise AttachmentValidationError("invalid_jpeg")


def _jpeg_entropy_data(data: bytes, offset: int) -> tuple[bytes, int]:
    entropy = bytearray()
    while offset < len(data):
        if data[offset] != 0xFF:
            entropy.append(data[offset])
            offset += 1
            continue
        if offset + 1 >= len(data):
            raise AttachmentValidationError("invalid_jpeg")
        if data[offset + 1] == 0x00:
            entropy.append(0xFF)
            offset += 2
            continue
        marker_offset = offset
        offset += 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data) or 0xD0 <= data[offset] <= 0xD7:
            raise AttachmentValidationError("unsupported_jpeg_restart")
        return bytes(entropy), marker_offset
    raise AttachmentValidationError("invalid_jpeg")


def _validate_jpeg_scan(
    entropy: bytes,
    *,
    width: int,
    height: int,
    components: dict[int, tuple[int, int, int]],
    selected_order: list[int],
    selected_tables: dict[int, tuple[int, int]],
    huffman_tables: dict[tuple[int, int], dict[tuple[int, int], int]],
) -> None:
    if not entropy:
        raise AttachmentValidationError("invalid_jpeg")
    max_horizontal = max(horizontal for horizontal, _vertical, _table in components.values())
    max_vertical = max(vertical for _horizontal, vertical, _table in components.values())
    if len(selected_order) > 1:
        columns = (width + 8 * max_horizontal - 1) // (8 * max_horizontal)
        rows = (height + 8 * max_vertical - 1) // (8 * max_vertical)
        block_groups = [
            (component_id, components[component_id][0] * components[component_id][1])
            for component_id in selected_order
        ]
    else:
        component_id = selected_order[0]
        horizontal, vertical, _table = components[component_id]
        columns = (width * horizontal + 8 * max_horizontal - 1) // (8 * max_horizontal)
        rows = (height * vertical + 8 * max_vertical - 1) // (8 * max_vertical)
        block_groups = [(component_id, 1)]

    bit_offset = 0

    def read_bits(count: int) -> int:
        nonlocal bit_offset
        if bit_offset + count > len(entropy) * 8:
            raise AttachmentValidationError("invalid_jpeg")
        value = 0
        for _ in range(count):
            byte = entropy[bit_offset // 8]
            shift = 7 - bit_offset % 8
            value = (value << 1) | ((byte >> shift) & 1)
            bit_offset += 1
        return value

    def symbol(table: dict[tuple[int, int], int]) -> int:
        code = 0
        for bit_length in range(1, 17):
            code = (code << 1) | read_bits(1)
            value = table.get((bit_length, code))
            if value is not None:
                return value
        raise AttachmentValidationError("invalid_jpeg")

    def block(component_id: int) -> None:
        dc_id, ac_id = selected_tables[component_id]
        dc_size = symbol(huffman_tables[(0, dc_id)])
        if dc_size > 11:
            raise AttachmentValidationError("invalid_jpeg")
        read_bits(dc_size)
        coefficient = 1
        ac_table = huffman_tables[(1, ac_id)]
        while coefficient <= 63:
            run_size = symbol(ac_table)
            run, size = run_size >> 4, run_size & 0x0F
            if size == 0:
                if run == 0:
                    break
                if run != 15:
                    raise AttachmentValidationError("invalid_jpeg")
                coefficient += 16
                if coefficient > 64:
                    raise AttachmentValidationError("invalid_jpeg")
                continue
            if size > 10:
                raise AttachmentValidationError("invalid_jpeg")
            coefficient += run
            if coefficient > 63:
                raise AttachmentValidationError("invalid_jpeg")
            read_bits(size)
            coefficient += 1

    for _mcu in range(columns * rows):
        for component_id, blocks_per_mcu in block_groups:
            for _block_index in range(blocks_per_mcu):
                block(component_id)

    padding_bits = len(entropy) * 8 - bit_offset
    if padding_bits > 7 or (padding_bits and read_bits(padding_bits) != (1 << padding_bits) - 1):
        raise AttachmentValidationError("invalid_jpeg")
