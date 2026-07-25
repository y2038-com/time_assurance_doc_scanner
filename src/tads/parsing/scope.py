# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Limit which document content enters analysis (dev/cost controls)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tads.parsing.document import ParsedDocument, Section
from tads.parsing.front_matter import (
    is_front_matter_section,
    section_looks_like_toc_blob,
)
from tads.parsing.sections import estimate_tokens


@dataclass
class AnalysisScope:
    """Caps and filters applied after parsing, before plan/scan costing."""

    include_front_matter: bool = False
    max_sections: Optional[int] = None
    max_chars: Optional[int] = None
    max_input_tokens: Optional[int] = None


@dataclass
class ScopedDocument:
    """Document view actually used for analysis estimates and LLM calls."""

    sections: list[Section]
    text: str
    notes: list[str] = field(default_factory=list)
    total_sections_before: int = 0
    skipped_front_matter_sections: int = 0
    truncated: bool = False
    # Totals for proportion reporting
    document_chars: int = 0
    document_tokens: int = 0
    eligible_sections: int = 0  # after front-matter skip, before caps
    eligible_chars: int = 0
    eligible_tokens: int = 0
    analyzed_chars: int = 0
    analyzed_tokens: int = 0


def apply_analysis_scope(
    document: ParsedDocument,
    scope: Optional[AnalysisScope] = None,
) -> ScopedDocument:
    """
    Filter front matter/TOC and apply section/char/input-token caps.

    Front matter is excluded by default. Caps apply to the remaining body
    sections in document order.
    """
    scope = scope or AnalysisScope()
    notes: list[str] = []
    all_sections = list(document.sections)
    total_before = len(all_sections)
    document_chars = len(document.text)
    document_tokens = estimate_tokens(document.text)

    kept: list[Section] = []
    skipped_fm = 0
    for section in all_sections:
        if not scope.include_front_matter and (
            is_front_matter_section(section) or section_looks_like_toc_blob(section)
        ):
            skipped_fm += 1
            continue
        kept.append(section)

    if skipped_fm:
        notes.append(
            f"Skipped {skipped_fm} front-matter/TOC section(s) "
            f"(use --include-front-matter to keep)."
        )
    elif not scope.include_front_matter and total_before:
        notes.append("No separate front-matter/TOC sections detected to skip.")

    eligible_sections = len(kept)
    eligible_chars = sum(len(s.text) for s in kept)
    eligible_tokens = sum(estimate_tokens(s.text) for s in kept)

    truncated = False
    if scope.max_sections is not None and len(kept) > scope.max_sections:
        kept = kept[: scope.max_sections]
        truncated = True
        notes.append(f"Capped to max_sections={scope.max_sections}.")

    # Apply char / input-token caps by taking whole sections until the budget fills.
    char_budget = scope.max_chars
    token_budget = scope.max_input_tokens
    if char_budget is not None or token_budget is not None:
        limited: list[Section] = []
        used_chars = 0
        used_tokens = 0
        for section in kept:
            sec_chars = len(section.text)
            sec_tokens = estimate_tokens(section.text)
            if limited and (
                (char_budget is not None and used_chars + sec_chars > char_budget)
                or (
                    token_budget is not None
                    and used_tokens + sec_tokens > token_budget
                )
            ):
                truncated = True
                break
            if not limited:
                # Always take at least the first section, but trim if needed.
                text = section.text
                if char_budget is not None and len(text) > char_budget:
                    text = text[:char_budget]
                    truncated = True
                    notes.append(f"Trimmed first section to max_chars={char_budget}.")
                if token_budget is not None and estimate_tokens(text) > token_budget:
                    # Approximate trim by character budget from token cap.
                    approx_chars = max(1, token_budget * 4)
                    text = text[:approx_chars]
                    truncated = True
                    notes.append(
                        f"Trimmed first section to max_input_tokens={token_budget}."
                    )
                if text != section.text:
                    section = Section(
                        id=section.id,
                        title=section.title,
                        text=text,
                        level=section.level,
                        start_char=section.start_char,
                        end_char=section.start_char + len(text),
                        parent_id=section.parent_id,
                    )
            limited.append(section)
            used_chars += len(section.text)
            used_tokens += estimate_tokens(section.text)
        if truncated and "Capped to max_sections" not in " ".join(notes):
            if char_budget is not None:
                notes.append(f"Stopped at max_chars={char_budget} (used {used_chars}).")
            if token_budget is not None:
                notes.append(
                    f"Stopped at max_input_tokens={token_budget} (used ~{used_tokens})."
                )
        kept = limited

    def _bundle(
        sections: list[Section],
        text: str,
        *,
        analyzed_chars: int,
        analyzed_tokens: int,
    ) -> ScopedDocument:
        return ScopedDocument(
            sections=sections,
            text=text,
            notes=notes,
            total_sections_before=total_before,
            skipped_front_matter_sections=skipped_fm,
            truncated=truncated,
            document_chars=document_chars,
            document_tokens=document_tokens,
            eligible_sections=eligible_sections,
            eligible_chars=eligible_chars,
            eligible_tokens=eligible_tokens,
            analyzed_chars=analyzed_chars,
            analyzed_tokens=analyzed_tokens,
        )

    if not kept:
        notes.append("Analysis scope is empty after filters/caps.")
        stub = Section(
            id="empty-scope",
            title="Empty analysis scope",
            text="",
            level=1,
        )
        return _bundle([stub], "", analyzed_chars=0, analyzed_tokens=0)

    text = "\n\n".join(section.text for section in kept)
    return _bundle(
        kept,
        text,
        analyzed_chars=len(text),
        analyzed_tokens=estimate_tokens(text),
    )
