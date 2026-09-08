# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Clause-oriented sectionization for ETSI / 3GPP / similar specs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tads.parsing.document import Section
from tads.parsing.front_matter import is_toc_line

# 4.2.1 Title   or   Clause 4.2.1 Title  (title may be empty after split-join prep)
_CLAUSE = re.compile(
    r"^(?:(?:Clause|Section)\s+)?"
    r"(?P<num>\d+(?:\.\d+)*)\.?\s+"
    r"(?P<title>\S.*)$",
    re.IGNORECASE,
)

# Lone clause number (PDF/convert often emits number and title on separate lines).
_CLAUSE_NUM_ONLY = re.compile(
    r"^(?:(?:Clause|Section)\s+)?(?P<num>\d+(?:\.\d+)*)\.?\s*$",
    re.IGNORECASE,
)

# Annex A (normative): Title   or   Annex A Title
_ANNEX = re.compile(
    r"^(?P<label>Annex\s+[A-Z])(?:\s*\((?P<kind>[^)]+)\))?\s*[:.]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)

_ANNEX_MARKER_ONLY = re.compile(
    r"^(?P<label>Annex\s+[A-Z])(?:\s*\((?P<kind>[^)]+)\))?\s*[:.]?\s*$",
    re.IGNORECASE,
)

_RANGE_TITLE = re.compile(r"^\d+\s+to\s+(?:\d+|m)\b", re.IGNORECASE)
_COMMA_LOWER = re.compile(r",\s+[a-z]")
# Mid-sentence / list openers that rarely start real clause titles.
_PROSE_OPENER = re.compile(
    r"^(?:"
    r"Unless\b|If\b|When\b|Where\b|While\b|"
    r"The\b|This\b|These\b|Those\b|There\b|"
    r"For\b|In\b|On\b|At\b|With\b|Without\b|After\b|Before\b|"
    r"shall\b|should\b|may\b|must\b|can\b|"
    r"presented\b"
    r")\b",
    re.IGNORECASE,
)

_MAX_HEADING_CHARS = 120
_MAX_TITLE_CHARS = 100


@dataclass(frozen=True)
class ClauseParseHealth:
    """Lightweight sanity check for clause sectionization quality."""

    ok: bool
    notes: list[str] = field(default_factory=list)
    section_count: int = 0
    annex_count: int = 0
    tiny_section_count: int = 0
    sentence_like_title_count: int = 0


def section_plain_text_clauses(text: str) -> list[Section]:
    """
    Split standards-style plain text on numbered clauses and annexes.

    Applies a light logical normalization for converter output where a clause
    number (or annex marker) and its title land on consecutive lines.
    Heuristic by design; corpus adapters can refine later without changing
    the Section model.
    """
    lines = text.splitlines(keepends=True)
    # (offset, end_of_heading_block, id_label, title, level)
    headings: list[tuple[int, int, str, str, int]] = []
    offset = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        line_start = offset
        line_end = offset + len(line)

        if not stripped:
            offset = line_end
            i += 1
            continue

        # --- Annex ---
        annex_only = _ANNEX_MARKER_ONLY.match(stripped)
        if annex_only:
            nxt_i, nxt_line, nxt_stripped = _next_nonblank(lines, i + 1, line_end)
            title = ""
            block_end = line_end
            if nxt_stripped and _is_plausible_title(nxt_stripped, for_annex=True):
                title = nxt_stripped
                block_end = _offset_after_line(lines, nxt_i)
                i = nxt_i  # consume title line
            if title and _accept_annex_heading(stripped, title):
                label = re.sub(r"\s+", " ", annex_only.group("label")).title()
                kind = (annex_only.group("kind") or "").strip()
                display = title
                if kind:
                    display = f"{title} ({kind})"
                headings.append(
                    (line_start, block_end, label.replace(" ", "-"), display, 1)
                )
            offset = _offset_after_line(lines, i) if title else line_end
            i += 1
            continue

        annex = _ANNEX.match(stripped)
        if annex and not _ANNEX_MARKER_ONLY.match(stripped):
            title = (annex.group("title") or "").strip()
            if _accept_annex_heading(stripped, title):
                label = re.sub(r"\s+", " ", annex.group("label")).title()
                kind = (annex.group("kind") or "").strip()
                display = title or label
                if kind:
                    display = (
                        f"{display} ({kind})" if display != label else f"{label} ({kind})"
                    )
                headings.append(
                    (line_start, line_end, label.replace(" ", "-"), display, 1)
                )
            offset = line_end
            i += 1
            continue

        # --- Numbered clause (possibly split across lines) ---
        num_only = _CLAUSE_NUM_ONLY.match(stripped)
        if num_only:
            num = num_only.group("num")
            nxt_i, nxt_line, nxt_stripped = _next_nonblank(lines, i + 1, line_end)
            if nxt_stripped and _is_plausible_title(nxt_stripped):
                logical = f"{num} {nxt_stripped}"
                if _looks_like_heading(logical, num):
                    level = num.count(".") + 1
                    block_end = _offset_after_line(lines, nxt_i)
                    headings.append(
                        (line_start, block_end, num, nxt_stripped.strip(), level)
                    )
                    offset = block_end
                    i = nxt_i + 1
                    continue
            offset = line_end
            i += 1
            continue

        match = _CLAUSE.match(stripped)
        if match and _looks_like_heading(stripped, match.group("num")):
            num = match.group("num")
            level = num.count(".") + 1
            headings.append(
                (line_start, line_end, num, match.group("title").strip(), level)
            )

        offset = line_end
        i += 1

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

    for idx, (start, _block_end, num, title, level) in enumerate(headings):
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


def assess_clause_parse_health(
    sections: list[Section],
    *,
    document_chars: int | None = None,
) -> ClauseParseHealth:
    """
    Flag suspicious clause parses (pseudo-sections, sentence-like titles, etc.).

    Used before trusting section-aware coverage arithmetic.
    """
    notes: list[str] = []
    # Ignore synthetic single-body fallback.
    real = [s for s in sections if s.id not in {"body", "preamble", "toc"}]
    if not real:
        return ClauseParseHealth(ok=True, notes=[], section_count=0)

    annex = [s for s in real if s.id.lower().startswith("annex")]
    tiny = [s for s in real if len(s.text.strip()) < 40]
    sentence_like = [
        s
        for s in real
        if s.title.rstrip().endswith((".", ";", ","))
        or _COMMA_LOWER.search(s.title)
        or _RANGE_TITLE.match(_title_only(s))
    ]

    doc_chars = document_chars
    if doc_chars is None:
        doc_chars = sum(len(s.text) for s in sections)

    ok = True
    n = len(real)
    annex_n = len(annex)
    tiny_n = len(tiny)
    sent_n = len(sentence_like)

    if n >= 40 and tiny_n / n >= 0.25:
        chars_per = doc_chars / max(n, 1)
        # Attribute-heavy specs often have short clauses; only flag when
        # overall density also looks over-segmented.
        if chars_per < 300:
            ok = False
            notes.append(
                f"Clause parse health: {tiny_n}/{n} sections are tiny (<40 chars) "
                f"and density is ~{chars_per:.0f} chars/section; structure may be "
                "unreliable."
            )
    if n >= 20 and sent_n / n >= 0.15:
        ok = False
        notes.append(
            f"Clause parse health: {sent_n}/{n} section titles look sentence-like; "
            "possible false headings."
        )
    if n >= 15 and annex_n / n >= 0.35:
        ok = False
        notes.append(
            f"Clause parse health: annex share {annex_n}/{n} is unusually high."
        )
    if doc_chars >= 5000 and n >= 80:
        chars_per = doc_chars / n
        if chars_per < 180:
            ok = False
            notes.append(
                f"Clause parse health: {n} sections for {doc_chars} chars "
                f"(~{chars_per:.0f} chars/section); possible over-segmentation."
            )
    # Dense false positives on short docs (e.g. excerpt with many FPs).
    if 5 <= n <= 40 and sent_n >= 3 and sent_n / n >= 0.2:
        ok = False
        if not any("sentence-like" in note for note in notes):
            notes.append(
                f"Clause parse health: {sent_n}/{n} sentence-like titles on a "
                "modest section set."
            )

    if not ok and not notes:
        notes.append("Clause parse health: suspicious section structure.")

    return ClauseParseHealth(
        ok=ok,
        notes=notes,
        section_count=n,
        annex_count=annex_n,
        tiny_section_count=tiny_n,
        sentence_like_title_count=sent_n,
    )


def _title_only(section: Section) -> str:
    title = section.title.strip()
    if section.id.startswith("s-"):
        num = section.id[2:]
        if title.startswith(num):
            return title[len(num) :].lstrip(". \t")
    return title


def _next_nonblank(
    lines: list[str], start_i: int, start_offset: int
) -> tuple[int, str, str]:
    """Return (index, raw_line, stripped) of next non-blank line, or (-1, '', '')."""
    offset = start_offset
    for j in range(start_i, len(lines)):
        raw = lines[j]
        stripped = raw.strip()
        if stripped:
            return j, raw, stripped
        offset += len(raw)
    return -1, "", ""


def _offset_after_line(lines: list[str], index: int) -> int:
    return sum(len(lines[k]) for k in range(index + 1))


def _is_plausible_title(title: str, *, for_annex: bool = False) -> bool:
    t = title.strip()
    if not t or len(t) > _MAX_TITLE_CHARS:
        return False
    if is_toc_line(t):
        return False
    if ".." in t:
        return False
    if t.rstrip().endswith((".", ";", ",", ":")):
        return False
    if _COMMA_LOWER.search(t):
        return False
    if _RANGE_TITLE.match(t):
        return False
    if re.fullmatch(r"\d+", t):
        return False
    if _CLAUSE_NUM_ONLY.match(t) or _ANNEX_MARKER_ONLY.match(t):
        return False
    if _ANNEX.match(t) and not for_annex:
        return False
    if not any(c.isalpha() for c in t):
        return False
    # Numbered list / table index lines: "10 Source IPv4 Address" is ok as title,
    # but a lone short ALL-CAPS table header after a blank is still a title — allow.
    if _PROSE_OPENER.match(t) and (len(t) > 48 or t[:1].islower()):
        return False
    # Reject titles that are clearly continuing prose (lowercase start).
    if t[0].islower():
        return False
    return True


def _accept_annex_heading(line: str, title: str) -> bool:
    if is_toc_line(line) or is_toc_line(f"{line} {title}".strip()):
        return False
    if not title.strip():
        return False
    if len(line) > _MAX_HEADING_CHARS:
        return False
    if ".." in line:
        return False
    combined = f"{line} {title}".strip() if title else line
    if combined.rstrip().endswith((".", ";", ",")):
        return False
    if _COMMA_LOWER.search(title):
        return False
    return _is_plausible_title(title, for_annex=True) or (
        any(c.isalpha() for c in title) and len(title) <= _MAX_TITLE_CHARS
    )


def _looks_like_heading(line: str, num: str | None = None) -> bool:
    if is_toc_line(line):
        return False
    if ".." in line or len(line) > _MAX_HEADING_CHARS:
        return False
    if num is None:
        return any(c.isalpha() for c in line)

    title_part = line[len(num) :].lstrip(". \t").strip()
    # Strip optional "Clause "/"Section " prefix already consumed by regex; title_part
    # is everything after the numeric id on the logical line.
    # Re-extract via regex for robustness when "Clause N Title" form is used.
    match = _CLAUSE.match(line.strip())
    if match:
        title_part = match.group("title").strip()

    if not _is_plausible_title(title_part):
        return False

    # Reject year-/count-like bare integers used as "clause" numbers with prose titles.
    if "." not in num:
        try:
            value = int(num)
        except ValueError:
            return False
        # Top-level clauses are usually small; huge bare numbers are prose (2038, 650…).
        if value > 40:
            return False
        # Numbered list items: "1 presented to TSG…"
        if _PROSE_OPENER.match(title_part):
            return False
        if title_part.rstrip().endswith((";", ".")):
            return False

    return True
