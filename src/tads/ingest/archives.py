# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Archive member selection and extraction."""

from __future__ import annotations

import io
import tarfile
import zipfile
from dataclasses import dataclass

from tads.ingest.detect import extension_of
from tads.ingest.fetch import IngestError
from tads.ingest.types import (
    DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO,
    DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
    MEMBER_PREFERENCE,
)


@dataclass
class ArchiveMember:
    name: str
    data: bytes


def extract_preferred_member(
    data: bytes,
    *,
    archive_kind: str,
    archive_member: str | None = None,
    max_archive_member_bytes: int = DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
    max_archive_expansion_ratio: float = DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO,
) -> ArchiveMember:
    """
    Extract one document member from a zip/tar archive.

    Preference when --archive-member is omitted: .docx > .pdf > .txt

    Uncompressed member size (and ZIP expansion ratio) are capped to reduce
    decompression-bomb risk. Nested/recursive archive extraction is out of
    scope and would need independent depth/size controls if added later.
    """
    if max_archive_member_bytes <= 0:
        max_archive_member_bytes = DEFAULT_MAX_ARCHIVE_MEMBER_BYTES
    if max_archive_expansion_ratio <= 0:
        max_archive_expansion_ratio = DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO

    if archive_kind == "zip":
        return _extract_zip(
            data,
            archive_member=archive_member,
            max_archive_member_bytes=max_archive_member_bytes,
            max_archive_expansion_ratio=max_archive_expansion_ratio,
        )
    if archive_kind == "tar":
        return _extract_tar(
            data,
            archive_member=archive_member,
            max_archive_member_bytes=max_archive_member_bytes,
        )
    raise IngestError(f"Unsupported archive kind: {archive_kind}")


def _extract_zip(
    data: bytes,
    *,
    archive_member: str | None,
    max_archive_member_bytes: int,
    max_archive_expansion_ratio: float,
) -> ArchiveMember:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [
                n
                for n in zf.namelist()
                if not n.endswith("/")
                and not _ignore_member(n)
            ]
            chosen = _choose_member(names, archive_member=archive_member)
            info = zf.getinfo(chosen)
            _assert_zip_member_allowed(
                info,
                max_archive_member_bytes=max_archive_member_bytes,
                max_archive_expansion_ratio=max_archive_expansion_ratio,
            )
            with zf.open(chosen, "r") as handle:
                payload = _read_limited(
                    handle,
                    max_bytes=max_archive_member_bytes,
                    member_name=chosen,
                )
            return ArchiveMember(name=chosen, data=payload)
    except zipfile.BadZipFile as exc:
        raise IngestError("Invalid ZIP archive") from exc


def _extract_tar(
    data: bytes,
    *,
    archive_member: str | None,
    max_archive_member_bytes: int,
) -> ArchiveMember:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = [
                m
                for m in tf.getmembers()
                if m.isfile() and not _ignore_member(m.name)
            ]
            names = [m.name for m in members]
            chosen = _choose_member(names, archive_member=archive_member)
            info = next(m for m in members if m.name == chosen)
            if not info.isfile():
                raise IngestError(
                    f"Archive member {chosen!r} is not a regular file"
                )
            if info.size > max_archive_member_bytes:
                raise IngestError(
                    f"Archive member {chosen!r} declares an uncompressed size of "
                    f"{info.size} bytes, exceeding the configured limit of "
                    f"{max_archive_member_bytes} bytes."
                )
            extracted = tf.extractfile(info)
            if extracted is None:
                raise IngestError(f"Could not extract archive member: {chosen}")
            with extracted:
                payload = _read_limited(
                    extracted,
                    max_bytes=max_archive_member_bytes,
                    member_name=chosen,
                )
            return ArchiveMember(name=chosen, data=payload)
    except tarfile.TarError as exc:
        raise IngestError("Invalid tar/tgz archive") from exc


def _assert_zip_member_allowed(
    info: zipfile.ZipInfo,
    *,
    max_archive_member_bytes: int,
    max_archive_expansion_ratio: float,
) -> None:
    name = info.filename
    if info.file_size > max_archive_member_bytes:
        raise IngestError(
            f"Archive member {name!r} declares an uncompressed size of "
            f"{info.file_size} bytes, exceeding the configured limit of "
            f"{max_archive_member_bytes} bytes."
        )
    compressed = max(info.compress_size, 1)
    ratio = info.file_size / compressed
    if ratio > max_archive_expansion_ratio:
        raise IngestError(
            f"Archive member {name!r} exceeds the maximum allowed expansion "
            f"ratio ({max_archive_expansion_ratio}:1); "
            f"declared uncompressed={info.file_size} compressed={info.compress_size}."
        )


def _read_limited(handle, *, max_bytes: int, member_name: str) -> bytes:
    """Read a stream, aborting if more than ``max_bytes`` are produced."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise IngestError(
                f"Archive member {member_name!r} exceeded the maximum "
                f"uncompressed size ({max_bytes} bytes) while reading."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _choose_member(names: list[str], *, archive_member: str | None) -> str:
    if not names:
        raise IngestError("Archive contains no extractable files")
    if archive_member:
        # Exact or suffix match
        for name in names:
            if (
                name == archive_member
                or name.endswith("/" + archive_member)
                or name.endswith(archive_member)
            ):
                return name
        # Case-insensitive basename match
        target = archive_member.lower().rsplit("/", 1)[-1]
        for name in names:
            if name.lower().rsplit("/", 1)[-1] == target:
                return name
        preview = ", ".join(names[:12])
        more = "" if len(names) <= 12 else f", … (+{len(names) - 12})"
        raise IngestError(
            f"Archive member {archive_member!r} not found. Candidates: {preview}{more}"
        )

    for pref in MEMBER_PREFERENCE:
        candidates = [
            n for n in names if extension_of(n.rsplit("/", 1)[-1]) == pref
        ]
        if candidates:
            # Prefer shortest path / non-nested when possible
            candidates.sort(key=lambda n: (n.count("/"), len(n), n.lower()))
            return candidates[0]

    preview = ", ".join(names[:12])
    raise IngestError(
        "Archive has no .docx/.pdf/.txt member to convert. "
        f"Use --archive-member. Files: {preview}"
    )


def _ignore_member(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if base.startswith("~$") or base.startswith("."):
        return True
    if "__MACOSX/" in name or name.startswith("__MACOSX"):
        return True
    return False
