# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Prompt framework for time-assurance analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tads.parsing.document import ParsedDocument, Section

PROMPT_FRAMEWORK_VERSION = "0.3.0"

SYSTEM_PROMPT = """You are a specialist reviewer of technical standards and protocol documentation \
with expertise in long-horizon time assurance (Y2036 NTP era, Y2038 32-bit signed time, \
Y2100 leap-year/RTC issues, Y2106 unsigned 32-bit time, epochs, leap seconds, UTC/TAI/GPS, \
monotonic clocks, serialization, certificates, and related topics).

Your job is to identify explicit defects, internal inconsistencies, missing documentation, \
implied assumptions, time assurance gaps, and lifetime-versus-representation mismatches.

Rules:
- Be evidence-based. Quote the document.
- Do not rely on keyword presence alone; reason about behavior and representations.
- Prefer precision over volume. Mark confidence honestly.
- Human review is authoritative; your outputs are candidates for review (advisory), not validated findings.
- Recommendations must be Level 1 only: remediation direction, not rewritten normative text.
- Distinguish machine interpretation from anything that would need deterministic verification.
- When a candidate involves a fixed-width time counter, supply structured time_representation \
parameters taken only from the document. Use null for any parameter the document does not establish. \
Do not guess widths, signedness, epochs, units, tick rates, or horizons.
- Output MUST be a single valid JSON object only. No markdown fences, no preamble, no commentary.
"""


FINDING_JSON_INSTRUCTIONS = """Return ONLY a JSON object (no markdown) with key "findings" (array).
Each finding must include:
- finding_type: one of explicit_defect, internal_inconsistency, missing_documentation,
  implied_assumption, time_assurance_gap, lifetime_representation_mismatch
- title, description
- severity: critical|high|medium|low|info
- confidence: high|medium|low
- domains: array of domain tags (e.g. y2038, rollover, representation)
- section_id / section_title when known
- evidence: array of {quote, note}
- machine_interpretation
- recommendation_level1 (short remediation direction) or null
- time_representation: object or null. When the finding concerns a numeric time/counter
  representation, include this object with ONLY values established by the document:
  - width_bits: integer bit width or null
  - signed: true|false or null (two's-complement vs unsigned)
  - epoch: ISO-8601 datetime (prefer UTC, e.g. 1970-01-01T00:00:00Z) or null
  - unit: one of seconds|milliseconds|microseconds|nanoseconds|days|weeks|ticks or null
  - ticks_per_second: number or null (required only when unit is ticks)
  - claimed_horizon: ISO date or datetime the document (or your description) states, or null
  - rollover_behavior: short string from the document (e.g. wrap, saturate) or null
  Use null for every field the document does not clearly establish. Do not invent values.
  If the finding is not about a fixed-width/epoch counter, set time_representation to null.
Escape quotes inside strings. Keep evidence quotes short.
If there are no findings, return {"findings": []}.
"""

JSON_REPAIR_INSTRUCTIONS = """The previous response was not valid JSON for the scanner schema.
Rewrite it as ONLY a valid JSON object with key "findings" (array), using the same findings.
Preserve time_representation objects when present (including null fields).
No markdown fences, no commentary. Fix trailing commas, unescaped quotes, and truncation.
"""

@dataclass
class PromptBundle:
    system: str
    user: str
    framework_version: str = PROMPT_FRAMEWORK_VERSION


def build_whole_document_prompt(
    document: ParsedDocument,
    *,
    corpus_notes: Optional[dict[str, str]] = None,
    text_override: Optional[str] = None,
) -> PromptBundle:
    header = _document_header(document, corpus_notes)
    body = document.text if text_override is None else text_override
    user = (
        f"{header}\n\n"
        f"Analyze the ENTIRE document below for time-assurance issues.\n\n"
        f"{FINDING_JSON_INSTRUCTIONS}\n\n"
        f"----- BEGIN DOCUMENT -----\n{body}\n----- END DOCUMENT -----\n"
    )
    return PromptBundle(system=SYSTEM_PROMPT, user=user)


def build_section_prompt(
    document: ParsedDocument,
    section: Section,
    *,
    corpus_notes: Optional[dict[str, str]] = None,
    document_summary: Optional[str] = None,
) -> PromptBundle:
    header = _document_header(document, corpus_notes)
    summary = document_summary or "(no summary provided)"
    user = (
        f"{header}\n\n"
        f"Document summary / context:\n{summary}\n\n"
        f"Analyze this SECTION for time-assurance issues. "
        f"Every section is examined; do not skip because keywords are absent.\n\n"
        f"Section id: {section.id}\n"
        f"Section title: {section.title}\n\n"
        f"{FINDING_JSON_INSTRUCTIONS}\n\n"
        f"----- BEGIN SECTION -----\n{section.text}\n----- END SECTION -----\n"
    )
    return PromptBundle(system=SYSTEM_PROMPT, user=user)


def build_json_repair_prompt(broken_response: str) -> PromptBundle:
    """Ask the model to rewrite a broken payload as valid findings JSON."""
    # Keep repair prompts bounded so we don't re-send huge broken outputs.
    excerpt = broken_response
    if len(excerpt) > 60_000:
        excerpt = excerpt[:60_000] + "\n...[truncated]..."
    user = (
        f"{JSON_REPAIR_INSTRUCTIONS}\n\n"
        f"----- BEGIN PREVIOUS RESPONSE -----\n{excerpt}\n"
        f"----- END PREVIOUS RESPONSE -----\n"
    )
    return PromptBundle(system=SYSTEM_PROMPT, user=user)


def _document_header(
    document: ParsedDocument,
    corpus_notes: Optional[dict[str, str]],
) -> str:
    lines = [
        f"Corpus: {document.corpus}",
        f"Document ID: {document.doc_id}",
        f"Title: {document.title or '(unknown)'}",
    ]
    if corpus_notes:
        # Prefer the fields that shape interpretation for this SDO.
        preferred = [
            "display_name",
            "tier",
            "structure",
            "clause_organization",
            "normative_language",
            "references",
            "versioning",
            "editorial_style",
        ]
        for key in preferred:
            if key in corpus_notes:
                lines.append(f"{key}: {corpus_notes[key]}")
        for key, value in corpus_notes.items():
            if key not in preferred and key not in {
                "corpus_id",
                "supports_remote_fetch",
                "fetch",
                "portal",
                "status",
            }:
                lines.append(f"{key}: {value}")
    return "\n".join(lines)
