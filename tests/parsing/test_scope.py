# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""TOC skip and analysis-scope cap tests."""

from tads.corpus.threegpp import ThreeGPPAdapter
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.front_matter import (
    is_acknowledgments_section,
    is_index_section,
    is_toc_line,
)
from tads.parsing.scope import AnalysisScope, apply_analysis_scope
from tads.pipeline import plan_scan

SAMPLE = """\
Contents
1\tScope\t25
2\tReferences\t26
4.2.1\tGeneral\t45

1 Scope

This document defines timers and clocks.

2 References

Normative references.

4 Architecture

4.2 Architecture reference model

4.2.1 General

Clocks may wrap.
"""


def test_is_toc_line():
    assert is_toc_line("1\tScope\t25")
    assert is_toc_line("4.2.1 General ........ 45")
    assert not is_toc_line("1 Scope")
    assert not is_toc_line("4.2.1 General")


def test_toc_folded_into_preamble_and_skipped():
    adapter = ThreeGPPAdapter()
    ref = adapter.resolve("TS 23.501")
    doc = adapter.parse(SAMPLE, ref)
    ids = [s.id for s in doc.sections]
    assert "preamble" in ids
    assert "s-1" in ids
    # TOC entries must not become their own analyzed headings
    assert ids.count("s-1") == 1

    scoped = apply_analysis_scope(doc, AnalysisScope())
    assert scoped.skipped_front_matter_sections >= 1
    assert all(s.id != "preamble" for s in scoped.sections)
    assert any(s.id == "s-1" for s in scoped.sections)


def test_max_sections_and_input_caps():
    adapter = ThreeGPPAdapter()
    ref = adapter.resolve("TS 23.501")
    doc = adapter.parse(SAMPLE, ref)
    scoped = apply_analysis_scope(
        doc,
        AnalysisScope(max_sections=2, max_chars=5000, max_input_tokens=2000),
    )
    assert len(scoped.sections) <= 2


def test_plan_scan_respects_scope_caps():
    plan = plan_scan(
        SAMPLE,
        doc_id="TS 23.501",
        corpus="3gpp",
        provider="mock",
        scope=AnalysisScope(max_sections=1),
    )
    assert plan.section_count == 1
    assert plan.scoped.truncated is True
    assert plan.scoped.document_chars > plan.scoped.analyzed_chars
    assert plan.scoped.eligible_sections >= plan.section_count


def test_index_and_ack_detectors():
    assert is_index_section(Section(id="s-Index", title="Index", text="a", level=1))
    assert is_index_section(
        Section(id="s-99", title="Index of Subjects", text="a", level=1)
    )
    assert not is_index_section(
        Section(id="s-4", title="Indexing algorithm", text="a", level=1)
    )
    assert not is_index_section(
        Section(id="s-2", title="References", text="a", level=1)
    )

    assert is_acknowledgments_section(
        Section(id="s-A", title="Acknowledgements", text="a", level=1)
    )
    assert is_acknowledgments_section(
        Section(id="s-A", title="Acknowledgments", text="a", level=1)
    )
    assert is_acknowledgments_section(
        Section(id="acknowledgements", title="Thanks", text="a", level=1)
    )
    assert not is_acknowledgments_section(
        Section(id="s-2", title="Normative References", text="a", level=1)
    )
    assert not is_acknowledgments_section(
        Section(id="annex-a", title="Annex A", text="a", level=1)
    )


def test_index_and_acknowledgments_skipped_by_default():
    doc = ParsedDocument(
        corpus="ietf",
        doc_id="RFC9999",
        text="body",
        sections=[
            Section(id="s-1", title="Scope", text="Scope body.", level=1),
            Section(id="s-2", title="References", text="Refs body.", level=1),
            Section(
                id="s-A",
                title="Acknowledgements",
                text="Thanks to reviewers.",
                level=1,
            ),
            Section(id="s-Index", title="Index", text="epoch, 12\nrollover, 44\n", level=1),
            Section(id="annex-a", title="Annex A", text="Annex body.", level=1),
        ],
    )
    scoped = apply_analysis_scope(doc, AnalysisScope())
    titles = [s.title for s in scoped.sections]
    assert titles == ["Scope", "References", "Annex A"]
    assert scoped.skipped_index_ack_sections == 2

    kept = apply_analysis_scope(
        doc, AnalysisScope(include_index_and_acknowledgments=True)
    )
    assert [s.title for s in kept.sections] == [
        "Scope",
        "References",
        "Acknowledgements",
        "Index",
        "Annex A",
    ]
    assert kept.skipped_index_ack_sections == 0
