# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic section-ID uniquification and W3C TOC correction."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from tads.corpus.base import CorpusDocumentRef
from tads.corpus.registry import get_adapter
from tads.parsing.chunks import (
    DuplicateSectionIdError,
    pack_section_chunks,
)
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.scope import AnalysisScope, apply_analysis_scope
from tads.parsing.section_ids import uniquify_section_ids
from tads.parsing.chunks import SectionBinding
from tads.pipeline import plan_scan
from tads.pipeline.parse_findings import parse_findings_payload
from tads.schemas.report import AnalysisMode

INPUTS = Path(__file__).resolve().parents[2] / "inputs"
HR_TIME = INPUTS / "hr-time-3.txt"

KNOWN_CORPUS = {
    "RFC5905.txt": "ietf",
    "rfc5905.txt": "ietf",
    "RFC868.txt": "ietf",
    "hr-time-3.txt": "w3c",
    "ECMA-262.txt": "ecma",
    "ECMA-404.txt": "ecma",
    "EN_300_468.txt": "etsi",
    "TS_103_221-2.txt": "etsi",
    "TS_23.501.txt": "3gpp",
    "TS_29.061.txt": "3gpp",
    "TS_29.274.txt": "3gpp",
    "OpenFormula.txt": "oasis",
    "SP-800-57pt1r5.txt": "nist",
    "X.680.txt": "itu",
    "IEEE_1588-2019.txt": "ieee",
}


def _sec(sid: str, title: str, text: str, *, start: int = 0) -> Section:
    return Section(
        id=sid,
        title=title,
        text=text,
        level=1,
        start_char=start,
        end_char=start + len(text),
    )


def test_uniquify_preserves_first_and_suffixes_later():
    sections = [
        _sec("s-1", "One", "a"),
        _sec("s-1", "One", "b"),
        _sec("s-1", "Other", "c"),
    ]
    out = uniquify_section_ids(sections)
    assert [s.id for s in out] == ["s-1", "s-1--2", "s-1--3"]
    assert out[0].title == "One" and out[0].text == "a"
    assert out[1].title == "One" and out[1].text == "b"
    assert out[2].title == "Other" and out[2].text == "c"


def test_uniquify_same_id_different_titles():
    out = uniquify_section_ids(
        [_sec("s-2", "Alpha", "a"), _sec("s-2", "Beta", "b")]
    )
    assert [s.id for s in out] == ["s-2", "s-2--2"]


def test_uniquify_skips_reserved_preexisting_suffix():
    # Originals: s-1, s-1, s-1--2, s-1 → s-1, s-1--3, s-1--2, s-1--4
    out = uniquify_section_ids(
        [
            _sec("s-1", "A", "a"),
            _sec("s-1", "B", "b"),
            _sec("s-1--2", "C", "c"),
            _sec("s-1", "D", "d"),
        ]
    )
    assert [s.id for s in out] == ["s-1", "s-1--3", "s-1--2", "s-1--4"]


def test_uniquify_leaves_unique_ids_unchanged():
    sections = [_sec("s-1", "A", "a"), _sec("s-2", "B", "b"), _sec("preamble", "P", "p")]
    out = uniquify_section_ids(sections)
    assert [s.id for s in out] == ["s-1", "s-2", "preamble"]
    assert out[0] is sections[0]
    assert out[1] is sections[1]


def test_uniquify_repeated_parse_stable():
    sections = [_sec("x", "T", "a"), _sec("x", "T", "b"), _sec("x", "U", "c")]
    a = [s.id for s in uniquify_section_ids(sections)]
    b = [s.id for s in uniquify_section_ids(sections)]
    assert a == b == ["x", "x--2", "x--3"]


def test_parsed_document_establishes_unique_ids():
    doc = ParsedDocument(
        corpus="generic",
        doc_id="D",
        text="ab",
        sections=[_sec("s-1", "A", "a", start=0), _sec("s-1", "B", "b", start=1)],
    )
    assert [s.id for s in doc.sections] == ["s-1", "s-1--2"]


def test_hr_time_toc_stubs_not_analyzed_and_body_s1_kept():
    text = HR_TIME.read_text(encoding="utf-8")
    doc = get_adapter("w3c").parse(
        text, CorpusDocumentRef(corpus="w3c", doc_id="hr-time-3")
    )
    scoped = apply_analysis_scope(doc, AnalysisScope())
    ids = [s.id for s in doc.sections]
    assert len(ids) == len(set(ids))
    # Leader-less TOC stubs removed; body Introduction remains as s-1.
    s1 = next(s for s in doc.sections if s.id == "s-1")
    assert s1.title == "1 Introduction"
    assert s1.text.lstrip().startswith("1. Introduction")
    assert "This section is non-normative" in s1.text
    assert s1.start_char == text.index("1. Introduction")
    assert len(s1.text) > 100
    # No second Introduction with a suffix from TOC.
    assert not any(s.id.startswith("s-1--") for s in doc.sections)
    assert scoped.eligible_sections == len(scoped.sections)
    assert scoped.skipped_front_matter_sections >= 1
    # TOC marker content belongs to skipped preamble, not analyzed stubs.
    assert any(s.id == "preamble" for s in doc.sections)
    preamble = next(s for s in doc.sections if s.id == "preamble")
    assert "Table of Contents" in preamble.text
    assert "1 Introduction" in preamble.text  # undotted TOC line
    assert scoped.sections[0].id == "s-1"


