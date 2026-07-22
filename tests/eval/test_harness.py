"""Eval harness tests."""

from pathlib import Path

from tads.eval import load_labels, load_manifest, match_findings


ROOT = Path(__file__).resolve().parents[2]


def test_load_manifest_and_labels():
    manifest = load_manifest(ROOT / "eval/corpus/seed_manifest.yaml")
    assert len(manifest.documents) == 5
    assert manifest.documents[0].doc_id == "RFC5905"
    labels = load_labels(ROOT / "eval/corpus/labels/RFC5905.json")
    assert labels[0].id == "E-001"


def test_match_findings():
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
