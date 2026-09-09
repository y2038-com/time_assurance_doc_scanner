# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for clause sectionization (split headings, false positives)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tads.parsing.clauses import (
    assess_clause_parse_health,
    section_plain_text_clauses,
)
from tads.pipeline import UnreliableSectionizationError, plan_scan
from tads.schemas.report import AnalysisMode

FIXTURES = Path(__file__).parent / "fixtures" / "clauses"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_joins_split_clause_number_and_title():
    text = "Preamble.\n\n5.3.10\nTimestamp\nBody of the clause follows.\n"
    secs = section_plain_text_clauses(text)
    by_id = {s.id: s for s in secs}
    assert "s-5.3.10" in by_id
    assert "Timestamp" in by_id["s-5.3.10"].title
    assert "Body of the clause" in by_id["s-5.3.10"].text


def test_joins_split_annex_marker_and_title():
    text = "Intro\n\nAnnex A (normative)\nEncoding rules\nBits go here.\n"
    secs = section_plain_text_clauses(text)
    annex = [s for s in secs if s.id.lower().startswith("annex")]
    assert len(annex) == 1
    assert "Encoding rules" in annex[0].title
    assert "normative" in annex[0].title.lower()


def test_rejects_sentence_like_and_year_false_headings():
    text = """\
Intro

4.1 Epoch
The epoch is defined.

2038 cannot be encoded. There is no standardized solution.

2.3 Unless otherwise specified by the relevant architecture, MIME types apply.

1 presented to TSG for information;
2 presented to TSG for approval;

60 seconds).
"""
    secs = section_plain_text_clauses(text)
    ids = {s.id for s in secs}
    assert "s-4.1" in ids
    assert "s-2038" not in ids
    assert "s-2.3" not in ids
    assert "s-1" not in ids
    assert "s-2" not in ids
    assert "s-60" not in ids


def test_rejects_toc_dotted_split_heading():
    text = (
        "Contents\n\n"
        "5.3.10\n"
        "Timestamp .............................................................. 16\n\n"
        "5.3.10\n"
        "Timestamp\n"
        "If used, this conditional attribute shall contain the time.\n"
    )
    secs = section_plain_text_clauses(text)
    hits = [s for s in secs if s.id == "s-5.3.10"]
    assert len(hits) == 1
    assert "If used" in hits[0].text


def test_rejects_annex_cross_reference_prose():
    text = (
        "Overview\n\n"
        "Clause 38 and Annex B define the types that provide support for ISO 8601.\n\n"
        "Annex B (informative)\n"
        "Additional information\n"
        "Annex body.\n"
    )
    secs = section_plain_text_clauses(text)
    annex_ids = [s.id for s in secs if s.id.lower().startswith("annex")]
    assert annex_ids == ["Annex-B"]
    assert "Additional information" in secs[-1].title


def test_idealized_one_line_headings_still_work():
    text = """\
ETSI sample

1 Scope
This document specifies time behaviour.

4.1 Epoch
The epoch is defined as ...

Annex A (normative): Encoding
Bit layouts go here.
"""
    secs = section_plain_text_clauses(text)
    ids = [s.id for s in secs]
    assert "s-1" in ids
    assert "s-4.1" in ids
    assert any(i.lower().startswith("annex") for i in ids)


def test_etsi_converter_fixture_recognizes_5_3_10_timestamp():
    text = _load("etsi_ts_103_221_2_excerpt.txt")
    secs = section_plain_text_clauses(text)
    by_id = {s.id: s for s in secs}
    assert "s-5.3.10" in by_id
    assert "Timestamp" in by_id["s-5.3.10"].title
    assert "s-2038" not in by_id
    assert "s-60" not in by_id
    real = [s for s in secs if s.id != "preamble"]
    assert 1 <= len(real) <= 12


def test_itu_converter_fixture_split_headings():
    text = _load("itu_t_x680_excerpt.txt")
    secs = section_plain_text_clauses(text)
    by_id = {s.id: s for s in secs}
    assert "s-2.1" in by_id
    assert "Shared Editions" in by_id["s-2.1"].title
    assert "s-2.2" in by_id
    assert "s-3.1" in by_id
    assert not any(s.id.lower().startswith("annex") for s in secs)
    real = [s for s in secs if s.id != "preamble"]
    assert 2 <= len(real) <= 10


