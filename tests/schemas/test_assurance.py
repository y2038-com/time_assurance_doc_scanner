# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Assurance status derivation tests (Phase A/B display layer)."""

from tads.export import report_to_markdown
from tads.schemas.assurance import (
    AssuranceStatus,
    assurance_status_label,
    derive_assurance_status,
)
from tads.schemas.findings import (
    Disposition,
    Finding,
    FindingType,
    Severity,
    ValidationStatus,
)
from tads.schemas.report import DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence


def _finding(**kwargs) -> Finding:
    base = dict(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Era",
        description="desc",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        machine_interpretation="interp",
    )
    base.update(kwargs)
    return Finding(**base)


def test_derive_defaults_to_candidate():
    assert derive_assurance_status(_finding()) == AssuranceStatus.CANDIDATE


def test_derive_human_confirmed_and_rejected_and_deferred():
    assert (
        derive_assurance_status(_finding(disposition=Disposition.ACCEPTED))
        == AssuranceStatus.HUMAN_CONFIRMED
    )
    assert (
        derive_assurance_status(_finding(disposition=Disposition.REJECTED))
        == AssuranceStatus.REJECTED
    )
    assert (
        derive_assurance_status(_finding(disposition=Disposition.NEEDS_REVIEW))
        == AssuranceStatus.DEFERRED
    )


def test_derive_deterministically_validated_from_verified():
    assert (
        derive_assurance_status(
            _finding(validation_status=ValidationStatus.VERIFIED)
        )
        == AssuranceStatus.DETERMINISTICALLY_VALIDATED
    )


def test_derive_source_verified():
    assert (
        derive_assurance_status(_finding(source_verified=True))
        == AssuranceStatus.SOURCE_VERIFIED
    )


def test_deterministic_precedes_source_verified():
    assert (
        derive_assurance_status(
            _finding(
                source_verified=True,
                validation_status=ValidationStatus.VERIFIED,
            )
        )
        == AssuranceStatus.DETERMINISTICALLY_VALIDATED
    )


def test_rejected_precedes_human_and_deterministic():
    assert (
        derive_assurance_status(
            _finding(
                disposition=Disposition.REJECTED,
                validation_status=ValidationStatus.VERIFIED,
            )
        )
        == AssuranceStatus.REJECTED
    )
    assert (
        derive_assurance_status(
            _finding(
                disposition=Disposition.ACCEPTED,
                validation_status=ValidationStatus.VERIFIED,
            )
        )
        == AssuranceStatus.HUMAN_CONFIRMED
    )


def test_labels_reserve_validated_finding_phrase():
    assert "validated finding" in assurance_status_label(
        AssuranceStatus.HUMAN_CONFIRMED
    )
    assert "validated finding" not in assurance_status_label(
        AssuranceStatus.DETERMINISTICALLY_VALIDATED
    )
    assert (
        assurance_status_label(AssuranceStatus.DETERMINISTICALLY_VALIDATED)
        == "deterministically checked candidate"
    )
    assert (
        assurance_status_label(AssuranceStatus.CANDIDATE) == "candidate for review"
    )


def test_markdown_provenance_header():
    report = Report(
        document=DocumentIdentity(
            corpus="ietf",
            doc_id="RFC5905",
            content_sha256="abc123",
        ),
        run=RunMetadata(scanner_version="0.4.0", prompt_framework_version="0.5.0"),
    )
    md = report_to_markdown(report)
    assert "**Content SHA-256:** abc123" in md
    assert "**Scanner:** 0.4.0" in md
    assert "**Prompt framework:** 0.5.0" in md


def test_markdown_omits_content_hash_when_absent():
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(scanner_version="0.4.0", prompt_framework_version="0.5.0"),
    )
    md = report_to_markdown(report)
    assert "Content SHA-256" not in md
    assert "**Prompt framework:** 0.5.0" in md


def test_markdown_uses_candidate_language():
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(scanner_version="0.0.0"),
        findings=[_finding()],
    )
    md = report_to_markdown(report)
    assert "## Candidates for review" in md
    assert "## Findings" not in md
    assert "machine-generated candidates for review" in md
    assert "validated finding" in md  # reserved-phrase notice
    assert "**Assurance status:** candidate for review (`candidate`)" in md
    assert "Assurance status: candidate=1" in md
    assert "Source verified:" in md
    assert "Deterministic check:" in md
    assert "Candidates (JSON): **1**" in md
    assert "Scope: core=1" in md
    assert "- **Scope:** `core`" in md


def test_old_json_without_source_verified_loads():
    raw = """
    {
      "schema_version": "0.1.0",
      "document": {"corpus": "ietf", "doc_id": "RFC9999"},
      "run": {"scanner_version": "0.4.0"},
      "findings": [
        {
          "id": "F-001",
          "finding_type": "time_assurance_gap",
          "title": "Era",
          "description": "desc",
          "severity": "medium",
          "confidence": "medium",
          "machine_interpretation": "interp",
          "disposition": "new",
          "validation_status": "unverified"
        }
      ]
    }
    """
    report = Report.model_validate_json(raw)
    assert report.findings[0].source_verified is False
    assert derive_assurance_status(report.findings[0]) == AssuranceStatus.CANDIDATE
