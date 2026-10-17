# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic multi-section chunk packing and opaque section references."""

from __future__ import annotations

from dataclasses import dataclass

from tads.errors import PublicScanError
from tads.parsing.document import Section
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS, estimate_tokens

DEFAULT_MAX_SECTIONS_PER_REQUEST = 8
PLANNED_OUTPUT_TOKENS_PER_SECTION = 1500

# Opaque references: S + zero-padded 1-based index (safe charset [A-Z0-9]).
_OPAQUE_REF_PREFIX = "S"


class SectionChunkingError(PublicScanError, RuntimeError):
    """Planning refused: section chunking constraints cannot be satisfied."""


class OversizedSectionError(SectionChunkingError):
    """A single analyzed section cannot fit in one model request."""


class DuplicateSectionIdError(SectionChunkingError):
    """Analyzed sections contain duplicate canonical section IDs."""


@dataclass(frozen=True)
class SectionBinding:
    """Trusted mapping from an opaque wire reference to a canonical section."""

    opaque_ref: str
    section: Section
    canonical_id: str
    canonical_title: str


@dataclass(frozen=True)
class SectionChunk:
    """One primary analysis request covering one or more adjacent sections."""

    chunk_index: int
    bindings: tuple[SectionBinding, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int

    @property
    def sections(self) -> tuple[Section, ...]:
        return tuple(b.section for b in self.bindings)

    @property
    def section_count(self) -> int:
        return len(self.bindings)

    def allowlist(self) -> dict[str, SectionBinding]:
        return {b.opaque_ref: b for b in self.bindings}


def opaque_section_ref(index: int) -> str:
    """Return the deterministic opaque reference for 1-based analyzed order."""
    if index < 1:
        raise ValueError("opaque section index must be >= 1")
    return f"{_OPAQUE_REF_PREFIX}{index:04d}"


def planned_output_tokens(section_count: int, max_output_tokens: int) -> int:
    """Estimate completion tokens for a chunk of ``section_count`` sections."""
    if section_count <= 0:
        return 0
    return min(int(max_output_tokens), PLANNED_OUTPUT_TOKENS_PER_SECTION * section_count)


def render_section_chunk_body(
    opaque_refs: list[str] | tuple[str, ...],
    sections: list[Section] | tuple[Section, ...],
) -> str:
    """Serialize section records for the untrusted chunk envelope body."""
    if len(opaque_refs) != len(sections):
        raise ValueError("opaque_refs and sections must have the same length")
    parts: list[str] = []
    for ref, section in zip(opaque_refs, sections, strict=True):
        parts.append(f"--- SECTION ref={ref} ---")
        parts.append(f"section_title: {section.title}")
        parts.append("text:")
        parts.append(section.text)
        parts.append(f"--- END SECTION ref={ref} ---")
    return "\n".join(parts)


def render_section_chunk_user_message(
    *,
    document_id: str,
    document_title: str,
    section_summary: str,
    opaque_refs: list[str] | tuple[str, ...],
    sections: list[Section] | tuple[Section, ...],
) -> str:
    """
    Canonical user-channel serialization for a section chunk.

    Shared by prompt construction and planning token estimates so framing
    cannot drift from a parallel stub.
    """
    body = render_section_chunk_body(opaque_refs, sections)
    count = len(body)
    lines = [
        f"UNTRUSTED_DATA kind=section_chunk chars={count}",
        f"document_id: {document_id}",
        f"title: {document_title}",
        f"section_summary: {section_summary}",
        f"section_count: {len(sections)}",
        "text:",
        body,
        f"UNTRUSTED_DATA_END chars={count}",
    ]
    return "\n".join(lines)


def estimate_chunk_input_tokens(
    bindings: list[SectionBinding] | tuple[SectionBinding, ...],
    *,
    document_id: str,
    document_title: str,
    section_summary: str,
) -> int:
    """Full per-request input estimate: live user envelope + shared overhead."""
    if not bindings:
        return REQUEST_OVERHEAD_TOKENS
    user = render_section_chunk_user_message(
        document_id=document_id,
        document_title=document_title,
        section_summary=section_summary,
        opaque_refs=[b.opaque_ref for b in bindings],
        sections=[b.section for b in bindings],
    )
    return estimate_tokens(user) + REQUEST_OVERHEAD_TOKENS


def _assert_unique_canonical_ids(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for index, section in enumerate(sections):
        prior = seen.get(section.id)
        if prior is not None:
            raise DuplicateSectionIdError(
                "Cannot plan section-aware analysis: duplicate canonical "
                f"section_id {section.id!r} in the analyzed set "
                f"(first at index {prior}, again at index {index})."
            )
        seen[section.id] = index


def _bindings_for(
    sections: list[Section],
    *,
    start_index: int,
) -> list[SectionBinding]:
    """Build bindings with opaque refs continuing global analyzed order."""
    out: list[SectionBinding] = []
    for offset, section in enumerate(sections):
        ref = opaque_section_ref(start_index + offset)
        out.append(
            SectionBinding(
                opaque_ref=ref,
                section=section,
                canonical_id=section.id,
                canonical_title=section.title,
            )
        )
    return out


def pack_section_chunks(
    sections: list[Section],
    *,
    context_window: int,
    max_output_tokens: int,
    max_sections_per_request: int = DEFAULT_MAX_SECTIONS_PER_REQUEST,
    document_id: str = "",
    document_title: str = "",
    section_summary: str = "",
    section_chunking: bool = True,
) -> list[SectionChunk]:
    """
    Pack analyzed sections into deterministic adjacent chunks.

    Opaque references use stable analyzed-section order (S0001, S0002, ...).
    When ``section_chunking`` is False, each section is its own chunk.
    """
    if max_sections_per_request < 1:
        raise SectionChunkingError("max_sections_per_request must be >= 1")
    if not sections:
        return []
    _assert_unique_canonical_ids(sections)

    if not section_chunking:
        chunks: list[SectionChunk] = []
        for index, section in enumerate(sections):
            bindings = _bindings_for([section], start_index=index + 1)
            est_in = estimate_chunk_input_tokens(
                bindings,
                document_id=document_id,
                document_title=document_title,
                section_summary=section_summary,
            )
            if est_in + max_output_tokens > context_window:
                raise OversizedSectionError(
                    "Cannot plan section-aware analysis: analyzed section "
                    f"{section.id!r} does not fit alone in the model context "
                    f"window ({context_window} tokens) after reserving "
                    f"{max_output_tokens} completion tokens and "
                    f"{REQUEST_OVERHEAD_TOKENS} request-overhead tokens "
                    f"(estimated input {est_in})."
                )
            chunks.append(
                SectionChunk(
                    chunk_index=index,
                    bindings=tuple(bindings),
                    estimated_input_tokens=est_in,
                    estimated_output_tokens=planned_output_tokens(
                        1, max_output_tokens
                    ),
                )
            )
        return chunks

    chunks = []
    current: list[SectionBinding] = []
    global_index = 1

    def _seal(bindings: list[SectionBinding]) -> SectionChunk:
        est_in = estimate_chunk_input_tokens(
            bindings,
            document_id=document_id,
            document_title=document_title,
            section_summary=section_summary,
        )
        return SectionChunk(
            chunk_index=len(chunks),
            bindings=tuple(bindings),
            estimated_input_tokens=est_in,
            estimated_output_tokens=planned_output_tokens(
                len(bindings), max_output_tokens
            ),
        )

    for section in sections:
        candidate_binding = _bindings_for([section], start_index=global_index)[0]
        trial = current + [candidate_binding]
        est_in = estimate_chunk_input_tokens(
            trial,
            document_id=document_id,
            document_title=document_title,
            section_summary=section_summary,
        )
        exceeds_context = est_in + max_output_tokens > context_window
        exceeds_cap = len(trial) > max_sections_per_request

        if current and (exceeds_context or exceeds_cap):
            chunks.append(_seal(current))
            current = []
            trial = [candidate_binding]
            est_in = estimate_chunk_input_tokens(
                trial,
                document_id=document_id,
                document_title=document_title,
                section_summary=section_summary,
            )
            if est_in + max_output_tokens > context_window:
                raise OversizedSectionError(
                    "Cannot plan section-aware analysis: analyzed section "
                    f"{section.id!r} does not fit alone in the model context "
                    f"window ({context_window} tokens) after reserving "
                    f"{max_output_tokens} completion tokens and "
                    f"{REQUEST_OVERHEAD_TOKENS} request-overhead tokens "
                    f"(estimated input {est_in})."
                )

        if not current and est_in + max_output_tokens > context_window:
            raise OversizedSectionError(
                "Cannot plan section-aware analysis: analyzed section "
                f"{section.id!r} does not fit alone in the model context "
                f"window ({context_window} tokens) after reserving "
                f"{max_output_tokens} completion tokens and "
                f"{REQUEST_OVERHEAD_TOKENS} request-overhead tokens "
                f"(estimated input {est_in})."
            )

        current = trial
        global_index += 1

    if current:
        chunks.append(_seal(current))
    return chunks
