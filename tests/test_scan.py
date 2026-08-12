# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Scan pipeline and export tests (mock provider — no network)."""

from pathlib import Path

from tads.export import load_report_json, report_to_markdown, write_report_json
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import extract_json_object, parse_findings_payload
from tads.pipeline.validate import enrich_finding_validation
from tads.schemas.findings import Finding, FindingType, Severity, ValidationStatus
from tads.schemas.taxonomy import Confidence, TimeDomain

SAMPLE = """\
Internet Engineering Task Force (IETF)                          D. Mills
Request for Comments: 9999

Network Time Protocol Sample Spec

Abstract

This memo describes a sample protocol.

1. Introduction

Timestamps are 32-bit seconds. The era advances after 2036-02-07.

2. Details

More text.
"""


def test_extract_json_from_fenced_response():
    text = """Here you go:\n```json\n{"findings": []}\n```\n"""
    assert extract_json_object(text) == {"findings": []}


def test_extract_json_repairs_trailing_comma_and_preamble():
    text = """Sure. Here is the result:
{"findings":[{"finding_type":"time_assurance_gap","title":"Era","description":"x","severity":"low","confidence":"low","domains":["y2036"],"evidence":[],"machine_interpretation":"x","recommendation_level1":null},]}
"""
    data = extract_json_object(text)
    assert len(data["findings"]) == 1


def test_extract_json_strips_think_blocks():
    text = """<think>reasoning</think>
{"findings": []}
"""
    assert extract_json_object(text) == {"findings": []}


def test_parse_findings_payload():
    findings = parse_findings_payload(
        {
            "findings": [
                {
                    "finding_type": "implied_assumption",
                    "title": "Era assumed",
                    "description": "Assumes single era",
                    "severity": "high",
                    "confidence": "medium",
                    "domains": ["y2036", "rollover"],
                    "evidence": [{"quote": "era advances"}],
                    "machine_interpretation": "Implied single-era ops",
                    "recommendation_level1": "Define multi-era behavior",
                }
            ]
        }
    )
    assert len(findings) == 1
    assert findings[0].id == "F-001"
    assert TimeDomain.Y2036 in findings[0].domains


def test_enrich_validation_on_known_date():
    finding = Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Y2036 date",
        description="Rollover on 2036-02-07 is cited.",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        domains=[TimeDomain.Y2036],
        machine_interpretation="Claims 2036-02-07",
    )
    enriched = enrich_finding_validation(finding)
    assert enriched.validation_status == ValidationStatus.VERIFIED


def test_run_scan_with_mock(tmp_path: Path):
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
    )
    assert report.document.doc_id == "RFC9999"
    assert "Network Time Protocol Sample Spec" in (report.document.title or "")
    assert report.run.provider == "mock"
    assert len(report.findings) >= 1
    assert report.actual_usage is not None
    assert report.findings[0].source_verified is True
    assert report.findings[0].source_verification_detail is not None

    json_path = tmp_path / "out.json"
    write_report_json(report, json_path)
    restored = load_report_json(json_path)
    assert restored.findings[0].id == report.findings[0].id
    assert restored.findings[0].source_verified is True

    md = report_to_markdown(report)
    assert "Time Assurance Scan Report" in md
    assert "Candidates for review" in md
    assert "source-verified candidate" in md
    assert report.findings[0].id in md
