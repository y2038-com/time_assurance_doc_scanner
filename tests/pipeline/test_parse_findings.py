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
