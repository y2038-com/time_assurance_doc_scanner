# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared ingest types and options."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB

# Preference when an archive contains multiple convertible members.
MEMBER_PREFERENCE = (".docx", ".pdf", ".txt", ".text")


@dataclass
class IngestOptions:
    """Controls for fetch/convert (privacy-preserving defaults)."""

    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    archive_member: Optional[str] = None
    save_text_path: Optional[str] = None
    timeout_seconds: float = 120.0
    # When False, HTML payloads from URLs raise (avoids login pages as "docs").
    # Local .html/.htm files and allow_html=True always convert.
    allow_html: bool = False


@dataclass
class IngestResult:
    """Plain text ready for plan/scan, plus provenance metadata."""

    text: str
    source: str  # original path or URL
    media_type: str
    member_name: Optional[str] = None
    converter: str = "identity"
    notes: list[str] = field(default_factory=list)
    saved_text_path: Optional[str] = None
    bytes_fetched: int = 0
