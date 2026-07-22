"""Sectionization helpers and analysis-mode selection."""

from __future__ import annotations

import re

from tads.parsing.document import ParsedDocument, Section
from tads.schemas.report import AnalysisMode

# Rough heuristic: ~4 characters per token for English/technical prose.
CHARS_PER_TOKEN = 4

# Leave headroom for system prompt, schema instructions, and model output.
DEFAULT_CONTEXT_TOKEN_BUDGET = 100_000
DEFAULT_OUTPUT_RESERVE_TOKENS = 4_000

_RFC_SECTION = re.compile(
    r"^(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>\S.*)$",
)


def estimate_tokens(text: str) -> int:
    """Estimate tokens without a provider-specific tokenizer."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def choose_analysis_mode(
    document: ParsedDocument,
    *,
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE_TOKENS,
    prompt_overhead_tokens: int = 2_000,
) -> AnalysisMode:
    """Prefer whole-document analysis when the doc fits; else section-aware."""
    available = context_token_budget - output_reserve_tokens - prompt_overhead_tokens
    if available <= 0:
        return AnalysisMode.SECTION_AWARE
    if estimate_tokens(document.text) <= available:
        return AnalysisMode.WHOLE_DOCUMENT
    return AnalysisMode.SECTION_AWARE


def section_plain_text_rfc(text: str) -> list[Section]:
    """
    Split IETF-style plain text into numbered sections.

    This is intentionally heuristic for Phase 0. Phase 1 may refine with
    stronger RFC/I-D parsers while keeping the Section model stable.
    """
    lines = text.splitlines(keepends=True)
    # Collect candidate headings with character offsets
    headings: list[tuple[int, str, str, int]] = []
    offset = 0
    for line in lines:
        stripped = line.strip()
        match = _RFC_SECTION.match(stripped)
        if match and _looks_like_section_heading(stripped, match.group("num")):
            level = match.group("num").count(".") + 1
            headings.append(
                (offset, match.group("num"), match.group("title").strip(), level)
            )
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
    # Preamble before first section
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
        sections.append(
            Section(
                id=f"s-{num}",
                title=f"{num}. {title}",
                text=text[start:end],
                level=level,
                start_char=start,
                end_char=end,
            )
        )
    return sections


def _looks_like_section_heading(line: str, num: str) -> bool:
    # Avoid treating table-of-contents dotted leaders as headings.
    if ".." in line or " . " in line:
        return False
    # Single-number lines that are too long are unlikely headings.
    if len(line) > 120:
        return False
    # Require at least one letter in the title portion.
    title_part = line[len(num) :].lstrip(". ").strip()
    return any(c.isalpha() for c in title_part)
