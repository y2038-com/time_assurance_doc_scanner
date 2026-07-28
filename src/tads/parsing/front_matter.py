# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Front matter / TOC detection helpers."""

from __future__ import annotations

import re

from tads.parsing.document import Section

# TOC lines from Word/PDF extracts often look like:
#   1<TAB>Scope<TAB>25
#   4.2.1 General ........ 45
_TOC_TAB_PAGE = re.compile(r"\t\d{1,4}\s*$")
_TOC_DOT_LEADERS = re.compile(r"\.{2,}\s*\d{1,4}\s*$")
_TOC_SPACED_PAGE = re.compile(
    r"^(?:(?:Clause|Section)\s+)?\d+(?:\.\d+)*\.?\s+\S.*\s{2,}\d{1,4}\s*$",
    re.IGNORECASE,
)
_FRONT_MATTER_IDS = frozenset({"preamble", "toc", "front_matter", "contents"})
# Section titles/ids for Index and Acknowledgments (skipped by default).
# Do not match Bibliography, References, or Annexes.
_ACK_TITLE = re.compile(r"^acknowledg(?:e)?ments?(?:\s+and\s+.+)?$", re.IGNORECASE)
_INDEX_TITLE = re.compile(r"^index(?:\s+of\s+.+)?$", re.IGNORECASE)
_ACK_ID_TOKENS = frozenset(
    {
        "acknowledgement",
        "acknowledgements",
        "acknowledgment",
        "acknowledgments",
    }
)


def is_toc_line(line: str) -> bool:
    """Return True if a line looks like a table-of-contents entry, not a real heading."""
    stripped = line.strip()
    if not stripped:
        return False
    if _TOC_TAB_PAGE.search(stripped):
        return True
    if _TOC_DOT_LEADERS.search(stripped):
        return True
    # Multi-space page number at end (common when tabs become spaces)
    if _TOC_SPACED_PAGE.match(stripped) and not stripped.lower().startswith("annex"):
        # Avoid flagging normal headings that merely end in a year-like token unless
        # the gap before the trailing number is wide.
        return True
    return False


def is_front_matter_section(section: Section) -> bool:
    if section.id.lower() in _FRONT_MATTER_IDS:
        return True
    title = section.title.lower()
    if title in {"preamble", "contents", "table of contents", "front matter"}:
        return True
    if title.startswith("contents"):
        return True
    return False


def section_looks_like_toc_blob(section: Section, *, min_toc_lines: int = 8) -> bool:
    """True when a section body is mostly TOC-style lines (fallback detector)."""
    lines = [ln.strip() for ln in section.text.splitlines() if ln.strip()]
    if len(lines) < min_toc_lines:
        return False
    toc_hits = sum(1 for ln in lines if is_toc_line(ln))
    return toc_hits >= min_toc_lines and toc_hits / len(lines) >= 0.5


def _normalized_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip())


def _id_tail_token(section_id: str) -> str:
    """Last path-like token of a section id (e.g. ``s-Acknowledgements`` → acknowledgements)."""
    raw = section_id.strip().lower().replace("_", "-")
    return raw.rsplit("-", 1)[-1] if raw else ""


def is_index_section(section: Section) -> bool:
    """True for Index / Index of … sections (by title or id), not 'indexing' prose headings."""
    if _id_tail_token(section.id) == "index":
        return True
    return bool(_INDEX_TITLE.match(_normalized_title(section.title)))


def is_acknowledgments_section(section: Section) -> bool:
    """True for Acknowledgement(s)/Acknowledgment(s) sections (US/UK spelling)."""
    if _id_tail_token(section.id) in _ACK_ID_TOKENS:
        return True
    return bool(_ACK_TITLE.match(_normalized_title(section.title)))


def is_index_or_acknowledgments_section(section: Section) -> bool:
    """Sections skipped by default unless ``--include-index-and-acknowledgments``."""
    return is_index_section(section) or is_acknowledgments_section(section)
