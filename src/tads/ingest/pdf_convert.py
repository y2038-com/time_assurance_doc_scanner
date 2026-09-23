# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""PDF → plain text conversion."""

from __future__ import annotations

from tads.ingest.fetch import IngestError
from tads.ingest.limits import assert_converted_text_limit, note_converted_chars
from tads.ingest.types import DEFAULT_MAX_CONVERTED_CHARS


def pdf_to_text(
    data: bytes,
    *,
    max_converted_chars: int = DEFAULT_MAX_CONVERTED_CHARS,
) -> str:
    try:
        import fitz  # PyMuPDF (optional [pdf] extra)
    except ImportError as exc:
        raise IngestError(
            "PDF support requires the optional `pdf` extra.\n"
            "Install with:\n"
            '    pip install "time-assurance-doc-scanner[pdf]"\n'
            "For an editable checkout:\n"
            '    pip install -e ".[pdf]"'
        ) from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"Failed to read PDF: {exc}") from exc
    try:
        parts: list[str] = []
        used = 0
        for page in doc:
            piece = page.get_text("text")
            extra = "\n" if parts else ""
            used = note_converted_chars(extra + piece, used=used, max_chars=max_converted_chars)
            parts.append(piece)
        text = "\n".join(parts)
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        text = text.strip() + ("\n" if text.strip() else "")
        assert_converted_text_limit(text, max_converted_chars)
        return text
    finally:
        doc.close()