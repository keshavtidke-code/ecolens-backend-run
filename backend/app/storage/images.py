"""Upload reading and image-type sniffing (trust file bytes, not the client's Content-Type)."""
from __future__ import annotations

from typing import BinaryIO

from app.core.exceptions import BadRequestError, PayloadTooLargeError

EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}

# Smallest plausible real image for each format (bytes).
# The corrupt stub files on disk are exactly 104 bytes (JPEG header + all zeros).
# Any structurally valid image – even a 1×1 pixel synthetic JPEG – is at least ~200 B.
# Real mobile camera photos are always several kilobytes.
_MIN_IMAGE_BYTES = 200

# JPEG end-of-image marker – every well-formed JPEG must end with these two bytes.
_JPEG_EOI = b"\xff\xd9"


def sniff_image_type(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return "image/heic"
    return None


def validate_image_data(data: bytes, mime: str) -> None:
    """Raise BadRequestError if *data* looks truncated or corrupt.

    Checks performed:
    - Minimum byte count (catches empty / all-zero stub uploads).
    - JPEG end-of-image marker presence (catches mid-stream truncation).
    """
    if len(data) < _MIN_IMAGE_BYTES:
        raise BadRequestError(
            "The uploaded photo is too small to be a valid image. "
            "Please retake the photo and try again.",
            code="photo_too_small",
        )
    if mime == "image/jpeg" and not data.rstrip(b"\x00").endswith(_JPEG_EOI):
        raise BadRequestError(
            "The uploaded JPEG photo appears to be incomplete or damaged. "
            "Please retake the photo and try again.",
            code="photo_corrupt",
        )


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read an upload fully, aborting as soon as it exceeds max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while chunk := stream.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"Photo exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                code="photo_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)
