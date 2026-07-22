"""Prompt framework for time-assurance analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tads.parsing.document import ParsedDocument, Section

PROMPT_FRAMEWORK_VERSION = "0.1.0"

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
- Human review is authoritative; your findings are advisory.
- Recommendations must be Level 1 only: remediation direction, not rewritten normative text.
- Distinguish machine interpretation from anything that would need deterministic verification.
"""


FINDING_JSON_INSTRUCTIONS = """Return a JSON object with key "findings" (array). Each finding must include:
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
If there are no findings, return {"findings": []}.
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
) -> PromptBundle:
    header = _document_header(document, corpus_notes)
    user = (
        f"{header}\n\n"
        f"Analyze the ENTIRE document below for time-assurance issues.\n\n"
        f"{FINDING_JSON_INSTRUCTIONS}\n\n"
        f"----- BEGIN DOCUMENT -----\n{document.text}\n----- END DOCUMENT -----\n"
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
        for key, value in corpus_notes.items():
            lines.append(f"{key}: {value}")
    return "\n".join(lines)
