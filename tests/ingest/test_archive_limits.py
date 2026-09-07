# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Archive decompression / expansion-limit tests."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from tads.ingest import IngestError, IngestOptions, ingest_to_text
from tads.ingest.archives import (
    _read_limited,
    extract_preferred_member,
)
from tads.ingest.types import (
    DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO,
    DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
)


def _zip_bytes(
    members: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tar_bytes(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for info, data in members:
            if data is None:
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_zip_small_member_succeeds():
    data = _zip_bytes({"notes.txt": b"epoch wrap risk"})
    member = extract_preferred_member(data, archive_kind="zip")
    assert member.name == "notes.txt"
    assert b"epoch" in member.data


def test_zip_stored_member_succeeds():
    data = _zip_bytes(
        {"plain.txt": b"stored member ok"},
        compression=zipfile.ZIP_STORED,
    )
    member = extract_preferred_member(data, archive_kind="zip")
    assert member.data == b"stored member ok"


def test_zip_member_just_under_limit_succeeds():
    payload = b"a" * 40
    data = _zip_bytes({"a.txt": payload}, compression=zipfile.ZIP_STORED)
    member = extract_preferred_member(
        data,
        archive_kind="zip",
        max_archive_member_bytes=40,
        max_archive_expansion_ratio=1000,
    )
    assert len(member.data) == 40


def test_zip_member_above_size_limit_rejected():
    payload = b"b" * 100
    data = _zip_bytes({"big.txt": payload}, compression=zipfile.ZIP_STORED)
    with pytest.raises(IngestError, match="uncompressed size"):
        extract_preferred_member(
            data,
            archive_kind="zip",
            max_archive_member_bytes=50,
            max_archive_expansion_ratio=1000,
        )


def test_zip_high_expansion_ratio_rejected():
    # Highly compressible payload → high file_size/compress_size ratio.
    payload = b"\x00" * 5000
    data = _zip_bytes({"zeros.txt": payload}, compression=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo("zeros.txt")
        assert info.file_size / max(info.compress_size, 1) > 20
    with pytest.raises(IngestError, match="expansion ratio"):
        extract_preferred_member(
            data,
            archive_kind="zip",
            max_archive_member_bytes=100_000,
            max_archive_expansion_ratio=10,
        )


def test_zip_selection_prefers_docx():
    data = _zip_bytes(
        {
            "readme.txt": b"txt",
            "spec/body.docx": b"PK-fake-docx",
            "other.pdf": b"%PDF",
        }
    )
    member = extract_preferred_member(data, archive_kind="zip")
    assert member.name.endswith("body.docx")


def test_read_limited_aborts_when_stream_exceeds_cap():
    class _Growing:
        def __init__(self) -> None:
            self._n = 0

        def read(self, size: int = -1) -> bytes:
            if self._n >= 200:
                return b""
            self._n += 50
            return b"x" * 50

    with pytest.raises(IngestError, match="while reading"):
        _read_limited(_Growing(), max_bytes=100, member_name="x.bin")


def test_tar_small_regular_file_succeeds():
    info = tarfile.TarInfo(name="doc.txt")
    data = _tar_bytes([(info, b"tar content")])
    member = extract_preferred_member(data, archive_kind="tar")
    assert member.data == b"tar content"


def test_tar_member_above_limit_rejected():
    info = tarfile.TarInfo(name="big.txt")
    data = _tar_bytes([(info, b"y" * 200)])
    with pytest.raises(IngestError, match="uncompressed size"):
        extract_preferred_member(
            data,
            archive_kind="tar",
            max_archive_member_bytes=50,
        )


def test_tar_symlink_not_selected():
    link = tarfile.TarInfo(name="alias.txt")
    link.type = tarfile.SYMTYPE
    link.linkname = "real.txt"
    real = tarfile.TarInfo(name="real.txt")
    data = _tar_bytes([(link, None), (real, b"only real")])
    member = extract_preferred_member(data, archive_kind="tar")
    assert member.name == "real.txt"
    assert member.data == b"only real"


def test_tar_symlink_only_archive_has_no_files():
    link = tarfile.TarInfo(name="alias.txt")
    link.type = tarfile.SYMTYPE
    link.linkname = "missing"
    data = _tar_bytes([(link, None)])
    with pytest.raises(IngestError, match="no extractable files"):
        extract_preferred_member(data, archive_kind="tar")


def test_tar_hardlink_ignored():
    target = tarfile.TarInfo(name="target.txt")
    link = tarfile.TarInfo(name="hard.txt")
    link.type = tarfile.LNKTYPE
    link.linkname = "target.txt"
    data = _tar_bytes([(target, b"payload"), (link, None)])
    member = extract_preferred_member(data, archive_kind="tar")
    assert member.name == "target.txt"


def test_tar_device_ignored():
    dev = tarfile.TarInfo(name="dev")
    dev.type = tarfile.CHRTYPE
    text = tarfile.TarInfo(name="ok.txt")
    data = _tar_bytes([(dev, None), (text, b"ok")])
    member = extract_preferred_member(data, archive_kind="tar")
    assert member.name == "ok.txt"


def test_ingest_options_defaults_propagate():
    opts = IngestOptions()
    assert opts.max_archive_member_bytes == DEFAULT_MAX_ARCHIVE_MEMBER_BYTES
    assert opts.max_archive_expansion_ratio == DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO


def test_ingest_pipeline_honors_archive_member_limit(tmp_path):
    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(
        _zip_bytes({"doc.txt": b"z" * 80}, compression=zipfile.ZIP_STORED)
    )
    with pytest.raises(IngestError, match="uncompressed size"):
        ingest_to_text(
            str(zip_path),
            options=IngestOptions(
                max_archive_member_bytes=40,
                max_archive_expansion_ratio=1000,
            ),
        )


def test_non_archive_ingest_unaffected(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("plain text path", encoding="utf-8")
    result = ingest_to_text(
        str(path),
        options=IngestOptions(max_archive_member_bytes=1),
    )
    assert "plain text" in result.text
