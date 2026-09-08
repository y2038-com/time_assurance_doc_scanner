# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Phase B: ambiguous signedness, named epochs, ISO date regex."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tads.pipeline.parse_findings import parse_findings_payload
from tads.pipeline.validate import apply_horizon_validation, enrich_finding_validation
from tads.schemas.findings import (
    Finding,
    FindingType,
    Severity,
    ValidationStatus,
)
from tads.schemas.horizon import EpochKind, TimeRepresentationParams
from tads.schemas.taxonomy import Confidence, TimeDomain
from tads.validators import (
    WELL_KNOWN_EPOCHS,
    TimeRepresentation,
    parse_epoch_kind,
    resolve_epoch_datetime,
    validate_time_representation,
)
from tads.validators.known import parse_iso_date

UTC = timezone.utc
EPOCH_1970 = datetime(1970, 1, 1, tzinfo=UTC)
EPOCH_1900 = datetime(1900, 1, 1, tzinfo=UTC)


def test_ambiguous_signedness_unix_32bit_both_horizons():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=None,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "ambiguous_signedness"
    assert result.signedness_resolved is False
    assert result.signed_interpretation is not None
    assert result.unsigned_interpretation is not None
    assert result.signed_interpretation.last_representable == datetime(
        2038, 1, 19, 3, 14, 7, tzinfo=UTC
    )
    assert result.unsigned_interpretation.last_representable == datetime(
        2106, 2, 7, 6, 28, 15, tzinfo=UTC
    )
    assert result.minimum_value is None
    assert result.maximum_value is None


def test_ambiguous_signedness_ntp_epoch():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=None,
            epoch_kind=EpochKind.NTP,
            unit="seconds",
        )
    )
    assert result.status == "ambiguous_signedness"
    assert result.unsigned_interpretation is not None
    assert result.unsigned_interpretation.last_representable == datetime(
        2036, 2, 7, 6, 28, 15, tzinfo=UTC
    )


def test_explicit_signed_true_unchanged():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.signedness_resolved is True
    assert result.signed_interpretation is None
    assert result.last_representable == datetime(2038, 1, 19, 3, 14, 7, tzinfo=UTC)


def test_explicit_signed_false_unchanged():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.signedness_resolved is True
    assert result.last_representable == datetime(2106, 2, 7, 6, 28, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "kind",
    [
        EpochKind.UNIX,
        EpochKind.NTP,
        EpochKind.GPS,
        EpochKind.MJD,
        EpochKind.NTFS,
        EpochKind.UUID,
        EpochKind.TAI_1958,
    ],
)
def test_named_epochs_resolve_deterministically(kind: EpochKind):
    resolved, notes, err = resolve_epoch_datetime(epoch_kind=kind, epoch=None)
    assert err is None
    assert resolved == WELL_KNOWN_EPOCHS[kind]
    assert any(kind.value in n for n in notes)


def test_epoch_kind_other_requires_datetime():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch_kind=EpochKind.OTHER,
            epoch=None,
            unit="seconds",
        )
    )
    assert result.status == "insufficient_parameters"
    assert any("other" in n.lower() for n in result.notes)


def test_epoch_kind_other_with_explicit_datetime():
    custom = datetime(2000, 1, 1, tzinfo=UTC)
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch_kind=EpochKind.OTHER,
            epoch=custom,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.earliest_representable is not None


def test_mjd_by_name_without_literal_date():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=16,
            signed=False,
            epoch_kind=EpochKind.MJD,
            unit="days",
        )
    )
    assert result.status == "verified"
    assert result.earliest_representable == WELL_KNOWN_EPOCHS[EpochKind.MJD]


def test_parse_epoch_kind_aliases_and_invalid():
    assert parse_epoch_kind("POSIX") == EpochKind.UNIX
    assert parse_epoch_kind("modified-julian-date") == EpochKind.MJD
    assert parse_epoch_kind("not-an-epoch") is None


def test_coerce_epoch_kind_from_findings_payload():
    findings = parse_findings_payload(
        {
            "findings": [
                {
                    "finding_type": "time_assurance_gap",
                    "title": "MJD counter",
                    "description": "Uses MJD",
                    "severity": "medium",
                    "confidence": "medium",
                    "domains": ["epoch"],
                    "evidence": [],
                    "machine_interpretation": "MJD epoch",
                    "time_representation": {
                        "width_bits": 16,
                        "signed": False,
                        "epoch_kind": "mjd",
                        "unit": "days",
                    },
                }
            ]
        }
    )
    assert findings[0].time_representation is not None
    assert findings[0].time_representation.epoch_kind == EpochKind.MJD
    assert findings[0].time_representation.epoch is None


def test_apply_horizon_ambiguous_maps_not_applicable():
    finding = Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Unknown signedness",
        description="32-bit seconds from Unix epoch",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        machine_interpretation="signedness unclear",
        time_representation=TimeRepresentationParams(
            width_bits=32,
            signed=None,
            epoch_kind=EpochKind.UNIX,
            unit="seconds",
        ),
    )
    out = apply_horizon_validation(finding)
    assert out.horizon_validation is not None
    assert out.horizon_validation.status == "ambiguous_signedness"
    assert out.validation_status == ValidationStatus.NOT_APPLICABLE
    assert out.horizon_validation.signed_interpretation is not None
    assert out.horizon_validation.unsigned_interpretation is not None


@pytest.mark.parametrize(
    "iso,ok",
    [
        ("1999-12-31", True),
        ("2038-01-19", True),
        ("2106-02-07", True),
        ("2217-01-01", True),
        ("2038-13-40", False),
        ("not-a-date", False),
    ],
)
def test_iso_date_regex_and_parse_range(iso: str, ok: bool):
    from tads.pipeline import validate as validate_mod

    matches = validate_mod._ISO_DATE.findall(f"horizon on {iso} mentioned")
    if not ok and iso == "2038-13-40":
        # Shape matches regex; parse_iso_date must reject.
        assert matches == ["2038-13-40"]
        assert parse_iso_date(iso) is None
        return
    if not ok:
        assert matches == []
        return
    assert matches == [iso]
    assert parse_iso_date(iso) is not None


def test_enrich_heuristic_reaches_y2106():
    finding = Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Y2106",
        description="Rollover cited as 2106-02-07 for unsigned 32-bit Unix time.",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        domains=[TimeDomain.Y2106],
        machine_interpretation="2106-02-07",
    )
    out = enrich_finding_validation(finding)
    assert out.validation_status == ValidationStatus.VERIFIED
    assert out.validation_detail is not None
    assert "2106" in out.validation_detail
