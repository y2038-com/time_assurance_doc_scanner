# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Clause-oriented sectionization for ETSI / 3GPP / similar specs."""

from __future__ import annotations

import re

from tads.parsing.document import Section
from tads.parsing.front_matter import is_toc_line

# 4.2.1 Title   or   Clause 4.2.1 Title
_CLAUSE = re.compile(
    r"^(?:(?:Clause|Section)\s+)?"
    r"(?P<num>\d+(?:\.\d+)*)\.?\s+"
    r"(?P<title>\S.*)$",
    re.IGNORECASE,
)

# Annex A (normative): Title   or   Annex A Title
_ANNEX = re.compile(
    r"^(?P<label>Annex\s+[A-Z])(?:\s*\((?P<kind>[^)]+)\))?\s*[:.]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)


def section_plain_text_clauses(text: str) -> list[Section]:
    """
    Split standards-style plain text on numbered clauses and annexes.

    Heuristic by design; corpus adapters can refine later without changing
    the Section model.
    """
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, str, str, int]] = []
    offset = 0
    for line in lines:
        stripped = line.strip()
        annex = _ANNEX.match(stripped)
        if annex and _looks_like_heading(stripped):
            label = re.sub(r"\s+", " ", annex.group("label")).title()
            title = (annex.group("title") or "").strip() or label
            kind = (annex.group("kind") or "").strip()
            if kind:
                title = f"{title} ({kind})" if title != label else f"{label} ({kind})"
            headings.append((offset, label.replace(" ", "-"), title, 1))
            offset += len(line)
            continue
        match = _CLAUSE.match(stripped)
        if match and _looks_like_heading(stripped, match.group("num")):
            num = match.group("num")
            level = num.count(".") + 1
            headings.append((offset, num, match.group("title").strip(), level))
        offset += len(line)

    if not headings:
        return [
            Section(
                id="body",
                title="Document body",
                text=text,
                level=1,
                start_char=0,
                end_char=len(text),
            )
        ]

    sections: list[Section] = []
    first_start = headings[0][0]
    if first_start > 0:
        preamble = text[:first_start].strip()
        if preamble:
            sections.append(
                Section(
                    id="preamble",
                    title="Preamble",
                    text=text[:first_start],
                    level=1,
                    start_char=0,
                    end_char=first_start,
                )
            )

    for idx, (start, num, title, level) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        section_id = num if num.lower().startswith("annex") else f"s-{num}"
        display = title if num.lower().startswith("annex") else f"{num} {title}"
        sections.append(
            Section(
                id=section_id,
                title=display,
                text=text[start:end],
                level=level,
                start_char=start,
                end_char=end,
            )
        )
    return sections


def _looks_like_heading(line: str, num: str | None = None) -> bool:
    if is_toc_line(line):
        return False
    if ".." in line or len(line) > 140:
        return False
    if num is not None:
        title_part = line[len(num) :].lstrip(". ").strip()
        return any(c.isalpha() for c in title_part)
    return any(c.isalpha() for c in line)
