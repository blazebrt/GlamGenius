"""Upload validation.

The declared ``Content-Type`` on a multipart part is caller-supplied and worth
nothing on its own — anyone can label a zip as ``image/png``. So the real type
is sniffed from the file's leading bytes, and the declared type is only accepted
when it agrees.

Dimensions are read from the same header bytes for JPEG, PNG and WebP rather
than by decoding the image. No decoder runs on untrusted input, which removes a
whole class of image-parser exploits, and it needs no new dependency.
"""
from __future__ import annotations

import struct
from typing import Optional

from app.config import MEDIA_ALLOWED_MIME, MEDIA_MAX_BYTES
from app.shared.errors.exceptions import MediaTooLargeError, UnsupportedMediaTypeError


def sniff_mime(data: bytes) -> Optional[str]:
    """The real type, from magic bytes. None when it is not an image we accept."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    # IHDR is always the first chunk: width and height are two big-endian
    # 32-bit integers at offset 16.
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _jpeg_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    # Walk the segment markers to the start-of-frame, which carries the size.
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # SOF0-SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        if index + 4 > length:
            return None
        segment_length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if segment_length <= 0:
            return None
        index += 2 + segment_length
    return None


def _webp_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    if len(data) < 30:
        return None
    fourcc = data[12:16]
    if fourcc == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if fourcc == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if fourcc == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None


def read_dimensions(data: bytes, mime: str) -> tuple[Optional[int], Optional[int]]:
    """Best-effort width and height. Never raises — dimensions are optional."""
    try:
        if mime == "image/png":
            result = _png_dimensions(data)
        elif mime == "image/jpeg":
            result = _jpeg_dimensions(data)
        elif mime == "image/webp":
            result = _webp_dimensions(data)
        else:
            result = None
    except (struct.error, IndexError, ValueError):
        return None, None
    return result if result else (None, None)


def validate_upload(data: bytes, declared_type: Optional[str]) -> tuple[str, int]:
    """Check size and type. Returns the sniffed MIME type and byte size.

    Raises ``MediaTooLargeError`` or ``UnsupportedMediaTypeError``, both of which
    the global handler turns into a structured 413 / 415.
    """
    size = len(data)
    if size == 0:
        raise UnsupportedMediaTypeError(
            "That file is empty. Please choose a photo and try again.",
            allowed=MEDIA_ALLOWED_MIME,
        )
    if size > MEDIA_MAX_BYTES:
        limit_mb = MEDIA_MAX_BYTES / (1024 * 1024)
        raise MediaTooLargeError(
            f"That photo is larger than the {limit_mb:.0f} MB limit. "
            "Please choose a smaller one.",
            max_bytes=MEDIA_MAX_BYTES,
        )

    sniffed = sniff_mime(data)
    if sniffed is None or sniffed not in MEDIA_ALLOWED_MIME:
        raise UnsupportedMediaTypeError(
            "That file is not a photo we can read. Please upload a JPEG, PNG or WebP image.",
            allowed=MEDIA_ALLOWED_MIME,
        )

    normalised_declared = (declared_type or "").split(";")[0].strip().lower()
    if normalised_declared and normalised_declared != sniffed:
        # The file contradicts its own label. Refuse rather than guess.
        raise UnsupportedMediaTypeError(
            "That file's contents do not match its type. "
            "Please upload a JPEG, PNG or WebP image.",
            allowed=MEDIA_ALLOWED_MIME,
        )

    return sniffed, size
