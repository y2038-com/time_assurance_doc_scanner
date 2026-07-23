"""PDF → plain text conversion."""

from __future__ import annotations

from tads.ingest.fetch import IngestError


def pdf_to_text(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise IngestError(
            "pymupdf is required for PDF conversion. "
            "Install with: pip install pymupdf"
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
