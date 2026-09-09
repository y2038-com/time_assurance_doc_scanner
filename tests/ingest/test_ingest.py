# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Ingest layer tests: detect, archives, convert, pipeline."""

from __future__ import annotations

import builtins
import io
import zipfile
from pathlib import Path

import pytest

from tads.ingest import IngestError, IngestOptions, ingest_to_text
from tads.ingest.archives import extract_preferred_member
from tads.ingest.detect import detect_media_type, looks_like_url
from tads.ingest.docx_convert import docx_to_text
from tads.ingest.pdf_convert import pdf_to_text
from tads.ingest.pipeline import _resolve_save_text_path
from tads.ingest.urls import rfc_editor_text_fallback, rewrite_document_url


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf_bytes(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_detect_and_url():
    assert looks_like_url("https://example.com/a.pdf")
    assert not looks_like_url("./local.docx")
    assert detect_media_type(name="x.docx") == "docx"
    assert detect_media_type(name="x.pdf", data=b"%PDF-1.4") == "pdf"
    assert detect_media_type(name="x.zip") == "zip"


def test_docx_convert(tmp_path: Path):
    docx_bytes = _make_docx_bytes(["1 Scope", "Timers may wrap."])
    text = docx_to_text(docx_bytes)
    assert "Timers may wrap" in text

    out = tmp_path / "out.txt"
    src = tmp_path / "sample.docx"
    src.write_bytes(docx_bytes)
    result = ingest_to_text(str(src), options=IngestOptions(save_text_path=str(out)))
    assert result.converter == "python-docx"
    assert out.exists()
    assert "Scope" in out.read_text(encoding="utf-8")


def test_pdf_convert_when_pymupdf_available():
    pdf_bytes = _make_pdf_bytes("UTC leap seconds")
    assert "leap" in pdf_to_text(pdf_bytes).lower()


def test_pdf_requires_optional_extra(monkeypatch: pytest.MonkeyPatch):
    real_import = builtins.__import__

    def _block_fitz(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fitz" or name.startswith("fitz."):
            raise ImportError("No module named 'fitz'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _block_fitz)
    with pytest.raises(IngestError, match=r"optional `pdf` extra") as exc_info:
        pdf_to_text(b"%PDF-1.4 fake")
    msg = str(exc_info.value)
    assert "time-assurance-doc-scanner[pdf]" in msg
    assert ".[pdf]" in msg


def test_zip_prefers_docx(tmp_path: Path):
    docx_bytes = _make_docx_bytes(["4.1 Epoch", "Signed time."])
    # Placeholder PDF bytes are enough for preference ordering (no conversion).
    pdf_bytes = b"%PDF-1.4 placeholder"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes/readme.txt", "ignore")
        zf.writestr("spec/body.pdf", pdf_bytes)
        zf.writestr("spec/body.docx", docx_bytes)
    member = extract_preferred_member(buf.getvalue(), archive_kind="zip")
    assert member.name.endswith("body.docx")

    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(buf.getvalue())
    result = ingest_to_text(str(zip_path))
    assert result.member_name.endswith("body.docx")
    assert "Signed time" in result.text


def test_archive_member_override(tmp_path: Path):
    docx_bytes = _make_docx_bytes(["from docx"])
    pdf_bytes = _make_pdf_bytes("from pdf uniquely")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.docx", docx_bytes)
        zf.writestr("b.pdf", pdf_bytes)
    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(buf.getvalue())
    result = ingest_to_text(
        str(zip_path),
        options=IngestOptions(archive_member="b.pdf"),
    )
    assert result.media_type == "pdf"
    assert "uniquely" in result.text.lower()


def test_max_download_bytes_local(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("x" * 1000, encoding="utf-8")
    with pytest.raises(IngestError, match="exceeds max size"):
        ingest_to_text(str(path), options=IngestOptions(max_download_bytes=100))


def test_ietf_url_rewrite():
    rewritten, note = rewrite_document_url(
        "https://tools.ietf.org/pdf/rfc5905.pdf"
    )
    assert rewritten == "https://www.rfc-editor.org/rfc/rfc5905.txt"
    assert note is not None

    rewritten, note = rewrite_document_url(
        "https://datatracker.ietf.org/doc/html/rfc9110"
    )
    assert rewritten == "https://www.rfc-editor.org/rfc/rfc9110.txt"
    assert note is not None

    same, note = rewrite_document_url(
        "https://www.rfc-editor.org/rfc/rfc5905.txt"
    )
    assert same.endswith("rfc5905.txt")
    assert note is None

    assert (
        rfc_editor_text_fallback("https://www.rfc-editor.org/rfc/rfc5905.pdf")
        == "https://www.rfc-editor.org/rfc/rfc5905.txt"
    )


def test_save_text_directory(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    resolved = _resolve_save_text_path(str(out_dir) + "/", preferred_stem="rfc5905")
    assert resolved == out_dir / "rfc5905.txt"

    file_path = tmp_path / "custom.txt"
    assert _resolve_save_text_path(str(file_path), preferred_stem="x") == file_path

    src = tmp_path / "sample.txt"
    src.write_text("hello timers", encoding="utf-8")
    result = ingest_to_text(
        str(src),
        options=IngestOptions(save_text_path=str(out_dir) + "/"),
    )
    assert Path(result.saved_text_path or "").name == "sample.txt"
    assert (out_dir / "sample.txt").read_text(encoding="utf-8") == "hello timers"
