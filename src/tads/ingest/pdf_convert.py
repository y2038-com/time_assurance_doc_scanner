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
    missing = False
    try:
        import fitz  # PyMuPDF (optional [pdf] extra)
    except ImportError:
        missing = True
    if missing:
        raise IngestError(
            "PDF support requires the optional `pdf` extra.\n"
            "Install with:\n"
            '    pip install "time-assurance-doc-scanner[pdf]"\n'
            "For an editable checkout:\n"
            '    pip install -e ".[pdf]"'
        )
    invalid = False
    limit_error: IngestError | None = None
    doc = None
    text: str | None = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        parts: list[str] = []
        used = 0
        for page in doc:
            piece = page.get_text("text")
            extra = "\n" if parts else ""
            used = note_converted_chars(
                extra + piece, used=used, max_chars=max_converted_chars
            )
            parts.append(piece)
        joined = "\n".join(parts)
        while "\n\n\n" in joined:
            joined = joined.replace("\n\n\n", "\n\n")
        joined = joined.strip() + ("\n" if joined.strip() else "")
        assert_converted_text_limit(joined, max_converted_chars)
        text = joined
    except IngestError as exc:
        limit_error = exc
    except Exception:  # noqa: BLE001
        invalid = True
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass
    if limit_error is not None:
        raise limit_error
    if invalid or text is None:
        raise IngestError("Invalid PDF")
    return text
