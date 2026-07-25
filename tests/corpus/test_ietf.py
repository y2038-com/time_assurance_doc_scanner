# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""IETF adapter and sectionization tests."""

from tads.corpus.ietf import IETFAdapter
from tads.parsing.sections import choose_analysis_mode, estimate_tokens
from tads.schemas.report import AnalysisMode

SAMPLE = """\
Network Working Group
Request for Comments: 9999

Sample Time Protocol

Abstract

This document describes a sample protocol using 32-bit timestamps.

1. Introduction

This document describes a sample protocol using 32-bit timestamps.

2. Timestamp Format

Timestamps are unsigned 32-bit seconds since an epoch.

2.1. Epoch

The epoch is 1 January 1900.

3. Security Considerations

None.
"""


def test_normalize_and_resolve_rfc():
    adapter = IETFAdapter()
    assert adapter.normalize_id("rfc5905") == "RFC5905"
    ref = adapter.resolve("5905")
    assert ref.doc_id == "RFC5905"
    assert "rfc5905.txt" in (ref.source_uri or "")


def test_parse_sections():
    adapter = IETFAdapter()
    ref = adapter.resolve("RFC9999")
    doc = adapter.parse(SAMPLE, ref)
    assert doc.title == "Sample Time Protocol"
    ids = [s.id for s in doc.sections]
    assert "s-1" in ids
    assert "s-2" in ids
    assert "s-2.1" in ids


def test_analysis_mode_selection():
    adapter = IETFAdapter()
    ref = adapter.resolve("RFC9999")
    doc = adapter.parse(SAMPLE, ref)
    assert choose_analysis_mode(doc, context_token_budget=100_000) == AnalysisMode.WHOLE_DOCUMENT
    assert choose_analysis_mode(doc, context_token_budget=50) == AnalysisMode.SECTION_AWARE
    assert estimate_tokens("abcd") == 1
