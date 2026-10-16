# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Schema 0.5.0 request-batching provenance and legacy load tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tads.export import (
    load_report_json,
    report_to_json,
    report_to_markdown,
    write_report_json,
)
from tads.jsonutil import JsonLoadError
from tads.parsing.scope import AnalysisScope
from tads.pipeline import run_scan
from tads.schemas.report import (
    BATCHING_RUN_FIELDS,
    AnalysisMode,
    DocumentIdentity,
    Report,
    RequestBatching,
    RunMetadata,
)

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds.

2. Details

More text about epochs.
"""


def test_schema_050_round_trip(tmp_path: Path):
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=2,
            repair_request_count=1,
            chunk_section_counts=[3, 2],
            coverage_complete=True,
            sections_analyzed=5,
        ),
        findings=[],
    )
    path = tmp_path / "r.json"
    write_report_json(report, path)
    restored = load_report_json(path)
    assert restored.schema_version == "0.5.0"
    assert restored.run.request_batching == RequestBatching.SECTION_CHUNKS
    assert restored.run.analysis_request_count == 2
    assert restored.run.repair_request_count == 1
    assert restored.run.chunk_section_counts == [3, 2]
    assert restored.run.coverage_complete is True
    dumped = json.loads(report_to_json(restored))
    assert dumped["run"]["request_batching"] == "section_chunks"


def test_new_scan_writes_batching_fields():
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
        enforce_budget=False,
    )
    assert report.schema_version == "0.5.0"
    assert report.run.request_batching == RequestBatching.WHOLE_DOCUMENT
    assert report.run.analysis_request_count == 1
    assert report.run.repair_request_count == 0
    assert report.run.coverage_complete is True
    assert report.run.chunk_section_counts
    md = report_to_markdown(report)
    assert "Request batching" in md
    assert "whole_document" in md


def test_coverage_complete_false_when_max_sections_caps():
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        scope=AnalysisScope(max_sections=1),
        enforce_budget=False,
    )
    assert report.run.sections_analyzed == 1
    assert report.run.eligible_sections == 2
    assert report.run.coverage_complete is False
    md = report_to_markdown(report)
    assert "**Coverage complete:** no" in md


def test_coverage_complete_true_for_full_eligible():
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    assert report.run.eligible_sections == report.run.sections_analyzed
    assert report.run.coverage_complete is True


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0", "0.4.0"])
def test_legacy_batching_fields_remain_null_or_absent(version: str, tmp_path: Path):
    raw = {
        "schema_version": version,
        "document": {"corpus": "ietf", "doc_id": "RFC1"},
        "run": {
            "scanner_version": "0.6.0rc3",
            "analysis_mode": "section_aware",
            "sections_analyzed": 2,
            "prompt_framework_version": "0.8.1",
        },
        "findings": [],
    }
    path = tmp_path / f"legacy-{version}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    report = load_report_json(path)
    after = path.read_text(encoding="utf-8")
    assert after == before  # loading must not rewrite the source file
    assert report.schema_version == version
    for key in BATCHING_RUN_FIELDS:
        assert getattr(report.run, key) is None

    dumped = json.loads(report_to_json(report))
    assert dumped["schema_version"] == version
    run = dumped["run"]
    for key in BATCHING_RUN_FIELDS:
        assert key not in run

    md = report_to_markdown(report)
    assert "RFC1" in md
    assert "Request batching" not in md
    assert "Model requests" not in md
    assert "Coverage complete" not in md
    assert "Sections per request" not in md


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0", "0.4.0"])
def test_legacy_injected_batching_fields_stripped(version: str):
    raw = {
        "schema_version": version,
        "document": {"corpus": "ietf", "doc_id": "RFC1"},
        "run": {
            "scanner_version": "0.6.0rc3",
            "analysis_mode": "section_aware",
            "sections_analyzed": 2,
            "request_batching": "per_section",
            "analysis_request_count": 2,
            "repair_request_count": 0,
            "chunk_section_counts": [1, 1],
            "coverage_complete": True,
        },
        "findings": [],
    }
    report = Report.model_validate(raw)
    for key in BATCHING_RUN_FIELDS:
        assert getattr(report.run, key) is None
    dumped = json.loads(report_to_json(report))
    for key in BATCHING_RUN_FIELDS:
        assert key not in dumped["run"]


def test_schema_050_rejects_repair_gt_analysis():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.5.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
            run=RunMetadata(
                scanner_version="0.6.0rc3",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.PER_SECTION,
                analysis_request_count=1,
                repair_request_count=2,
                chunk_section_counts=[1],
                coverage_complete=True,
                sections_analyzed=1,
            ),
        )


def test_schema_050_rejects_bool_request_counts():
    with pytest.raises(ValidationError):
        Report.model_validate(
            {
                "schema_version": "0.5.0",
                "document": {"corpus": "ietf", "doc_id": "RFC1"},
                "run": {
                    "scanner_version": "0.6.0rc3",
                    "analysis_mode": "whole_document",
                    "request_batching": "whole_document",
                    "analysis_request_count": True,
                    "repair_request_count": False,
                    "chunk_section_counts": [1],
                    "coverage_complete": True,
                },
                "findings": [],
            }
        )


def test_schema_050_rejects_bool_in_chunk_counts():
    with pytest.raises(ValidationError):
        Report.model_validate(
            {
                "schema_version": "0.5.0",
                "document": {"corpus": "ietf", "doc_id": "RFC1"},
                "run": {
                    "scanner_version": "0.6.0rc3",
                    "analysis_mode": "section_aware",
                    "request_batching": "per_section",
                    "analysis_request_count": 1,
                    "repair_request_count": 0,
                    "chunk_section_counts": [True],
                    "coverage_complete": True,
                    "sections_analyzed": 1,
                },
                "findings": [],
            }
        )


def test_schema_050_rejects_count_len_mismatch():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.5.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
            run=RunMetadata(
                scanner_version="0.6.0rc3",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.SECTION_CHUNKS,
                analysis_request_count=2,
                repair_request_count=0,
                chunk_section_counts=[3],
                coverage_complete=True,
                sections_analyzed=3,
            ),
        )


def test_schema_050_rejects_section_chunks_all_singletons():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.5.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
            run=RunMetadata(
                scanner_version="0.6.0rc3",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.SECTION_CHUNKS,
                analysis_request_count=2,
                repair_request_count=0,
                chunk_section_counts=[1, 1],
                coverage_complete=True,
                sections_analyzed=2,
            ),
        )


def test_schema_050_rejects_per_section_with_multi():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.5.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
            run=RunMetadata(
                scanner_version="0.6.0rc3",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.PER_SECTION,
                analysis_request_count=1,
                repair_request_count=0,
                chunk_section_counts=[2],
                coverage_complete=True,
                sections_analyzed=2,
            ),
        )


def test_schema_050_rejects_sum_mismatch():
    with pytest.raises(ValidationError):
        Report(
            schema_version="0.5.0",
            document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
            run=RunMetadata(
                scanner_version="0.6.0rc3",
                analysis_mode=AnalysisMode.SECTION_AWARE,
                request_batching=RequestBatching.SECTION_CHUNKS,
                analysis_request_count=1,
                repair_request_count=0,
                chunk_section_counts=[2],
                coverage_complete=True,
                sections_analyzed=3,
            ),
        )


def test_schema_050_empty_section_aware_scope():
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.PER_SECTION,
            analysis_request_count=0,
            repair_request_count=0,
            chunk_section_counts=[],
            coverage_complete=True,
            sections_analyzed=0,
            eligible_sections=0,
        ),
    )
    assert report.run.analysis_request_count == 0
    assert report.run.chunk_section_counts == []


def test_contradictory_050_fails_controlled_load(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.5.0",
                "document": {"corpus": "ietf", "doc_id": "SECRET-DOC"},
                "run": {
                    "scanner_version": "0.6.0rc3",
                    "analysis_mode": "section_aware",
                    "request_batching": "whole_document",
                    "analysis_request_count": 1,
                    "repair_request_count": 0,
                    "chunk_section_counts": [1],
                    "coverage_complete": True,
                    "sections_analyzed": 1,
                },
                "findings": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JsonLoadError, match="Could not load report JSON") as exc_info:
        load_report_json(path)
    msg = str(exc_info.value)
    assert "SECRET-DOC" not in msg
    assert "whole_document" not in msg
    assert "request_batching" not in msg
