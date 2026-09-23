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
from tads.ingest.limits import drain_limited, read_limited, require_positive_float, require_positive_int
from tads.ingest.types import (
    DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO,
    DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
    DEFAULT_MAX_CONTAINER_MEMBERS,
    DEFAULT_MAX_CONTAINER_UNCOMPRESSED_BYTES,
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
    max_container_members: int = DEFAULT_MAX_CONTAINER_MEMBERS,
) -> ArchiveMember:
    """
    Extract one document member from a zip/tar archive.

    Preference when --archive-member is omitted: .docx > .pdf > .txt

    Uncompressed member size (and ZIP expansion ratio) are capped to reduce
    decompression-bomb risk. Nested/recursive archive extraction is out of
    scope and would need independent depth/size controls if added later.

    ZIP member-count enforcement runs after ZipFile has parsed the central
    directory; it does not bound that initial allocation. TGZ counts members
    during header iteration rather than after building a complete list.
    """
    max_archive_member_bytes = require_positive_int(
        "max_archive_member_bytes", max_archive_member_bytes
    )
    max_archive_expansion_ratio = require_positive_float(
        "max_archive_expansion_ratio", max_archive_expansion_ratio
    )
    max_container_members = require_positive_int(
        "max_container_members", max_container_members
    )

    if archive_kind == "zip":
        return _extract_zip(
            data,
            archive_member=archive_member,
            max_archive_member_bytes=max_archive_member_bytes,
            max_archive_expansion_ratio=max_archive_expansion_ratio,
            max_container_members=max_container_members,
        )
    if archive_kind == "tar":
        return _extract_tar(
            data,
            archive_member=archive_member,
            max_archive_member_bytes=max_archive_member_bytes,
            max_container_members=max_container_members,
        )
    raise IngestError(f"Unsupported archive kind: {archive_kind}")


def _extract_zip(
    data: bytes,
    *,
    archive_member: str | None,
    max_archive_member_bytes: int,
    max_archive_expansion_ratio: float,
    max_container_members: int,
) -> ArchiveMember:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # namelist/infolist require the central directory already parsed.
            infos = [info for info in zf.infolist() if not _zip_info_is_dir(info)]
            if len(infos) > max_container_members:
                raise IngestError(
                    f"ZIP archive has {len(infos)} non-directory entries, "
                    f"exceeding the configured limit of {max_container_members}."
                )
            names = [info.filename for info in infos if not _ignore_member(info.filename)]
            chosen = _choose_member(names, archive_member=archive_member)
            info = zf.getinfo(chosen)
            _assert_zip_member_allowed(
                info,
                max_archive_member_bytes=max_archive_member_bytes,
                max_archive_expansion_ratio=max_archive_expansion_ratio,
            )
            with zf.open(chosen, "r") as handle:
                payload = read_limited(
                    handle,
                    max_bytes=max_archive_member_bytes,
                    label=f"Archive member {chosen!r}",
                )
            return ArchiveMember(name=chosen, data=payload)
    except zipfile.BadZipFile as exc:
        raise IngestError("Invalid ZIP archive") from exc


def _extract_tar(
    data: bytes,
    *,
    archive_member: str | None,
    max_archive_member_bytes: int,
    max_container_members: int,
) -> ArchiveMember:
    try:
        chosen, declared_size = _select_tar_member(
            data,
            archive_member=archive_member,
            max_container_members=max_container_members,
        )
        if declared_size > max_archive_member_bytes:
            raise IngestError(
                f"Archive member {chosen!r} declares an uncompressed size of "
                f"{declared_size} bytes, exceeding the configured limit of "
                f"{max_archive_member_bytes} bytes."
            )
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for info in tf:
                if info.name != chosen:
                    continue
                if not info.isfile():
                    raise IngestError(
                        f"Archive member {chosen!r} is not a regular file"
                    )
                extracted = tf.extractfile(info)
                if extracted is None:
                    raise IngestError(f"Could not extract archive member: {chosen}")
                with extracted:
                    payload = read_limited(
                        extracted,
                        max_bytes=max_archive_member_bytes,
                        label=f"Archive member {chosen!r}",
                    )
                return ArchiveMember(name=chosen, data=payload)
        raise IngestError(f"Could not extract archive member: {chosen}")
    except tarfile.TarError as exc:
        raise IngestError("Invalid tar/tgz archive") from exc


