# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Format-aware corpus fetch plumbing (Phase 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tads.corpus.base import CorpusDocumentRef
from tads.fetch import (
    FetchNotSupportedError,
    FetchResolveError,
    assert_direct_document_uri,
    fetch_to_path,
    looks_like_direct_document_uri,
)
from tads.ingest import IngestOptions, IngestResult, ingest_to_text
from tads.ingest.html_convert import html_to_text


def test_html_to_text_strips_tags_and_script():
    html = """
    <html><head><style>body{color:red}</style>
    <script>evil()</script></head>
    <body><h1>NTP Era</h1><p>Wraps in <b>2036</b>.</p></body></html>
    """
    text = html_to_text(html)
    assert "NTP Era" in text
    assert "2036" in text
    assert "evil" not in text
    assert "color:red" not in text


def test_ingest_local_html_file(tmp_path: Path):
    path = tmp_path / "sample.html"
    path.write_text(
        "<html><body><h1>Title</h1><p>Monotonic clock notes.</p></body></html>",
        encoding="utf-8",
    )
    result = ingest_to_text(str(path))
    assert result.converter == "html-text"
    assert "Monotonic clock" in result.text
    assert "Title" in result.text


def test_ingest_local_pdf_still_works(tmp_path: Path):
    import fitz

    pdf_path = tmp_path / "x.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF epoch text")
    pdf_path.write_bytes(doc.tobytes())
    doc.close()
    result = ingest_to_text(str(pdf_path))
    assert result.converter == "pymupdf"
    assert "epoch" in result.text.lower()


def test_looks_like_direct_document_uri():
    assert looks_like_direct_document_uri(
        "https://www.rfc-editor.org/rfc/rfc5905.txt"
    )
    assert looks_like_direct_document_uri("https://www.w3.org/TR/hr-time-3/")
    assert looks_like_direct_document_uri("https://262.ecma-international.org/17.0/")
    assert looks_like_direct_document_uri(
        "https://example.org/files/spec.pdf"
    )
    assert not looks_like_direct_document_uri("https://www.etsi.org/standards-search")
    assert not looks_like_direct_document_uri("https://example.org/")
    with pytest.raises(FetchResolveError):
        assert_direct_document_uri("https://www.etsi.org/standards-search")


def test_fetch_to_path_converts_via_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = tmp_path / "out.txt"
    seen: dict = {}

    class FakeAdapter:
        corpus_id = "w3c"
        supports_remote_fetch = True

        def resolve(self, doc_id: str) -> CorpusDocumentRef:
            return CorpusDocumentRef(
                corpus="w3c",
                doc_id=doc_id,
                source_uri="https://www.w3.org/TR/hr-time-3/",
                media_type="text/html",
            )

    def fake_ingest(source: str, *, options: IngestOptions | None = None):
        seen["source"] = source
        seen["allow_html"] = bool(options and options.allow_html)
        assert options is not None
        assert options.save_text_path == str(out)
        out.write_text("High Resolution Time\n\nMonotonic clocks.\n", encoding="utf-8")
        return IngestResult(
            text=out.read_text(encoding="utf-8"),
            source=source,
            media_type="html",
            converter="html-text",
            saved_text_path=str(out),
            notes=["Converted HTML to plain text."],
        )

    monkeypatch.setattr("tads.fetch.get_adapter", lambda _c: FakeAdapter())
    monkeypatch.setattr("tads.fetch.ingest_to_text", fake_ingest)

    written = fetch_to_path("hr-time-3", out, corpus="w3c")
    assert written == str(out)
    assert seen["source"] == "https://www.w3.org/TR/hr-time-3/"
    assert seen["allow_html"] is True
    assert "Monotonic" in out.read_text(encoding="utf-8")


def test_fetch_to_path_rejects_portal_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class PortalAdapter:
        corpus_id = "etsi"
        supports_remote_fetch = True

        def resolve(self, doc_id: str) -> CorpusDocumentRef:
            return CorpusDocumentRef(
                corpus="etsi",
                doc_id=doc_id,
                source_uri="https://www.etsi.org/standards-search",
                media_type="text/plain",
            )

    monkeypatch.setattr("tads.fetch.get_adapter", lambda _c: PortalAdapter())
    with pytest.raises(FetchResolveError, match="portal"):
        fetch_to_path("EN 300 468", tmp_path / "x.txt", corpus="etsi")


def test_fetch_still_not_supported_for_real_etsi():
    with pytest.raises(FetchNotSupportedError):
        fetch_to_path("ETSI TS 103 246-1", Path("/tmp/nope.txt"), corpus="etsi")
