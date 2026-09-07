# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Resolve local paths or URLs into plain text for plan/scan."""

from __future__ import annotations

import os
from pathlib import Path

from tads.ingest.archives import extract_preferred_member
from tads.ingest.detect import detect_media_type, looks_like_url
from tads.ingest.docx_convert import docx_to_text
from tads.ingest.fetch import IngestError, fetch_url
from tads.ingest.html_convert import html_to_text
from tads.ingest.pdf_convert import pdf_to_text
from tads.ingest.types import DEFAULT_MAX_DOWNLOAD_BYTES, IngestOptions, IngestResult


def default_max_download_bytes() -> int:
    raw = os.getenv("TADS_MAX_DOWNLOAD_MB") or os.getenv("TADS_MAX_DOWNLOAD_BYTES")
    if not raw:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        if os.getenv("TADS_MAX_DOWNLOAD_MB") and not os.getenv("TADS_MAX_DOWNLOAD_BYTES"):
            return int(float(raw) * 1024 * 1024)
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_DOWNLOAD_BYTES


def ingest_to_text(
    source: str,
    *,
    options: IngestOptions | None = None,
) -> IngestResult:
    """
    Load a local file or URL and return converted plain text.

    Supports .txt, .docx, .pdf, .html/.htm, .zip, .tgz/.tar.gz. Ephemeral by
    default; set options.save_text_path to persist converted text.
    """
    opts = options or IngestOptions(max_download_bytes=default_max_download_bytes())
    if opts.max_download_bytes <= 0:
        opts.max_download_bytes = DEFAULT_MAX_DOWNLOAD_BYTES

    notes: list[str] = []
    if looks_like_url(source):
        # Allow HTML when explicitly requested or the URL looks like an HTML doc.
        allow_html = opts.allow_html or source.lower().rstrip("/").endswith(
            (".html", ".htm")
        )
        fetched = fetch_url(
            source,
            max_bytes=opts.max_download_bytes,
            timeout_seconds=opts.timeout_seconds,
            allow_html=allow_html,
            allow_private_url=opts.allow_private_url,
        )
        data = fetched.data
        name = fetched.filename
        media_type = fetched.media_type
        notes.extend(fetched.notes)
        if not any(n.startswith("Fetched ") for n in fetched.notes):
            notes.append(f"Fetched {len(data)} bytes from URL.")
        bytes_fetched = len(data)
        source_recorded = fetched.url
    else:
        path = Path(source).expanduser()
        if not path.exists() or not path.is_file():
            raise IngestError(f"Input not found or not a file: {source}")
        data = path.read_bytes()
        if len(data) > opts.max_download_bytes:
            raise IngestError(
                f"Local file exceeds max size ({opts.max_download_bytes} bytes): {path}"
            )
        name = path.name
        media_type = detect_media_type(name=name, data=data)
        bytes_fetched = len(data)
        source_recorded = source

    member_name = None
    converter = "identity"

    if media_type in {"zip", "tar"}:
        # Nested archives are rejected below after one extraction; recursive
        # archive processing would need independent depth/size controls.
        member = extract_preferred_member(
            data,
            archive_kind=media_type,
            archive_member=opts.archive_member,
            max_archive_member_bytes=opts.max_archive_member_bytes,
            max_archive_expansion_ratio=opts.max_archive_expansion_ratio,
        )
        member_name = member.name
        data = member.data
        media_type = detect_media_type(name=member.name, data=data)
        notes.append(f"Extracted archive member: {member_name}")

    if media_type == "text":
        text = data.decode("utf-8", errors="replace")
        converter = "identity"
    elif media_type == "docx":
        text = docx_to_text(data)
        converter = "python-docx"
    elif media_type == "pdf":
        text = pdf_to_text(data)
        converter = "pymupdf"
    elif media_type == "html":
        text = html_to_text(data)
        converter = "html-text"
        notes.append("Converted HTML to plain text.")
    elif media_type in {"zip", "tar"}:
        raise IngestError(
            "Nested archives are not supported; pass --archive-member to a document file."
        )
    else:
        raise IngestError(
            f"Unsupported input type for {name!r} (detected={media_type}). "
            "Supported: .txt, .docx, .pdf, .html, .zip, .tgz"
        )

    if not text.strip():
        raise IngestError(f"Conversion produced empty text from {name!r}")

    saved_path = None
    if opts.save_text_path:
        out = _resolve_save_text_path(
            opts.save_text_path,
            preferred_stem=_preferred_stem(name, member_name),
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        saved_path = str(out)
        notes.append(f"Saved converted text to {saved_path}")

    return IngestResult(
        text=text,
        source=source_recorded,
        media_type=media_type,
        member_name=member_name,
        converter=converter,
        notes=notes,
        saved_text_path=saved_path,
        bytes_fetched=bytes_fetched,
    )


def _preferred_stem(name: str, member_name: str | None) -> str:
    base = (member_name or name).rsplit("/", 1)[-1]
    stem = Path(base).stem or "document"
    return stem


def _resolve_save_text_path(save_text_path: str, *, preferred_stem: str) -> Path:
    """If PATH is a directory (or ends with /), write preferred_stem.txt inside it."""
    out = Path(save_text_path).expanduser()
    if save_text_path.endswith(("/", "\\")) or (out.exists() and out.is_dir()):
        return out / f"{preferred_stem}.txt"
    return out
