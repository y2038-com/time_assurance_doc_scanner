# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Ingest resource-containment tests: DOCX preflight, converted text, CLI."""

from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.ingest import IngestError, IngestOptions, ingest_to_text
from tads.ingest.archives import _assert_zip_member_allowed
from tads.ingest.limits import drain_limited, read_limited, validate_ingest_options
from tads.ingest.types import (
    DEFAULT_MAX_CONTAINER_MEMBERS,
    DEFAULT_MAX_CONVERTED_CHARS,
)


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _docx_plus_parts(
    extra: dict[str, bytes],
    *,
    paragraphs: list[str] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    base = _make_docx_bytes(paragraphs or ["Hello timers"])
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base), "r") as src:
        with zipfile.ZipFile(out, "w", compression=compression) as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            for name, payload in extra.items():
                dst.writestr(name, payload)
    return out.getvalue()


def _zip_bytes(members: dict[str, bytes], *, compression: int = zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_pdf_pages(texts: list[str]) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _docx_part_stats(data: bytes) -> tuple[int, int, int]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        parts = [info for info in zf.infolist() if not info.is_dir()]
    return (
        len(parts),
        max(info.file_size for info in parts),
        sum(info.file_size for info in parts),
    )


def test_valid_docx_under_defaults(tmp_path: Path):
    src = tmp_path / "ok.docx"
    src.write_bytes(_make_docx_bytes(["Epoch wrap risk"]))
    result = ingest_to_text(str(src))
    assert "Epoch wrap" in result.text
    assert result.converter == "python-docx"


def test_docx_part_at_and_above_declared_size():
    info = zipfile.ZipInfo("word/document.xml")
    info.file_size = 40
    info.compress_size = 40
    _assert_zip_member_allowed(
        info,
        max_archive_member_bytes=40,
        max_archive_expansion_ratio=200,
    )
    info.file_size = 41
    with pytest.raises(IngestError, match="uncompressed size"):
        _assert_zip_member_allowed(
            info,
            max_archive_member_bytes=40,
            max_archive_expansion_ratio=200,
        )


def test_docx_actual_part_at_and_above_drain_limit():
    class _Stream:
        def __init__(self, payload: bytes) -> None:
            self._data = payload

        def read(self, size: int = -1) -> bytes:
            if not self._data:
                return b""
            if size < 0:
                chunk, self._data = self._data, b""
                return chunk
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk

    assert drain_limited(
        _Stream(b"a" * 40),
        max_part_bytes=40,
        max_remaining_cumulative=40,
        label="part",
    ) == 40
    with pytest.raises(IngestError, match="uncompressed size"):
        drain_limited(
            _Stream(b"a" * 41),
            max_part_bytes=40,
            max_remaining_cumulative=100,
            label="part",
        )


def test_docx_cumulative_declared_at_and_above(tmp_path: Path):
    base = _make_docx_bytes(["Hi"])
    _count, _largest, total = _docx_part_stats(base)
    src = tmp_path / "cum.docx"
    src.write_bytes(base)
    ingest_to_text(
        str(src),
        options=IngestOptions(max_container_uncompressed_bytes=total),
    )
    with pytest.raises(IngestError, match="uncompressed size exceeds"):
        ingest_to_text(
            str(src),
            options=IngestOptions(max_container_uncompressed_bytes=total - 1),
        )


def test_docx_high_ratio_part_rejected(tmp_path: Path):
    data = _docx_plus_parts({"pad.xml": b"\x00" * 5000})
    src = tmp_path / "ratio.docx"
    src.write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo("pad.xml")
        assert info.file_size / max(info.compress_size, 1) > 20
    with pytest.raises(IngestError, match="expansion ratio"):
        ingest_to_text(
            str(src),
            options=IngestOptions(max_archive_expansion_ratio=10),
        )


def test_docx_too_many_parts_rejected(tmp_path: Path):
    extras = {f"pad/x{i}.bin": b"x" for i in range(DEFAULT_MAX_CONTAINER_MEMBERS)}
    src = tmp_path / "many.docx"
    src.write_bytes(_docx_plus_parts(extras, compression=zipfile.ZIP_STORED))
    with pytest.raises(IngestError, match="non-directory parts"):
        ingest_to_text(str(src))


def test_docx_zero_compressed_size_high_ratio():
    info = zipfile.ZipInfo("word/document.xml")
    info.file_size = 10_000
    info.compress_size = 0
    with pytest.raises(IngestError, match="expansion ratio"):
        _assert_zip_member_allowed(
            info,
            max_archive_member_bytes=100_000,
            max_archive_expansion_ratio=200,
        )


def test_malformed_docx_rejected(tmp_path: Path):
    src = tmp_path / "bad.docx"
    src.write_bytes(b"PK\x03\x04not-a-zip")
    with pytest.raises(IngestError, match="Invalid DOCX"):
        ingest_to_text(str(src))


def test_direct_docx_and_docx_in_archive(tmp_path: Path):
    payload = _make_docx_bytes(["Direct and archived"])
    direct = tmp_path / "spec.docx"
    direct.write_bytes(payload)
    assert "Direct and archived" in ingest_to_text(str(direct)).text

    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(_zip_bytes({"spec.docx": payload}))
    result = ingest_to_text(str(bundle))
    assert result.member_name == "spec.docx"
    assert "Direct and archived" in result.text


def test_plain_text_at_and_above_converted_limit(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text("a" * 20, encoding="utf-8")
    assert len(ingest_to_text(
        str(src),
        options=IngestOptions(max_converted_chars=20),
    ).text) == 20
    with pytest.raises(IngestError, match="max_converted_chars"):
        ingest_to_text(str(src), options=IngestOptions(max_converted_chars=19))


def test_html_at_and_above_converted_limit(tmp_path: Path):
    src = tmp_path / "doc.html"
    src.write_text("<p>" + ("b" * 20) + "</p>", encoding="utf-8")
    result = ingest_to_text(
        str(src),
        options=IngestOptions(max_converted_chars=20),
    )
    assert result.text.strip() == "b" * 20
    with pytest.raises(IngestError, match="max_converted_chars"):
        ingest_to_text(str(src), options=IngestOptions(max_converted_chars=19))


def test_pdf_accumulated_text_crosses_limit(tmp_path: Path):
    data = _make_pdf_pages(["ccccc", "ddddd"])
    src = tmp_path / "doc.pdf"
    src.write_bytes(data)
    ingest_to_text(str(src), options=IngestOptions(max_converted_chars=10_000))
    with pytest.raises(IngestError, match="max_converted_chars"):
        ingest_to_text(str(src), options=IngestOptions(max_converted_chars=3))


def test_archive_text_crosses_converted_limit(tmp_path: Path):
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(_zip_bytes({"doc.txt": b"e" * 30}))
    ingest_to_text(str(bundle), options=IngestOptions(max_converted_chars=30))
    with pytest.raises(IngestError, match="max_converted_chars"):
        ingest_to_text(str(bundle), options=IngestOptions(max_converted_chars=29))


def test_limit_failure_does_not_write_save_text(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text("f" * 50, encoding="utf-8")
    dest = tmp_path / "out.txt"
    with pytest.raises(IngestError, match="max_converted_chars"):
        ingest_to_text(
            str(src),
            options=IngestOptions(max_converted_chars=10, save_text_path=str(dest)),
        )
    assert not dest.exists()


def test_overwrite_approved_then_limit_failure_leaves_existing_file(
    tmp_path: Path,
):
    src = tmp_path / "doc.txt"
    src.write_text("g" * 50, encoding="utf-8")
    dest = tmp_path / "out.txt"
    dest.write_text("keep-me", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "convert",
            str(src),
            "-o",
            str(dest),
            "--overwrite",
            "--max-converted-chars",
            "10",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "max_converted_chars" in result.output
    assert dest.read_text(encoding="utf-8") == "keep-me"


def test_plan_and_scan_do_not_call_provider_on_limit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src = tmp_path / "doc.txt"
    src.write_text("h" * 50, encoding="utf-8")

    def _boom(*_a, **_k):
        raise AssertionError("provider must not be invoked")

    monkeypatch.setattr("tads.pipeline.get_provider", _boom)
    monkeypatch.setattr("tads.llm.registry.get_provider", _boom)
    runner = CliRunner()
    plan = runner.invoke(
        app,
        [
            "plan",
            str(src),
            "--doc-id",
            "TESTDOC",
            "--provider",
            "mock",
            "--max-converted-chars",
            "10",
        ],
    )
    assert plan.exit_code == 1, plan.output
    assert "max_converted_chars" in plan.output

    scan = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "TESTDOC",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--max-converted-chars",
            "10",
        ],
    )
    assert scan.exit_code == 1, scan.output
    assert "max_converted_chars" in scan.output


@pytest.mark.parametrize(
    "args",
    (
        ["--max-download-mb", "0"],
        ["--max-download-mb", "-1"],
        ["--max-archive-member-mb", "0"],
        ["--max-archive-expansion-ratio", "0"],
        ["--max-archive-expansion-ratio", "nan"],
        ["--max-converted-chars", "0"],
        ["--max-converted-chars", "-3"],
    ),
)
def test_cli_rejects_non_positive_limits(tmp_path: Path, args: list[str]):
    src = tmp_path / "doc.txt"
    src.write_text("ok", encoding="utf-8")
    dest = tmp_path / "out.txt"
    result = CliRunner().invoke(
        app,
        ["convert", str(src), "-o", str(dest), *args],
    )
    assert result.exit_code != 0
    assert not dest.exists()


def test_ingest_options_reject_invalid_limits():
    with pytest.raises(IngestError, match="positive"):
        validate_ingest_options(IngestOptions(max_download_bytes=0))
    with pytest.raises(IngestError, match="positive"):
        validate_ingest_options(IngestOptions(max_archive_expansion_ratio=-1))
    with pytest.raises(IngestError, match="positive"):
        validate_ingest_options(IngestOptions(max_converted_chars=0))
    with pytest.raises(IngestError, match="finite"):
        validate_ingest_options(
            IngestOptions(max_archive_expansion_ratio=math.inf)
        )


def test_local_oversize_file_rejected_before_full_buffer(tmp_path: Path):
    src = tmp_path / "big.txt"
    src.write_bytes(b"x" * 1000)
    with pytest.raises(IngestError, match="max size"):
        ingest_to_text(str(src), options=IngestOptions(max_download_bytes=50))


def test_read_limited_stops_at_cap_plus_one():
    class _Growing:
        def __init__(self) -> None:
            self.sent = 0

        def read(self, size: int = -1) -> bytes:
            if self.sent >= 200:
                return b""
            n = 200 if size < 0 else min(size, 200 - self.sent)
            self.sent += n
            return b"x" * n

    stream = _Growing()
    with pytest.raises(IngestError, match="while reading"):
        read_limited(stream, max_bytes=100, label="input")
    assert stream.sent <= 101


def test_defaults_accept_representative_small_documents(tmp_path: Path):
    assert DEFAULT_MAX_CONVERTED_CHARS == 20_000_000
    src = tmp_path / "rfc-like.txt"
    src.write_text("NTP " * 1000, encoding="utf-8")
    result = ingest_to_text(str(src))
    assert "NTP" in result.text
    dest = tmp_path / "ok.txt"
    saved = ingest_to_text(
        str(src),
        options=IngestOptions(save_text_path=str(dest)),
    )
    assert dest.read_text(encoding="utf-8") == saved.text


def test_error_messages_omit_document_body(tmp_path: Path):
    secret = "CANARY_SECRET_tads_limit"
    src = tmp_path / "doc.txt"
    src.write_text(secret * 20, encoding="utf-8")
    with pytest.raises(IngestError) as exc:
        ingest_to_text(str(src), options=IngestOptions(max_converted_chars=10))
    assert secret not in str(exc.value)
