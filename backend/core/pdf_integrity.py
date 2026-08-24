from __future__ import annotations

from pathlib import Path


PDF_MIN_BYTES = 128
PDF_MAX_DELIVERY_BYTES = 12 * 1024 * 1024


def is_valid_pdf_bytes(data: bytes) -> bool:
    """Cheap integrity guard for files crossing browser/WhatsApp boundaries."""
    if not data or len(data) < PDF_MIN_BYTES:
        return False
    return data.lstrip()[:5] == b"%PDF-" and b"%%EOF" in data[-2048:]


def is_valid_pdf_file(path: str | Path) -> bool:
    try:
        candidate = Path(path)
        if not candidate.is_file() or candidate.stat().st_size < PDF_MIN_BYTES:
            return False
        with candidate.open("rb") as stream:
            header = stream.read(16)
            stream.seek(max(0, candidate.stat().st_size - 2048))
            trailer = stream.read()
        return header.lstrip()[:5] == b"%PDF-" and b"%%EOF" in trailer
    except (OSError, ValueError):
        return False


def is_deliverable_pdf_file(
    path: str | Path,
    max_bytes: int = PDF_MAX_DELIVERY_BYTES,
) -> bool:
    try:
        candidate = Path(path)
        return candidate.stat().st_size <= max_bytes and is_valid_pdf_file(candidate)
    except (OSError, ValueError):
        return False