def test_en_converter_fixture_split_headings():
    text = _load("etsi_en_300_468_excerpt.txt")
    secs = section_plain_text_clauses(text)
    by_id = {s.id: s for s in secs}
    assert "s-1" in by_id
    assert by_id["s-1"].title.startswith("1 Scope")
    assert "s-2" in by_id
    assert "s-2.1" in by_id
    real = [s for s in secs if s.id != "preamble"]
    assert 2 <= len(real) <= 20


def test_gpp_converter_fixture_and_list_rejection():
    text = _load("gpp_ts_23501_excerpt.txt")
    secs = section_plain_text_clauses(text)
    by_id = {s.id: s for s in secs}
    assert "s-4.1" in by_id
    assert "General concepts" in by_id["s-4.1"].title
    titles = " ".join(s.title.lower() for s in secs)
    assert "presented to board" not in titles
    real = [s for s in secs if s.id != "preamble"]
    assert 1 <= len(real) <= 10


def test_does_not_join_clause_number_to_following_index_number():
    text = "Intro\n\n5.3.10\n\n10\nSource IPv4 Address\nMore text.\n"
    secs = section_plain_text_clauses(text)
    ids = {s.id for s in secs}
    assert "s-5.3.10" not in ids


def test_parse_health_flags_over_segmented_document():
    text = "Doc\n\n" + "\n\n".join(f"{i}.1 Title{i}\nx\n" for i in range(1, 50))
    secs = section_plain_text_clauses(text)
    health = assess_clause_parse_health(secs, document_chars=len(text))
    assert health.section_count >= 40
    assert health.ok is False
    assert health.notes


def test_plan_scan_forced_sections_warns_when_health_fails():
    dense = "Doc\n\n" + "\n\n".join(f"{i}.1 Title{i}\nx\n" for i in range(1, 50))
    plan = plan_scan(
        dense,
        doc_id="ETSI TS 103 246-1",
        corpus="etsi",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        context_token_budget=50_000,
    )
    assert plan.analysis_mode == AnalysisMode.SECTION_AWARE
    assert any(
        "untrustworthy" in n.lower() or "unreliable" in n.lower()
        for n in plan.cost_estimate.notes
    )


def test_plan_scan_unhealthy_large_doc_fails_closed_not_whole_document():
    """SECTION_AWARE chosen for size must not fall back to oversized WHOLE_DOCUMENT."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    chunks = []
    for i in range(20):
        label = letters[i % 26]
        chunks.append(
            f"Annex {label} (informative)\n"
            f"Extra material {i}\n" + ("padding word " * 800) + "\n"
        )
    bulky = "Doc\n\n" + "\n\n".join(chunks)
    with pytest.raises(UnreliableSectionizationError) as exc_info:
        plan_scan(
            bulky,
            doc_id="ETSI TS 103 246-1",
            corpus="etsi",
            provider="mock",
            force_mode=None,
            context_token_budget=8_000,
        )
    msg = str(exc_info.value).lower()
    assert "unreliable" in msg or "sectionization" in msg
    assert "whole-document" in msg or "fit" in msg
    assert "--force-sections" in str(exc_info.value) or "force-sections" in msg


def test_plan_scan_unhealthy_small_doc_uses_whole_document():
    """When whole-document already fits, unhealthy parse stays on whole-document."""
    # Dense tiny clauses → health failure, but text still fits a large window.
    dense = "Doc\n\n" + "\n\n".join(f"{i}.1 Title{i}\nx\n" for i in range(1, 50))
    plan = plan_scan(
        dense,
        doc_id="ETSI TS 103 246-1",
        corpus="etsi",
        provider="mock",
        force_mode=None,
        context_token_budget=100_000,
    )
    assert plan.analysis_mode == AnalysisMode.WHOLE_DOCUMENT
    assert any("Clause parse health" in n for n in plan.cost_estimate.notes)


def test_plan_scan_healthy_large_doc_stays_section_aware():
    # Many normal-sized clauses, enough text to exceed a tight budget.
    body = "Doc\n\n" + "\n\n".join(
        f"{i}.1 Title for clause {i}\n" + ("content word " * 200) + "\n"
        for i in range(1, 30)
    )
    plan = plan_scan(
        body,
        doc_id="ETSI TS 103 246-1",
        corpus="etsi",
        provider="mock",
        force_mode=None,
        context_token_budget=8_000,
    )
    assert plan.analysis_mode == AnalysisMode.SECTION_AWARE
    assert not any("unreliable" in n.lower() for n in plan.cost_estimate.notes)
