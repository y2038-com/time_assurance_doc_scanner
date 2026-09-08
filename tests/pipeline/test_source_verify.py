# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Source quote verification and offset preservation tests."""

from tads.pipeline.validate import (
    find_quote_spans,
    normalize_for_quote_match,
    verify_finding_source,
)
from tads.schemas.findings import (
    Evidence,
    Finding,
    FindingType,
    Severity,
)
from tads.schemas.taxonomy import Confidence

DOC = """
Network Time Protocol Version 4: Protocol and Algorithms Specification

Era 0 includes dates from the prime epoch to some time in 2036, when the
timestamp field wraps around and the next era begins.
"""


def _finding(*quotes: str) -> Finding:
    return Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Era",
        description="desc",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        machine_interpretation="interp",
        evidence=[Evidence(quote=q) for q in quotes],
    )


def test_normalize_collapses_whitespace_and_case():
    assert normalize_for_quote_match("  Era   0\nIncludes ") == "era 0 includes"


def test_source_verified_when_quote_present():
    finding = verify_finding_source(
        _finding("Era 0 includes dates from the prime epoch"),
        DOC,
    )
    assert finding.source_verified is True
    assert finding.source_verification_detail is not None
    assert "Matched 1 of 1" in finding.source_verification_detail


def test_unique_quote_sets_half_open_offsets():
    quote = "Era 0 includes dates from the prime epoch"
    finding = verify_finding_source(_finding(quote), DOC)
    assert finding.source_verified is True
    assert finding.evidence[0].location is not None
    start = finding.evidence[0].location.start_char
    end = finding.evidence[0].location.end_char
    assert start is not None and end is not None
    assert DOC[start:end].replace("\n", " ").startswith("Era 0 includes")
    assert finding.location is not None
    assert finding.location.start_char == start
    assert finding.location.end_char == end


def test_quote_at_beginning_and_end():
    text = "Alpha beta gamma delta epsilon zeta"
    # Long enough for MIN_QUOTE_CHARS after normalize
    start_quote = "Alpha beta gamma delta"
    end_quote = "gamma delta epsilon zeta"
    f1 = verify_finding_source(_finding(start_quote), text)
    assert f1.evidence[0].location is not None
    assert f1.evidence[0].location.start_char == 0
    assert text.startswith(text[f1.evidence[0].location.start_char : f1.evidence[0].location.end_char])

    f2 = verify_finding_source(_finding(end_quote), text)
    assert f2.evidence[0].location is not None
    assert f2.evidence[0].location.end_char == len(text)


def test_repeated_quote_leaves_offsets_unset():
    text = (
        "Era 0 includes dates from the prime epoch. "
        "Later again: Era 0 includes dates from the prime epoch."
    )
    quote = "Era 0 includes dates from the prime epoch"
    assert len(find_quote_spans(text, quote)) == 2
    finding = verify_finding_source(_finding(quote), text)
    assert finding.source_verified is True
    assert "multiple locations" in (finding.source_verification_detail or "")
    assert finding.evidence[0].location is None or (
        finding.evidence[0].location.start_char is None
    )
    assert finding.location is None or finding.location.start_char is None


def test_failed_verification_does_not_fabricate_offsets():
    finding = verify_finding_source(
        _finding("This quote does not appear anywhere in the document."),
        DOC,
    )
    assert finding.source_verified is False
    assert finding.evidence[0].location is None
    assert finding.location is None


def test_source_not_verified_when_quote_absent():
    finding = verify_finding_source(
        _finding("This quote does not appear anywhere in the document."),
        DOC,
    )
    assert finding.source_verified is False
    assert "Matched 0 of 1" in (finding.source_verification_detail or "")


def test_short_quotes_are_skipped():
    finding = verify_finding_source(_finding("era 0"), DOC)
    assert finding.source_verified is False
    assert "shorter than" in (finding.source_verification_detail or "")


def test_no_evidence_quotes():
    finding = verify_finding_source(_finding(), DOC)
    assert finding.source_verified is False
    assert "No evidence quotes" in (finding.source_verification_detail or "")
