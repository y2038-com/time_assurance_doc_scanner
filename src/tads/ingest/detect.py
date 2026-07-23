"""Media-type detection for ingest."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlparse


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def extension_of(name: str) -> str:
    # Handle nested suffixes like .tar.gz
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        return ".tar.gz"
    if lower.endswith(".tgz"):
        return ".tgz"
    return Path(name).suffix.lower()


def detect_media_type(
    *,
    name: str,
    content_type: str | None = None,
    data: bytes | None = None,
) -> str:
    """
    Return a coarse media type used by the converter registry.

    Values: text, docx, pdf, zip, tar, html, unknown
    """
    ext = extension_of(name)
    if ext in {".txt", ".text", ".md"}:
        return "text"
    if ext == ".docx":
        return "docx"
    if ext == ".pdf":
        return "pdf"
    if ext == ".zip":
        return "zip"
    if ext in {".tgz", ".tar.gz", ".tar"}:
        return "tar"

    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in {"text/plain", "text/markdown"}:
        return "text"
    if ctype in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return "docx"
    if ctype == "application/pdf":
        return "pdf"
    if ctype in {"application/zip", "application/x-zip-compressed"}:
        return "zip"
    if ctype in {"application/gzip", "application/x-gtar", "application/x-tar"}:
        return "tar"
    if ctype in {"text/html", "application/xhtml+xml"}:
        return "html"

    if data:
        if data.startswith(b"%PDF"):
            return "pdf"
        if data[:2] == b"PK":
            # zip or docx (docx is a zip)
            if b"word/" in data[:8192] or name.lower().endswith(".docx"):
                return "docx"
            return "zip"
        if data[:2] == b"\x1f\x8b":
            return "tar"
        # Heuristic: mostly text
        sample = data[:2048]
        if sample and sum(32 <= b < 127 or b in (9, 10, 13) for b in sample) / len(sample) > 0.85:
            return "text"

    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return detect_media_type(name=name, content_type=guessed, data=None)
    return "unknown"
