# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Schema 0.3.0 claimed_horizon object-or-null contract (model-facing parser)."""

from __future__ import annotations

import json

import pytest

from tads.pipeline.parse_findings import FindingParseError, parse_findings_payload
from tads.pipeline.validate import apply_horizon_validation
from tads.schemas.horizon import HorizonPrecision
from tads.schemas.report import Report
from tads.schemas.taxonomy import TimeDomain


def _valid_item(**overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Era wrap guidance may be incomplete",
        "description": "32-bit NTP seconds wrap on a known horizon.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["y2036", "rollover"],
        "section_id": "s-1",
        "section_title": "1. Introduction",
        "evidence": [
            {"quote": "Timestamps are 32-bit seconds", "note": "width mentioned"}
        ],
        "machine_interpretation": "Potential Y2036-related assurance gap.",
        "recommendation_level1": "Clarify era rollover.",
        "scope_relevance": "core",
        "scope_rationale": (
            "This matters to time assurance because NTP seconds are a "
            "fixed-width counter that wraps on a known horizon."
        ),
        "time_representation": {
            "width_bits": 32,
            "signed": False,
            "epoch": "1900-01-01T00:00:00Z",
            "unit": "seconds",
            "ticks_per_second": None,
            "claimed_horizon": {"value": "2036-02-07", "precision": "day"},
            "rollover_behavior": "wrap",
        },
    }
    item.update(overrides)
    return item


def _repr_with_horizon(claimed) -> dict:
    return _valid_item(
        time_representation={
            "width_bits": 32,
            "signed": False,
            "epoch_kind": "ntp",
            "epoch": None,
            "unit": "seconds",
            "ticks_per_second": None,
            "claimed_horizon": claimed,
            "rollover_behavior": None,
        }
    )


def test_year_precision_parses_and_stays_lexical():
    finding = parse_findings_payload(
        {"findings": [_repr_with_horizon({"value": "2036", "precision": "year"})]}
    )[0]
    claimed = finding.time_representation.claimed_horizon
    assert claimed is not None
    assert claimed.value == "2036"
    assert claimed.precision == HorizonPrecision.YEAR
    dumped = claimed.model_dump()
    assert dumped == {"value": "2036", "precision": "year"}
    serialized = json.dumps(dumped)
    assert "2036-01-01" not in serialized
    assert "2036-12-31" not in serialized
    assert "T00:00:00" not in serialized


def test_month_precision_parses():
    claimed = parse_findings_payload(
        {"findings": [_repr_with_horizon({"value": "2036-02", "precision": "month"})]}
    )[0].time_representation.claimed_horizon
    assert claimed is not None
    assert claimed.value == "2036-02"
    assert claimed.precision == HorizonPrecision.MONTH
    assert "2036-02-01" not in json.dumps(claimed.model_dump())


def test_leap_day_and_ordinary_day_precision_parse():
    leap = parse_findings_payload(
        {"findings": [_repr_with_horizon({"value": "2036-02-29", "precision": "day"})]}
    )[0].time_representation.claimed_horizon
    ordinary = parse_findings_payload(
        {"findings": [_repr_with_horizon({"value": "2036-02-07", "precision": "day"})]}
    )[0].time_representation.claimed_horizon
    assert leap is not None and leap.value == "2036-02-29"
    assert ordinary is not None and ordinary.value == "2036-02-07"
    assert leap.precision == ordinary.precision == HorizonPrecision.DAY


def test_timezone_aware_instant_parses():
    claimed = parse_findings_payload(
        {
            "findings": [
                _repr_with_horizon(
                    {"value": "2036-02-07T06:28:16Z", "precision": "instant"}
                )
            ]
        }
    )[0].time_representation.claimed_horizon
    assert claimed is not None
    assert claimed.value == "2036-02-07T06:28:16Z"
    assert claimed.precision == HorizonPrecision.INSTANT


def test_null_claimed_horizon_is_allowed():
    finding = parse_findings_payload({"findings": [_repr_with_horizon(None)]})[0]
    assert finding.time_representation is not None
    assert finding.time_representation.claimed_horizon is None


def test_omitted_time_representation_remains_null():
    item = _valid_item()
    del item["time_representation"]
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.time_representation is None


