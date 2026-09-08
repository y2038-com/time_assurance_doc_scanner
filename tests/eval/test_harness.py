# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Eval harness tests."""

from pathlib import Path

from tads.eval import ExpectedFinding, load_labels, load_manifest, match_findings
from tads.schemas.findings import FindingType


ROOT = Path(__file__).resolve().parents[2]


def test_load_manifest_and_labels():
    manifest = load_manifest(ROOT / "eval/corpus/seed_manifest.yaml")
    assert len(manifest.documents) == 5
    assert manifest.documents[0].doc_id == "RFC5905"
    labels = load_labels(ROOT / "eval/corpus/labels/RFC5905.json")
    assert labels[0].id == "E-001"


def test_match_findings_same_type():
    labels = load_labels(ROOT / "eval/corpus/labels/RFC5905.json")
    predicted = [
        {
            "id": "F-001",
            "finding_type": "time_assurance_gap",
            "location": {"section_id": "s-6"},
            "evidence": [{"quote": "The era number increments on overflow."}],
        }
    ]
    summary = match_findings(labels, predicted)
    assert summary.true_positives == 1
    assert summary.recall == 1.0
    assert summary.matches[0].type_agreement is True
    assert summary.matches[0].reason == "matched"


def test_match_despite_finding_type_disagreement():
    expected = [
        ExpectedFinding(
            id="E-1",
            finding_type=FindingType.TIME_ASSURANCE_GAP,
            quote_contains="era wrap",
        )
    ]
    predicted = [
        {
            "id": "F-9",
            "finding_type": "missing_documentation",
            "evidence": [{"quote": "Operators must handle era wrap carefully."}],
        }
    ]
    summary = match_findings(expected, predicted)
    assert summary.true_positives == 1
    assert summary.matches[0].matched is True
    assert summary.matches[0].type_agreement is False
    assert "disagreement" in summary.matches[0].reason


def test_different_source_does_not_match():
    expected = [
        ExpectedFinding(
            id="E-1",
            finding_type=FindingType.TIME_ASSURANCE_GAP,
            quote_contains="era wrap",
        )
    ]
    predicted = [
        {
            "id": "F-1",
            "finding_type": "time_assurance_gap",
            "evidence": [{"quote": "Leap seconds are optional."}],
        }
    ]
    summary = match_findings(expected, predicted)
    assert summary.true_positives == 0
    assert summary.matches[0].matched is False
    assert summary.matches[0].type_agreement is None


def test_nearby_distinct_findings_remain_distinct():
    expected = [
        ExpectedFinding(
            id="E-a",
            finding_type=FindingType.TIME_ASSURANCE_GAP,
            quote_contains="era zero",
        ),
        ExpectedFinding(
            id="E-b",
            finding_type=FindingType.MISSING_DOCUMENTATION,
            quote_contains="leap second table",
        ),
    ]
    predicted = [
        {
            "id": "F-a",
            "finding_type": "time_assurance_gap",
            "evidence": [{"quote": "era zero handling"}],
        },
        {
            "id": "F-b",
            "finding_type": "missing_documentation",
            "evidence": [{"quote": "leap second table missing"}],
        },
    ]
    summary = match_findings(expected, predicted)
    assert summary.true_positives == 2
    ids = {m.expected_id: m.matched_finding_id for m in summary.matches}
    assert ids["E-a"] == "F-a"
    assert ids["E-b"] == "F-b"
