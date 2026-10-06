"""Bounded container validation, not a pixel decoder or an efficacy authority.

Every walk advances within the already capped input. Check complete chunks,
dimensions and image payload/end markers, without allocating from dimensions,
decompressing pixels, or invoking an external codec on attacker-controlled data.
This is structural validation, not a guarantee that every pixel decodes.
"""
from __future__ import annotations

import struct
import zlib


def _png(data: bytes) -> tuple[int, int] | None:
    offset = 8
    dimensions = None
    payload = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            return None
        kind = data[offset + 4:offset + 8]
        body = memoryview(data)[offset + 8:end - 4]
        crc = zlib.crc32(body, zlib.crc32(kind)) & 0xFFFFFFFF
        if crc != int.from_bytes(data[end - 4:end], "big"):
            return None
        if dimensions is None:
            if kind != b"IHDR" or length != 13:
                return None
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            legal_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if not width or not height or depth not in legal_depths.get(colour, ()) or compression or filtering or interlace > 1:
                return None
            dimensions = (width, height)
        elif kind == b"IHDR":
            return None
        elif kind == b"IDAT":
            payload = payload or length > 0
        elif kind == b"IEND":
            return dimensions if length == 0 and payload and end == len(data) else None
        offset = end
    return None


def _jpeg(data: bytes) -> tuple[int, int] | None:
    offset = 2
    dimensions = None
    scan = False
    entropy = False
    while offset < len(data):
        if data[offset] != 0xFF:
            if not scan:
                return None
            entropy = True
            offset += 1
            continue
        offset += 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker == 0 and scan:
            entropy = True
            continue
        if 0xD0 <= marker <= 0xD7 and scan:
            continue
        if marker == 0xD9:
            return dimensions if scan and entropy and offset == len(data) else None
        if marker in (0, 0xD8) or offset + 2 > len(data):
            return None
        length = int.from_bytes(data[offset:offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(data):
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if length < 8:
                return None
            height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
            components = data[offset + 7]
            if not width or not height or not components or length != 8 + 3 * components:
                return None
            dimensions = (width, height)
        if marker == 0xDA:
            if dimensions is None or length < 6 or length != 6 + 2 * data[offset + 2]:
                return None
            scan = True
        offset = end
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        return None
    offset = 12
    dimensions = None
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        end = offset + 8 + length
        if end + (length & 1) > len(data):
            return None
        body = memoryview(data)[offset + 8:end]
        if kind == b"VP8 ":
            if length <= 10 or bytes(body[3:6]) != b"\x9d\x01\x2a" or body[0] & 1:
                return None
            width, height = struct.unpack("<HH", body[6:10])
            dimensions = (width & 0x3FFF, height & 0x3FFF)
        elif kind == b"VP8L":
            if length <= 5 or body[0] != 0x2F:
                return None
            bits = int.from_bytes(body[1:5], "little")
            if bits >> 29:
                return None
            dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        elif kind == b"VP8X" and length != 10 or kind in (b"ANIM", b"ANMF"):
            return None
        offset = end + (length & 1)
    return dimensions if offset == len(data) and dimensions and all(dimensions) else None


def image_dimensions(data: bytes, mime: str) -> tuple[int, int] | None:
    parser = {"image/png": _png, "image/jpeg": _jpeg, "image/webp": _webp}.get(mime)
    if parser is None:
        return None
    try:
        return parser(data)
    except (ValueError, IndexError, struct.error):
        return None
