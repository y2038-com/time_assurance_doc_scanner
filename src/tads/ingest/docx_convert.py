"""DOCX → plain text conversion."""

from __future__ import annotations

import io

from tads.ingest.fetch import IngestError


def docx_to_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise IngestError(
            "python-docx is required for .docx conversion. "
            "Install with: pip install python-docx"
        ) from exc
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - surface corrupt docs cleanly
        raise IngestError(f"Failed to read DOCX: {exc}") from exc

    parts: list[str] = []
    for paragraph in document.paragraphs:
        parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [
                cell.text.strip().replace("\n", " ") for cell in row.cells
            ]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + ("\n" if text.strip() else "")
