# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Schema 0.6.0 adaptive-recovery provenance and legacy load tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tads.export import load_report_json, report_to_json, report_to_markdown, write_report_json
from tads.schemas.report import (
    CURRENT_REPORT_SCHEMA,
    AnalysisMode,
    AnalysisStatus,
    DocumentIdentity,
    Report,
    RequestBatching,
    RunMetadata,
    UnanalyzedSection,
)


def test_new_report_default_schema_is_060():
    assert Report.model_fields["schema_version"].default == CURRENT_REPORT_SCHEMA
    assert CURRENT_REPORT_SCHEMA == "0.6.0"


def test_schema_060_round_trip(tmp_path: Path):
    report = Report(
        schema_version="0.6.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(
            scanner_version="0.6.0",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=3,
            repair_request_count=0,
            chunk_section_counts=[8, 7],
            coverage_complete=False,
            sections_analyzed=15,
            analyzed_chars=110,
            analysis_status=AnalysisStatus.PARTIAL,
            sections_succeeded=14,
            succeeded_chars=100,
            sections_failed=1,
            failed_chars=10,
            failed_section_ids=["s-15"],
            unanalyzed_sections=[
                UnanalyzedSection(
                    section_id="s-15",
                    category="invalid_model_output",
                )
            ],
            recovery_split_count=1,
            recovery_request_count=2,
        ),
        findings=[],
    )
    path = tmp_path / "r.json"
    write_report_json(report, path)
    restored = load_report_json(path)
    assert restored.schema_version == "0.6.0"
    assert restored.run.analysis_status == AnalysisStatus.PARTIAL
    assert restored.run.failed_section_ids == ["s-15"]
    dumped = json.loads(report_to_json(restored))
    assert dumped["run"]["analysis_status"] == "partial"
    assert dumped["run"]["coverage_complete"] is False
    md = report_to_markdown(restored)
    assert "PARTIAL" in md
    assert "s-15" in md


def test_legacy_050_strips_recovery_fields(tmp_path: Path):
    payload = {
        "schema_version": "0.5.0",
        "document": {"corpus": "ietf", "doc_id": "RFC9999"},
        "run": {
            "scanner_version": "0.6.0",
            "analysis_mode": "section_aware",
            "request_batching": "section_chunks",
            "analysis_request_count": 2,
            "repair_request_count": 0,
            "chunk_section_counts": [3, 2],
            "coverage_complete": True,
            "sections_analyzed": 5,
            "analysis_status": "partial",
            "sections_failed": 9,
            "failed_section_ids": ["invented"],
            "recovery_request_count": 4,
        },
        "findings": [],
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = load_report_json(path)
    assert restored.schema_version == "0.5.0"
    assert restored.run.analysis_status is None
    assert restored.run.failed_section_ids is None
    assert restored.run.recovery_request_count is None
    dumped = json.loads(report_to_json(restored))
    assert "analysis_status" not in dumped["run"]
    assert "failed_section_ids" not in dumped["run"]
    md = report_to_markdown(restored)
    assert "Unanalyzed sections" not in md


def test_partial_cannot_claim_coverage_complete():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=RunMetadata(
                scanner_version="0.6.0",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.PER_SECTION,
                analysis_request_count=1,
                repair_request_count=0,
                chunk_section_counts=[1],
                coverage_complete=True,
                sections_analyzed=1,
                analysis_status=AnalysisStatus.PARTIAL,
                sections_succeeded=0,
                succeeded_chars=0,
                sections_failed=1,
                failed_chars=1,
                failed_section_ids=["s-1"],
                unanalyzed_sections=[
                    UnanalyzedSection(
                        section_id="s-1", category="invalid_model_output"
                    )
                ],
                recovery_split_count=0,
                recovery_request_count=0,
            ),
            findings=[],
        )


def test_schema_060_allows_analysis_count_not_equal_chunks():
    report = Report(
        schema_version="0.6.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(
            scanner_version="0.6.0",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=3,
            repair_request_count=0,
            chunk_section_counts=[8, 7],
            coverage_complete=True,
            sections_analyzed=15,
            analyzed_chars=100,
            analysis_status=AnalysisStatus.COMPLETE,
            sections_succeeded=15,
            succeeded_chars=100,
            sections_failed=0,
            failed_chars=0,
            failed_section_ids=[],
            unanalyzed_sections=[],
            recovery_split_count=1,
            recovery_request_count=2,
        ),
        findings=[],
    )
    assert report.run.analysis_request_count != len(report.run.chunk_section_counts)
    assert report.run.total_provider_request_count == 3