@pytest.mark.parametrize(
    "claimed",
    [
        2036,
        {"value": "0000", "precision": "year"},
        {"value": "2036-13", "precision": "month"},
        {"value": "2036-02-30", "precision": "day"},
        {"value": "2023-02-29", "precision": "day"},
        {"value": "2036-02-07T25:00:00Z", "precision": "instant"},
        {"value": "2036-02-07T06:28:16", "precision": "instant"},
        {"value": "2036", "precision": "day"},
        {"value": "2036-02-07", "precision": "year"},
        {"value": "2036-02", "precision": "instant"},
        {"precision": "year"},
        {"value": "2036"},
        {"value": "2036", "precision": "year", "note": "until"},
        {"value": 2036, "precision": "year"},
        {"value": "2036", "precision": "era"},
        "2036",
        "2036-02-07",
    ],
)
def test_invalid_claimed_horizon_fails_closed(claimed):
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload({"findings": [_repr_with_horizon(claimed)]})


def test_does_not_infer_precision_from_malformed_value():
    with pytest.raises(FindingParseError):
        parse_findings_payload(
            {
                "findings": [
                    _repr_with_horizon({"value": "2036-02-07", "precision": "year"})
                ]
            }
        )


def test_rfc868_year_horizon_fixture_succeeds():
    item = _valid_item(
        title="TIME protocol seconds continue until the year 2036",
        description=(
            "The document says the 32-bit seconds field is valid until the year "
            "2036 without giving a calendar day."
        ),
        evidence=[
            {
                "quote": "valid until the year 2036",
                "note": "source-stated year horizon",
            }
        ],
        time_representation={
            "width_bits": 32,
            "signed": False,
            "epoch_kind": "ntp",
            "epoch": None,
            "unit": "seconds",
            "ticks_per_second": None,
            "claimed_horizon": {"value": "2036", "precision": "year"},
            "rollover_behavior": None,
        },
    )
    finding = parse_findings_payload({"findings": [item]})[0]
    claimed = finding.time_representation.claimed_horizon
    assert claimed is not None
    assert claimed.value == "2036"
    assert claimed.precision == HorizonPrecision.YEAR
    enriched = apply_horizon_validation(finding)
    assert enriched.horizon_validation is not None
    assert enriched.horizon_validation.claim_consistent is None
    assert enriched.horizon_validation.last_representable is not None
    assert enriched.horizon_validation.first_out_of_range is not None
    assert enriched.horizon_validation.claimed_horizon.value == "2036"
    notes = " ".join(enriched.horizon_validation.notes)
    assert "year or month precision" in notes
    dump = json.dumps(enriched.horizon_validation.claimed_horizon.model_dump())
    assert dump == json.dumps({"value": "2036", "precision": "year"})
    assert "2036-01-01" not in dump
    assert "2036-12-31" not in dump


def test_day_precision_still_sets_claim_consistent():
    finding = parse_findings_payload(
        {"findings": [_repr_with_horizon({"value": "2036-02-07", "precision": "day"})]}
    )[0]
    enriched = apply_horizon_validation(finding)
    assert enriched.horizon_validation is not None
    assert enriched.horizon_validation.claim_consistent is True
    assert enriched.horizon_validation.last_representable.year == 2036


def test_canonical_domain_list_succeeds():
    tokens = [member.value for member in TimeDomain]
    finding = parse_findings_payload({"findings": [_valid_item(domains=tokens)]})[0]
    assert [domain.value for domain in finding.domains] == tokens


def test_schema_030_report_rejects_scalar_claimed_horizon():
    raw = {
        "schema_version": "0.3.0",
        "document": {"corpus": "ietf", "doc_id": "RFC868"},
        "run": {"scanner_version": "0.6.0rc2"},
        "findings": [
            {
                "id": "F-001",
                "finding_type": "time_assurance_gap",
                "title": "Until 2036",
                "description": "desc",
                "severity": "medium",
                "confidence": "medium",
                "machine_interpretation": "interp",
                "disposition": "new",
                "validation_status": "unverified",
                "time_representation": {
                    "width_bits": 32,
                    "signed": False,
                    "unit": "seconds",
                    "claimed_horizon": "2036-02-07",
                },
            }
        ],
    }
    with pytest.raises(Exception):
        Report.model_validate(raw)
