# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Horizon validation integration with findings (step 2)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from tads.export import report_to_markdown
from tads.pipeline.validate import apply_horizon_validation, enrich_finding_validation
from tads.schemas.assurance import AssuranceStatus, derive_assurance_status
from tads.schemas.findings import (
    Disposition,
    Finding,
    FindingType,
    Severity,
    ValidationStatus,
)
from tads.schemas.horizon import TimeRepresentationParams
from tads.schemas.report import DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence

UTC = timezone.utc
EPOCH_1970 = datetime(1970, 1, 1, tzinfo=UTC)


def _base_finding(**kwargs) -> Finding:
    data = dict(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Horizon",
        description="desc",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        machine_interpretation="interp",
        disposition=Disposition.NEW,
    )
    data.update(kwargs)
    return Finding(**data)


def test_apply_horizon_sets_structured_result_and_validation_status():
    finding = _base_finding(
        time_representation=TimeRepresentationParams(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
            claimed_horizon=date(2038, 1, 19),
        )
    )
    out = apply_horizon_validation(finding)
    assert out.disposition == Disposition.NEW
    assert out.horizon_validation is not None
    assert out.horizon_validation.status == "verified"
    assert out.horizon_validation.claim_consistent is True
    assert out.validation_status == ValidationStatus.VERIFIED
    assert out.horizon_validation.last_representable == datetime(
        2038, 1, 19, 3, 14, 7, tzinfo=UTC
    )
    assert derive_assurance_status(out) == AssuranceStatus.DETERMINISTICALLY_VALIDATED


def test_apply_horizon_contradiction_maps_to_failed_without_touching_disposition():
    finding = _base_finding(
        disposition=Disposition.NEEDS_REVIEW,
        time_representation=TimeRepresentationParams(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
            claimed_horizon=date(2036, 2, 7),
        ),
    )
    out = apply_horizon_validation(finding)
    assert out.disposition == Disposition.NEEDS_REVIEW
    assert out.horizon_validation is not None
    assert out.horizon_validation.status == "contradicted"
    assert out.validation_status == ValidationStatus.FAILED
    # Deferred disposition still wins for public assurance.
    assert derive_assurance_status(out) == AssuranceStatus.DEFERRED


def test_enrich_prefers_structured_time_representation():
    finding = _base_finding(
        title="Mentions 2038-01-19 in prose",
        time_representation=TimeRepresentationParams(
            width_bits=32,
            signed=False,
            epoch=datetime(1900, 1, 1, tzinfo=UTC),
            unit="seconds",
            claimed_horizon=date(2036, 2, 7),
        ),
    )
    out = enrich_finding_validation(finding)
    assert out.horizon_validation is not None
    assert out.horizon_validation.last_representable is not None
    assert out.horizon_validation.last_representable.year == 2036


def test_horizon_validation_does_not_auto_accept_candidate():
    finding = _base_finding(
        time_representation=TimeRepresentationParams(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
            claimed_horizon=date(2038, 1, 19),
        )
    )
    out = apply_horizon_validation(finding)
    assert out.disposition == Disposition.NEW
    assert derive_assurance_status(out) == AssuranceStatus.DETERMINISTICALLY_VALIDATED
    assert derive_assurance_status(out) != AssuranceStatus.HUMAN_CONFIRMED


def test_markdown_renders_deterministic_validation_section():
    finding = apply_horizon_validation(
        _base_finding(
            time_representation=TimeRepresentationParams(
                width_bits=32,
                signed=False,
                epoch=datetime(1900, 1, 1, tzinfo=UTC),
                unit="seconds",
                claimed_horizon=date(2036, 2, 7),
            )
        )
    )
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC5905"),
        run=RunMetadata(scanner_version="0.4.0"),
        findings=[finding],
    )
    md = report_to_markdown(report)
    assert "#### Deterministic validation" in md
    assert "Status: **Verified**" in md
    assert "Width: 32 bits" in md
    assert "Unsigned" in md
    assert "4,294,967,295" in md
    assert "2036-02-07T06:28:15Z" in md
    assert "Consistent" in md
    assert "does **not** confirm a standards defect" in md


def test_old_json_without_horizon_fields_still_loads():
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
    assert report.findings[0].time_representation is None
    assert report.findings[0].horizon_validation is None
