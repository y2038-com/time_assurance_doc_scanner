# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Rewrite known awkward document URLs to stable public mirrors."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_RFC_IN_PATH = re.compile(r"rfc[-_]?(\d+)", re.IGNORECASE)


def rewrite_document_url(url: str) -> tuple[str, str | None]:
    """
    Return (possibly rewritten URL, note).

    tools.ietf.org and datatracker PDF/HTML links often bounce to a login wall.
    Prefer the public RFC Editor plain-text mirror (universally available; PDF
    exists only for newer RFCs).
    """
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if host in {
        "tools.ietf.org",
        "www.tools.ietf.org",
        "datatracker.ietf.org",
        "www.datatracker.ietf.org",
    }:
        rfc_num = _extract_rfc_number(path)
        if rfc_num is None:
            return url, None
        rewritten = f"https://www.rfc-editor.org/rfc/rfc{rfc_num}.txt"
        return rewritten, (
            f"Rewrote IETF URL to public RFC Editor text: {rewritten}"
        )

    return url, None


def rfc_editor_text_fallback(url: str) -> str | None:
    """If url is an rfc-editor PDF that may 404 for older RFCs, return .txt twin."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in {"www.rfc-editor.org", "rfc-editor.org"}:
        return None
    path = parsed.path or ""
    if not path.lower().endswith(".pdf"):
        return None
    rfc_num = _extract_rfc_number(path)
    if rfc_num is None:
        return None
    return f"https://www.rfc-editor.org/rfc/rfc{rfc_num}.txt"


def _extract_rfc_number(path: str) -> str | None:
    match = _RFC_IN_PATH.search(path)
    if match:
        return str(int(match.group(1)))
    return None
