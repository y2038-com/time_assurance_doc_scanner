# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""PDF → plain text conversion."""

from __future__ import annotations

from tads.ingest.fetch import IngestError


def pdf_to_text(data: bytes) -> str:
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
        for page in doc:
            parts.append(page.get_text("text"))
        text = "\n".join(parts)
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return text.strip() + ("\n" if text.strip() else "")
    finally:
        doc.close()
