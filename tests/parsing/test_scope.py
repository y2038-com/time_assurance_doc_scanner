# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""TOC skip and analysis-scope cap tests."""

from tads.corpus.threegpp import ThreeGPPAdapter
from tads.parsing.front_matter import is_toc_line
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
