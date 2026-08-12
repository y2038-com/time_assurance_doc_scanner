# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Resilient findings JSON coercion."""

from __future__ import annotations

from tads.pipeline.parse_findings import _coerce_optional_str, parse_findings_payload


def test_coerce_optional_str_joins_lists():
    assert _coerce_optional_str(["6", "8"]) == "6; 8"
    assert _coerce_optional_str(["Data Types", "On-Wire Protocol"]) == (
        "Data Types; On-Wire Protocol"
    )
    assert _coerce_optional_str("s-6") == "s-6"
    assert _coerce_optional_str(None) is None


def test_parse_findings_accepts_list_locations():
    payload = {
        "findings": [
            {
                "finding_type": "explicit_defect",
                "title": "Era rollover",
                "description": "32-bit NTP seconds wrap.",
                "severity": "high",
                "confidence": "medium",
                "section_id": ["6", "8"],
                "section_title": ["Data Types", "On-Wire Protocol"],
                "domains": ["y2036"],
                "evidence": [{"quote": "The seconds field is 32 bits."}],
                "recommendation_level1": "Document era handling.",
            }
        ]
    }
    findings = parse_findings_payload(payload)
    assert len(findings) == 1
    assert findings[0].location is not None
    assert findings[0].location.section_id == "6; 8"
    assert findings[0].location.section_title == "Data Types; On-Wire Protocol"


def test_parse_time_representation_and_leaves_null_object_out():
    payload = {
        "findings": [
            {
                "finding_type": "lifetime_representation_mismatch",
                "title": "NTP seconds",
                "description": "32-bit unsigned seconds from 1900.",
                "severity": "high",
                "confidence": "high",
                "domains": ["y2036"],
                "evidence": [{"quote": "32-bit unsigned seconds"}],
                "time_representation": {
                    "width_bits": "32",
                    "signed": "unsigned",
                    "epoch": "1900-01-01",
                    "unit": "seconds",
                    "claimed_horizon": "2036-02-07",
                    "ticks_per_second": None,
                    "rollover_behavior": None,
                },
            },
            {
                "finding_type": "missing_documentation",
                "title": "No counter",
                "description": "Prose only.",
                "severity": "low",
                "confidence": "low",
                "domains": ["other"],
                "evidence": [],
                "time_representation": {
                    "width_bits": None,
                    "signed": None,
                    "epoch": None,
                    "unit": None,
                    "claimed_horizon": None,
                },
            },
        ]
    }
    findings = parse_findings_payload(payload)
    assert len(findings) == 2
    rep = findings[0].time_representation
    assert rep is not None
    assert rep.width_bits == 32
    assert rep.signed is False
    assert rep.epoch is not None
    assert rep.epoch.year == 1900
    assert rep.unit == "seconds"
    assert findings[1].time_representation is None


def test_date_only_claimed_horizon_stays_date():
    findings = parse_findings_payload(
        {
            "findings": [
                {
                    "finding_type": "time_assurance_gap",
                    "title": "Unix",
                    "description": "signed 32",
                    "severity": "high",
                    "confidence": "high",
                    "domains": ["y2038"],
                    "evidence": [],
                    "time_representation": {
                        "width_bits": 32,
                        "signed": True,
                        "epoch": "1970-01-01T00:00:00Z",
                        "unit": "seconds",
                        "claimed_horizon": "2038-01-19",
                    },
                }
            ]
        }
    )
    from datetime import date

    claimed = findings[0].time_representation.claimed_horizon
    assert claimed == date(2038, 1, 19)
    assert not hasattr(claimed, "hour")