def test_hr_time_chunk_planning_succeeds():
    plan = plan_scan(
        HR_TIME.read_text(encoding="utf-8"),
        doc_id="hr-time-3",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert plan.chunks
    assert sum(plan.chunk_section_counts) == len(plan.scoped.sections)
    assert len({s.id for s in plan.scoped.sections}) == len(plan.scoped.sections)


def test_max_sections_selects_correct_normalized_instance():
    # Synthetic undotted duplicates (no TOC marker) → uniquified.
    text = "1 Alpha\n\nBody A about timestamps.\n\n1 Beta\n\nBody B about epochs.\n"
    doc = get_adapter("etsi").parse(
        text, CorpusDocumentRef(corpus="etsi", doc_id="dup")
    )
    assert [s.id for s in doc.sections if s.id.startswith("s-1")][:2]
    scoped = apply_analysis_scope(doc, AnalysisScope(max_sections=1))
    assert len(scoped.sections) == 1
    # First body instance kept under original id.
    assert scoped.sections[0].id == "s-1"
    assert "Body A" in scoped.sections[0].text


def test_opaque_refs_map_to_normalized_public_ids():
    text = "1 Alpha\n\nAAA timestamps are 32-bit.\n\n1 Beta\n\nBBB epochs wrap.\n"
    plan = plan_scan(
        text,
        doc_id="DUP1",
        corpus="etsi",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        section_chunking=False,
    )
    assert len(plan.chunks) >= 2
    bindings = []
    for chunk in plan.chunks:
        bindings.extend(chunk.bindings)
    by_ref = {b.opaque_ref: b for b in bindings}
    assert by_ref["S0001"].canonical_id == "s-1"
    assert by_ref["S0002"].canonical_id == "s-1--2"
    assert by_ref["S0001"].canonical_id != by_ref["S0002"].canonical_id


def test_evidence_on_later_duplicate_uses_that_instance_only():
    text_a = "AAA timestamps are 32-bit seconds here."
    text_b = "BBB timestamps are 32-bit seconds here."
    # Build uniquified document with authoritative offsets.
    full = f"1 Alpha\n\n{text_a}\n\n1 Beta\n\n{text_b}\n"
    doc = get_adapter("etsi").parse(
        full, CorpusDocumentRef(corpus="etsi", doc_id="dup")
    )
    later = next(s for s in doc.sections if s.id == "s-1--2")
    bindings = {
        "S0002": SectionBinding(
            opaque_ref="S0002",
            section=later,
            canonical_id=later.id,
            canonical_title=later.title,
        )
    }
    quote = "timestamps are 32-bit seconds here"
    findings = parse_findings_payload(
        {
            "findings": [
                {
                    "finding_type": "time_assurance_gap",
                    "title": "Gap",
                    "description": "A time assurance gap.",
                    "severity": "medium",
                    "confidence": "medium",
                    "domains": ["y2036"],
                    "section_id": "S0002",
                    "section_title": None,
                    "evidence": [{"quote": quote, "note": None}],
                    "machine_interpretation": "Width gap.",
                    "recommendation_level1": None,
                    "scope_relevance": "core",
                    "scope_rationale": (
                        "This matters to time assurance because width affects rollover."
                    ),
                    "time_representation": None,
                }
            ]
        },
        section_bindings=bindings,
        document_text=doc.text,
    )
    assert findings[0].location.section_id == "s-1--2"
    ev = findings[0].evidence[0]
    assert ev.location is not None
    start, end = ev.location.start_char, ev.location.end_char
    assert doc.text[start:end] == quote
    assert start >= later.start_char
    # Must not point at the earlier duplicate's match.
    assert start == doc.text.find(quote, later.start_char)


def test_empty_evidence_later_duplicate_unambiguous():
    later = _sec("s-1--2", "1 Beta", "body", start=10)
    bindings = {
        "S0002": SectionBinding(
            opaque_ref="S0002",
            section=later,
            canonical_id="s-1--2",
            canonical_title="1 Beta",
        )
    }
    findings = parse_findings_payload(
        {
            "findings": [
                {
                    "finding_type": "time_assurance_gap",
                    "title": "Gap",
                    "description": "A time assurance gap.",
                    "severity": "medium",
                    "confidence": "medium",
                    "domains": ["y2036"],
                    "section_id": "S0002",
                    "section_title": None,
                    "evidence": [],
                    "machine_interpretation": "Width gap.",
                    "recommendation_level1": None,
                    "scope_relevance": "core",
                    "scope_rationale": (
                        "This matters to time assurance because width affects rollover."
                    ),
                    "time_representation": None,
                }
            ]
        },
        section_bindings=bindings,
    )
    assert findings[0].location.section_id == "s-1--2"
    assert findings[0].evidence == []


def test_planner_still_rejects_duplicate_canonical_ids():
    # Bypass ParsedDocument normalization: feed raw duplicate Sections to packer.
    sections = [
        _sec("s-1", "A", "aaaa timestamps are 32-bit seconds."),
        _sec("s-1", "B", "bbbb epochs wrap around."),
    ]
    with pytest.raises(DuplicateSectionIdError, match="duplicate canonical"):
        pack_section_chunks(
            sections,
            context_window=100_000,
            max_output_tokens=16_384,
        )


def test_reference_corpus_final_parsed_ids_unique():
    failures = []
    for path in sorted(INPUTS.glob("*.txt")):
        if path.name == "README.md":
            continue
        corpus = KNOWN_CORPUS.get(path.name)
        if corpus is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        doc = get_adapter(corpus).parse(
            text, CorpusDocumentRef(corpus=corpus, doc_id=path.stem)
        )
        counts = Counter(s.id for s in doc.sections)
        dups = {k: v for k, v in counts.items() if v > 1}
        if dups:
            failures.append((path.name, corpus, dups))
    assert failures == []
