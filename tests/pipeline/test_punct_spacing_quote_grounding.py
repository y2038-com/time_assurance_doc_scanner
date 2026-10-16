# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Punctuation-adjacent whitespace equivalence for flexible quote grounding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tads.parsing.chunks import SectionBinding
from tads.parsing.document import Section
from tads.pipeline import plan_scan
from tads.pipeline.parse_findings import FindingParseError, parse_findings_payload
from tads.pipeline.validate import find_quote_spans
from tads.schemas.report import AnalysisMode

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PUNCT_DOC = FIXTURES / "punct_adjacent_spacing_doc.txt"
PUNCT_FINDINGS = FIXTURES / "punct_adjacent_spacing_findings.json"


def _binding(opaque: str, section: Section) -> dict[str, SectionBinding]:
    return {
        opaque: SectionBinding(
            opaque_ref=opaque,
            section=section,
            canonical_id=section.id,
            canonical_title=section.title,
        )
    }


def _item(section_id: str, quote: str, **overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Gap",
        "description": "A time assurance gap.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["y2036"],
        "section_id": section_id,
        "section_title": None,
        "evidence": [{"quote": quote, "note": None}],
        "machine_interpretation": "Counter width gap.",
        "recommendation_level1": None,
        "scope_relevance": "core",
        "scope_rationale": (
            "This matters to time assurance because width affects rollover."
        ),
        "time_representation": None,
    }
    item.update(overrides)
    return item


def test_agent_comma_matches_space_before_comma():
    source = "The monotonic clock exists in the user agent , so it cannot compare."
    quote = "The monotonic clock exists in the user agent, so it cannot compare."
    spans = find_quote_spans(source, quote)
    assert len(spans) == 1
    start, end = spans[0]
    assert source[start:end] == (
        "The monotonic clock exists in the user agent , so it cannot compare."
    )
    assert source[start:end] != quote


def test_clock_comma_across_flexible_whitespace():
    source = (
        "durations don't have an associated clock , and a duration\n\n"
        "calculated from two moments"
    )
    quote = (
        "durations don't have an associated clock, and a duration "
        "calculated from two moments"
    )
    spans = find_quote_spans(source, quote)
    assert len(spans) == 1
    start, end = spans[0]
    assert "clock ," in source[start:end]
    assert "\n\n" in source[start:end]


def test_straight_apostrophe_space_equivalence():
    source = "If context 's current wall time is less than source 's expiry."
    quote = "If context's current wall time is less than source's expiry."
    spans = find_quote_spans(source, quote)
    assert len(spans) == 1
    assert "context 's" in source[spans[0][0] : spans[0][1]]


def test_curly_apostrophe_space_without_straight_conversion():
    source = "it can\u2019t be used; also can 't elsewhere"
    curly_quote = "it can\u2019t be used"
    straight_quote = "it can't be used"
    curly_spans = find_quote_spans(source, curly_quote)
    assert len(curly_spans) == 1
    assert source[curly_spans[0][0] : curly_spans[0][1]] == "it can\u2019t be used"
    # Straight apostrophe must not match curly source punctuation.
    assert find_quote_spans(source, straight_quote) == []
    # Curly quote must not match a straight-only source span.
    straight_source = "it can't be used; also can\u2019t elsewhere"
    assert find_quote_spans(straight_source, curly_quote) == []
    assert len(find_quote_spans(straight_source, straight_quote)) == 1


def test_exact_match_takes_precedence():
    text = (
        "user agent, so exact form appears. "
        "Later the extraction form user agent , so also appears."
    )
    quote = "user agent, so exact form appears"
    spans = find_quote_spans(text, quote)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == quote
    assert "agent ," not in text[start:end]


def test_returned_spans_index_original_source():
    source = "prefix agent , so suffix and more words here"
    quote = "agent, so suffix and more"
    spans = find_quote_spans(source, quote)
    assert len(spans) == 1
    start, end = spans[0]
    assert source[start:end] == "agent , so suffix and more"
    assert source[start:end] != quote


def test_multiple_flexible_matches_remain_ambiguous():
    text = "alpha user agent , so beta user agent , so gamma enough"
    quote = "user agent, so"
    assert len(find_quote_spans(text, quote)) == 2
    sec = Section(
        id="s-1", title="1. A", text=text, level=1, start_char=0, end_char=len(text)
    )
    out = parse_findings_payload(
        {"findings": [_item("S0001", quote)]},
        section_bindings=_binding("S0001", sec),
        document_text=text,
    )
    ev = out[0].evidence[0]
    assert ev.location is None or (
        ev.location.start_char is None and ev.location.end_char is None
    )