def _partial_run(**overrides):
    fields = dict(
        scanner_version="0.6.0",
        analysis_mode=AnalysisMode.SECTION_AWARE,
        request_batching=RequestBatching.SECTION_CHUNKS,
        analysis_request_count=3,
        repair_request_count=0,
        total_provider_request_count=3,
        chunk_section_counts=[2, 2],
        coverage_complete=False,
        sections_analyzed=4,
        analyzed_chars=40,
        analysis_status=AnalysisStatus.PARTIAL,
        sections_succeeded=3,
        succeeded_chars=30,
        sections_failed=1,
        failed_chars=10,
        failed_section_ids=["s-4"],
        unanalyzed_sections=[
            UnanalyzedSection(section_id="s-4", category="invalid_model_output")
        ],
        recovery_split_count=1,
        recovery_request_count=2,
    )
    fields.update(overrides)
    return RunMetadata(**fields)


def test_schema_060_rejects_char_sum_mismatch():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=_partial_run(analyzed_chars=99),
            findings=[],
        )


def test_schema_060_rejects_duplicate_failed_section_ids():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=_partial_run(
                sections_failed=2,
                failed_chars=20,
                succeeded_chars=20,
                sections_succeeded=2,
                failed_section_ids=["s-3", "s-3"],
                unanalyzed_sections=[
                    UnanalyzedSection(
                        section_id="s-3", category="invalid_model_output"
                    ),
                    UnanalyzedSection(
                        section_id="s-3", category="truncated_output"
                    ),
                ],
            ),
            findings=[],
        )


def test_schema_060_rejects_recovery_count_above_total():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=_partial_run(recovery_request_count=9),
            findings=[],
        )


def test_schema_060_rejects_complete_status_with_failed_sections():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=_partial_run(analysis_status=AnalysisStatus.COMPLETE),
            findings=[],
        )


def test_schema_060_rejects_total_not_equal_analysis_plus_repair():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.6.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
            run=_partial_run(total_provider_request_count=1),
            findings=[],
        )


def test_schema_060_complete_with_incomplete_eligible_coverage():
    report = Report(
        schema_version="0.6.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(
            scanner_version="0.6.0",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.PER_SECTION,
            analysis_request_count=1,
            repair_request_count=0,
            chunk_section_counts=[1],
            coverage_complete=False,
            sections_analyzed=1,
            analyzed_chars=10,
            eligible_chars=50,
            analysis_status=AnalysisStatus.COMPLETE,
            sections_succeeded=1,
            succeeded_chars=10,
            sections_failed=0,
            failed_chars=0,
            failed_section_ids=[],
            unanalyzed_sections=[],
            recovery_split_count=0,
            recovery_request_count=0,
        ),
        findings=[],
    )
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert report.run.coverage_complete is False
    assert report.run.total_provider_request_count == 1


def test_schema_060_fills_total_from_analysis_plus_repair():
    report = Report(
        schema_version="0.6.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=_partial_run(total_provider_request_count=None),
        findings=[],
    )
    assert report.run.total_provider_request_count == 3


def test_legacy_050_strips_total_provider_request_count(tmp_path: Path):
    payload = {
        "schema_version": "0.5.0",
        "document": {"corpus": "ietf", "doc_id": "RFC9999"},
        "run": {
            "scanner_version": "0.6.0",
            "analysis_mode": "section_aware",
            "request_batching": "section_chunks",
            "analysis_request_count": 2,
            "repair_request_count": 0,
            "total_provider_request_count": 99,
            "chunk_section_counts": [3, 2],
            "coverage_complete": True,
            "sections_analyzed": 5,
        },
        "findings": [],
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = load_report_json(path)
    assert restored.schema_version == "0.5.0"
    assert restored.run.total_provider_request_count is None
    dumped = json.loads(report_to_json(restored))
    assert "total_provider_request_count" not in dumped["run"]
    assert "analysis_status" not in dumped["run"]
