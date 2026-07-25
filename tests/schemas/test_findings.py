# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Schema unit tests."""

from tads.schemas import (
    Disposition,
    DocumentIdentity,
    Finding,
    FindingType,
    Report,
    RunMetadata,
    Severity,
    ValidationStatus,
)
from tads.schemas.taxonomy import Confidence, TimeDomain


def test_finding_round_trip():
    finding = Finding(
        id="F-001",
        finding_type=FindingType.IMPLIED_ASSUMPTION,
        title="Unsigned 32-bit horizon unspecified",
        description="Document does not state behavior after 2106.",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        domains=[TimeDomain.Y2106, TimeDomain.ROLLOVER],
        machine_interpretation="Missing rollover specification.",
        validation_status=ValidationStatus.UNVERIFIED,
        recommendation_level1="Define supported date range and rollover behavior.",
        disposition=Disposition.NEW,
    )
    data = finding.model_dump()
    restored = Finding.model_validate(data)
    assert restored.id == "F-001"
    assert restored.domains == [TimeDomain.Y2106, TimeDomain.ROLLOVER]


def test_report_finding_by_id():
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC5905"),
        run=RunMetadata(scanner_version="0.1.0"),
        findings=[
            Finding(
                id="F-001",
                finding_type=FindingType.TIME_ASSURANCE_GAP,
                title="Era guidance",
                description="...",
                severity=Severity.HIGH,
                confidence=Confidence.HIGH,
            )
        ],
    )
    assert report.finding_by_id("F-001") is not None
    assert report.finding_by_id("missing") is None