def test_quote_cannot_match_different_section():
    text_a = "Section A mentions the user agent , so locally only."
    text_b = "Section B has unrelated epoch guidance for persistence."
    document = text_a + "\n\n" + text_b
    sec_a = Section(
        id="s-a", title="1. A", text=text_a, level=1, start_char=0, end_char=len(text_a)
    )
    sec_b = Section(
        id="s-b",
        title="2. B",
        text=text_b,
        level=1,
        start_char=len(text_a) + 2,
        end_char=len(document),
    )
    bindings = {
        "S0001": SectionBinding(
            opaque_ref="S0001",
            section=sec_a,
            canonical_id="s-a",
            canonical_title="1. A",
        ),
        "S0002": SectionBinding(
            opaque_ref="S0002",
            section=sec_b,
            canonical_id="s-b",
            canonical_title="2. B",
        ),
    }
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        parse_findings_payload(
            {
                "findings": [
                    _item(
                        "S0002",
                        "Section A mentions the user agent, so locally only.",
                    )
                ]
            },
            section_bindings=bindings,
            document_text=document,
        )


def test_changed_punctuation_still_fails():
    source = "use the user agent , so it works across restarts here"
    quote = "use the user agent; so it works across restarts here"
    assert find_quote_spans(source, quote) == []


def test_missing_punctuation_still_fails():
    source = "use the user agent , so it works across restarts here"
    quote = "use the user agent so it works across restarts here"
    assert find_quote_spans(source, quote) == []


def test_word_edits_still_fail():
    source = "use the user agent , so it works across restarts here"
    assert (
        find_quote_spans(
            source, "use the user agent, so it fails across restarts here"
        )
        == []
    )
    assert (
        find_quote_spans(
            source, "use the client agent, so it works across restarts here"
        )
        == []
    )
    assert (
        find_quote_spans(
            source, "use agent the user, so it works across restarts here"
        )
        == []
    )


def test_noncontiguous_text_cannot_be_joined():
    source = (
        "Alpha user agent , so first. "
        "Middle noise about epochs. "
        "Omega later clause finishes."
    )
    quote = "Alpha user agent, so first. Omega later clause finishes."
    assert find_quote_spans(source, quote) == []


@pytest.mark.parametrize("quote", ["", "   ", "\n\t"])
def test_empty_and_whitespace_quotes_fail_policy_a(quote):
    source = "use the user agent , so it works across restarts here"
    assert find_quote_spans(source, quote) == []
    sec = Section(
        id="s-1", title="1. A", text=source, level=1, start_char=0, end_char=len(source)
    )
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        parse_findings_payload(
            {"findings": [_item("S0001", quote)]},
            section_bindings=_binding("S0001", sec),
            document_text=source,
        )


def test_incorrect_quote_still_aborts_whole_response():
    source = "use the user agent , so it works across restarts here"
    sec = Section(
        id="s-1", title="1. A", text=source, level=1, start_char=0, end_char=len(source)
    )
    good = _item("S0001", "use the user agent, so it works across restarts here")
    bad = _item(
        "S0001",
        "this paraphrased claim is not present in the section text at all",
        title="Bad",
    )
    with pytest.raises(
        FindingParseError,
        match=r"Finding item 1: invalid field evidence\.quote",
    ):
        parse_findings_payload(
            {"findings": [good, bad]},
            section_bindings=_binding("S0001", sec),
            document_text=source,
        )


def test_punct_spacing_fixture_grounds_claimed_sections():
    """Synthetic doc + findings: agent,/clock,/context's ground with original offsets."""
    plan = plan_scan(
        PUNCT_DOC.read_text(encoding="utf-8"),
        doc_id="punct-spacing-synth",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    bindings = plan.chunks[0].allowlist()
    assert set(bindings) == {"S0001", "S0002", "S0003"}
    payload = json.loads(PUNCT_FINDINGS.read_text(encoding="utf-8"))
    findings = parse_findings_payload(
        payload,
        section_bindings=bindings,
        document_text=plan.document.text,
    )
    assert len(findings) == 3
    expected = [
        ("S0001", "s-1", "agent ,"),
        ("S0002", "s-2", "clock ,"),
        ("S0003", "s-3", "context \u2019s"),
    ]
    for finding, (opaque, canonical, marker), raw_item in zip(
        findings, expected, payload["findings"], strict=True
    ):
        assert raw_item["section_id"] == opaque
        assert finding.location.section_id == canonical
        model_quote = raw_item["evidence"][0]["quote"]
        assert finding.evidence[0].quote == model_quote
        loc = finding.evidence[0].location
        assert loc is not None
        assert loc.start_char is not None and loc.end_char is not None
        source_slice = plan.document.text[loc.start_char : loc.end_char]
        # Published span is the unmodified source substring (extraction spacing).
        assert source_slice != model_quote
        assert marker in source_slice
        sec = bindings[opaque].section
        assert loc.start_char >= sec.start_char
        assert loc.end_char <= sec.start_char + len(sec.text)
        assert plan.document.text[loc.start_char : loc.end_char] == source_slice
        # Quote must not ground in a different claimed section.
        for other_ref, other in bindings.items():
            if other_ref == opaque:
                continue
            assert not find_quote_spans(other.section.text, model_quote)

    # Cross-section claim still fails Policy A (quote from S0001 on S0003).
    stolen = dict(payload["findings"][0])
    stolen["section_id"] = "S0003"
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        parse_findings_payload(
            {"findings": [stolen]},
            section_bindings=bindings,
            document_text=plan.document.text,
        )