def _select_tar_member(
    data: bytes,
    *,
    archive_member: str | None,
    max_container_members: int,
) -> tuple[str, int]:
    """Count and choose a member during header iteration, not after getmembers()."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        members: list[tarfile.TarInfo] = []
        counted = 0
        for info in tf:
            if info.isdir():
                continue
            counted += 1
            if counted > max_container_members:
                raise IngestError(
                    f"TAR archive has more than {max_container_members} "
                    "non-directory entries."
                )
            if info.isfile() and not _ignore_member(info.name):
                members.append(info)
        names = [item.name for item in members]
        chosen = _choose_member(names, archive_member=archive_member)
        info = next(item for item in members if item.name == chosen)
        return chosen, info.size


def preflight_docx_package(
    data: bytes,
    *,
    max_archive_member_bytes: int = DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
    max_archive_expansion_ratio: float = DEFAULT_MAX_ARCHIVE_EXPANSION_RATIO,
    max_container_members: int = DEFAULT_MAX_CONTAINER_MEMBERS,
    max_container_uncompressed_bytes: int = DEFAULT_MAX_CONTAINER_UNCOMPRESSED_BYTES,
) -> None:
    """
    Inspect and stream-drain every DOCX package part before python-docx.

    Declared ZIP metadata is checked first. Each non-directory part is then
    decompressed through a drain that counts bytes and discards them. python-docx
    will decompress the package again afterward.
    """
    max_archive_member_bytes = require_positive_int(
        "max_archive_member_bytes", max_archive_member_bytes
    )
    max_archive_expansion_ratio = require_positive_float(
        "max_archive_expansion_ratio", max_archive_expansion_ratio
    )
    max_container_members = require_positive_int(
        "max_container_members", max_container_members
    )
    max_container_uncompressed_bytes = require_positive_int(
        "max_container_uncompressed_bytes", max_container_uncompressed_bytes
    )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            parts = [info for info in zf.infolist() if not _zip_info_is_dir(info)]
            if len(parts) > max_container_members:
                raise IngestError(
                    f"DOCX package has {len(parts)} non-directory parts, "
                    f"exceeding the configured limit of {max_container_members}."
                )
            declared_total = 0
            for info in parts:
                _assert_zip_member_allowed(
                    info,
                    max_archive_member_bytes=max_archive_member_bytes,
                    max_archive_expansion_ratio=max_archive_expansion_ratio,
                    label=f"DOCX package part {info.filename!r}",
                )
                declared_total += info.file_size
                if declared_total > max_container_uncompressed_bytes:
                    raise IngestError(
                        "DOCX package declared uncompressed size exceeds the "
                        f"configured limit of {max_container_uncompressed_bytes} bytes."
                    )
            actual_total = 0
            for info in parts:
                remaining = max_container_uncompressed_bytes - actual_total
                label = f"DOCX package part {info.filename!r}"
                try:
                    with zf.open(info, "r") as handle:
                        actual = drain_limited(
                            handle,
                            max_part_bytes=max_archive_member_bytes,
                            max_remaining_cumulative=remaining,
                            label=label,
                        )
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    raise IngestError(
                        f"Failed to read {label} during preflight."
                    ) from exc
                actual_total += actual
                if actual_total > max_container_uncompressed_bytes:
                    raise IngestError(
                        "DOCX package exceeded the maximum cumulative "
                        f"uncompressed size ({max_container_uncompressed_bytes} bytes) "
                        "while reading."
                    )
    except zipfile.BadZipFile as exc:
        raise IngestError("Invalid DOCX package") from exc


def _assert_zip_member_allowed(
    info: zipfile.ZipInfo,
    *,
    max_archive_member_bytes: int,
    max_archive_expansion_ratio: float,
    label: str | None = None,
) -> None:
    name = label or f"Archive member {info.filename!r}"
    if info.file_size > max_archive_member_bytes:
        raise IngestError(
            f"{name} declares an uncompressed size of "
            f"{info.file_size} bytes, exceeding the configured limit of "
            f"{max_archive_member_bytes} bytes."
        )
    compressed = max(info.compress_size, 1)
    ratio = info.file_size / compressed
    if ratio > max_archive_expansion_ratio:
        raise IngestError(
            f"{name} exceeds the maximum allowed expansion "
            f"ratio ({max_archive_expansion_ratio}:1); "
            f"declared uncompressed={info.file_size} compressed={info.compress_size}."
        )


def _read_limited(handle, *, max_bytes: int, member_name: str) -> bytes:
    """Compatibility wrapper used by existing archive-limit tests."""
    return read_limited(
        handle,
        max_bytes=max_bytes,
        label=f"Archive member {member_name!r}",
    )


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


def _zip_info_is_dir(info: zipfile.ZipInfo) -> bool:
    if getattr(info, "is_dir", None) is not None:
        return bool(info.is_dir())
    return info.filename.endswith("/")
