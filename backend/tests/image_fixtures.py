"""Complete small image files. No imaging dependency is needed by tests.

Generated once from solid pixels, unlike the old signature-only fixtures.
JPEG is 64x32, WebP 8x4, PNG 1x1. Distinct JPEGs exercise immutable evidence.
"""
import uuid
import zlib
from base64 import b64decode

JPEG_WHITE = b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/"
    "wAARCAAgAEADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/"
    "8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/"
    "9oADAMBAAIRAxEAPwD3+iiigAooooAKKKKACiiigAooooAKKKKACiiigAooooA//9k="
)
# A complete JPEG with distinct (harmless) comment metadata for replay tests.
JPEG_BLACK = JPEG_WHITE[:2] + b"\xff\xfe\x00\x09phone-B" + JPEG_WHITE[2:]
WEBP = b64decode("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoIAAQAAUAmJaQAA3AA/vz0AAA=")
PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")


def distinct_png() -> bytes:
    """A different complete PNG, not the old PNG-plus-trailing-garbage fixture."""
    chunk = b"tEXtfixture-id\x00" + uuid.uuid4().hex.encode("ascii")
    framed = (len(chunk) - 4).to_bytes(4, "big") + chunk + (zlib.crc32(chunk) & 0xFFFFFFFF).to_bytes(4, "big")
    return PNG[:-12] + framed + PNG[-12:]
