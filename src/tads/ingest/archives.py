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
from tads.ingest.types import MEMBER_PREFERENCE


@dataclass
class ArchiveMember:
    name: str
    data: bytes


def extract_preferred_member(
    data: bytes,
    *,
    archive_kind: str,
    archive_member: str | None = None,
) -> ArchiveMember:
    """
    Extract one document member from a zip/tar archive.

    Preference when --archive-member is omitted: .docx > .pdf > .txt
    """
    if archive_kind == "zip":
        return _extract_zip(data, archive_member=archive_member)
    if archive_kind == "tar":
        return _extract_tar(data, archive_member=archive_member)
    raise IngestError(f"Unsupported archive kind: {archive_kind}")


def _extract_zip(data: bytes, *, archive_member: str | None) -> ArchiveMember:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [
                n
                for n in zf.namelist()
                if not n.endswith("/")
                and not _ignore_member(n)
            ]
            chosen = _choose_member(names, archive_member=archive_member)
            return ArchiveMember(name=chosen, data=zf.read(chosen))
    except zipfile.BadZipFile as exc:
        raise IngestError("Invalid ZIP archive") from exc


def _extract_tar(data: bytes, *, archive_member: str | None) -> ArchiveMember:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            names = [
                m.name
                for m in tf.getmembers()
                if m.isfile() and not _ignore_member(m.name)
            ]
            chosen = _choose_member(names, archive_member=archive_member)
            extracted = tf.extractfile(chosen)
            if extracted is None:
                raise IngestError(f"Could not extract archive member: {chosen}")
            return ArchiveMember(name=chosen, data=extracted.read())
    except tarfile.TarError as exc:
        raise IngestError("Invalid tar/tgz archive") from exc


def _choose_member(names: list[str], *, archive_member: str | None) -> str:
    if not names:
        raise IngestError("Archive contains no extractable files")
    if archive_member:
        # Exact or suffix match
        for name in names:
            if name == archive_member or name.endswith("/" + archive_member) or name.endswith(archive_member):
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
