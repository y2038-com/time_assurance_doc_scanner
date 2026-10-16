# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Sectionization helpers and analysis-mode selection."""

from __future__ import annotations

import re

from tads.parsing.document import ParsedDocument, Section
from tads.parsing.front_matter import is_toc_line
from tads.schemas.report import AnalysisMode

# Rough heuristic: ~4 characters per token for English/technical prose.
CHARS_PER_TOKEN = 4

# Leave headroom for system prompt / schema framing (separate from completion budget).
DEFAULT_CONTEXT_TOKEN_BUDGET = 100_000
# Conservative per-request overhead shared by context-window planning and
# Luna short/long pricing-tier selection. Heuristic covering system prompt,
# corpus guidance, and native structured-output schema (user-channel framing
# is counted separately in the live chunk envelope estimate). Not a measured
# tokenizer count. Raised to 6000 after prompt 0.9.0 chunk instructions:
# measured system+OpenAI schema ≈4700 tokens with margin. Also above the
# RFC868 smoke gap (≈3,802 beyond a 756-token body). Not the completion budget.
REQUEST_OVERHEAD_TOKENS = 6_000
# Backward-compatible alias (same value as REQUEST_OVERHEAD_TOKENS).
DEFAULT_PROMPT_OVERHEAD_TOKENS = REQUEST_OVERHEAD_TOKENS
# Legacy default when callers omit max_output_tokens; CLI/plan pass the configured value.
DEFAULT_OUTPUT_RESERVE_TOKENS = 16_384

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
    request_overhead_tokens: int = REQUEST_OVERHEAD_TOKENS,
    prompt_overhead_tokens: int | None = None,
    text_override: str | None = None,
) -> AnalysisMode:
    """Prefer whole-document analysis when the doc fits; else section-aware.

    ``available_document_tokens = context_window - max_output_tokens
    - request_overhead_tokens``.

    ``output_reserve_tokens`` is the configured completion budget
    (``max_output_tokens`` / Luna ``max_completion_tokens``).
    ``request_overhead_tokens`` is the shared conservative request overhead
    (same constant used for Luna pricing-tier input estimates).
    ``prompt_overhead_tokens`` is accepted as a deprecated alias for
    ``request_overhead_tokens``.
    """
    overhead = (
        request_overhead_tokens
        if prompt_overhead_tokens is None
        else prompt_overhead_tokens
    )
    available = context_token_budget - output_reserve_tokens - overhead
    if available <= 0:
        return AnalysisMode.SECTION_AWARE
    text = document.text if text_override is None else text_override
    if estimate_tokens(text) <= available:
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
    if is_toc_line(line):
        return False
    # Avoid treating table-of-contents dotted leaders as headings.
    if ".." in line or " . " in line:
        return False
    # Single-number lines that are too long are unlikely headings.
    if len(line) > 120:
        return False
    # Require at least one letter in the title portion.
    title_part = line[len(num) :].lstrip(". ").strip()
    return any(c.isalpha() for c in title_part)
