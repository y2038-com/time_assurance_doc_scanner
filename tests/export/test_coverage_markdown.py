# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Markdown analysis-scope / coverage header tests."""

from datetime import datetime, timezone

from tads.export import eligible_coverage_percent, report_to_markdown
from tads.pipeline import run_scan
from tads.parsing.scope import AnalysisScope
from tads.schemas.report import (
    AnalysisMode,
    DocumentIdentity,
    PrivacyMode,
    Report,
    RunMetadata,
)


def _report(
    *,
    truncated: bool,
    eligible_chars: int | None,
    analyzed_chars: int | None,
    eligible_sections: int | None = 10,
    sections_analyzed: int | None = 10,
    analysis_mode: AnalysisMode = AnalysisMode.WHOLE_DOCUMENT,
    scope_notes: list[str] | None = None,
) -> Report:
    return Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC9999", title="Sample"),
        run=RunMetadata(
            scanner_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            provider="mock",
            model="mock-model",
            analysis_mode=analysis_mode,
            privacy_mode=PrivacyMode.EPHEMERAL,
            prompt_framework_version="0.5.0",
            scope_truncated=truncated,
            sections_total=12,
            sections_analyzed=sections_analyzed,
            eligible_sections=eligible_sections,
            eligible_chars=eligible_chars,
            analyzed_chars=analyzed_chars,
            document_chars=20_000,
            scope_notes=scope_notes or [],
        ),
        findings=[],
    )


def test_eligible_coverage_percent_clamped():
    run = RunMetadata(
        scanner_version="x",
        eligible_chars=100,
        analyzed_chars=150,  # pathological / legacy
    )
    assert eligible_coverage_percent(run) == 100.0


def test_markdown_complete_coverage_header():
    md = report_to_markdown(
        _report(truncated=False, eligible_chars=1000, analyzed_chars=1000)
    )
    assert md.startswith("# Time Assurance Scan Report: RFC9999\n")
    assert "(partial:" not in md.split("\n", 1)[0]
    assert "**Analysis scope:** COMPLETE" in md
    assert "**Coverage:** 100.0% of eligible text analyzed" in md
    assert "not a claim that every semantic aspect was assessed" in md


def test_markdown_partial_coverage_header_and_title_suffix():
    md = report_to_markdown(
        _report(
            truncated=True,
            eligible_chars=1000,
            analyzed_chars=124,
            sections_analyzed=2,
            eligible_sections=10,
            analysis_mode=AnalysisMode.SECTION_AWARE,
            scope_notes=["Capped to max_sections=2."],
        )
    )
    assert md.startswith(
        "# Time Assurance Scan Report: RFC9999 (partial: 12.4% of eligible text)"
    )
    assert "**Analysis scope:** PARTIAL" in md
    assert "**Coverage:** 12.4% of eligible text analyzed" in md
    assert "**Analysis mode:** section_aware" in md
    assert "Capped to max_sections=2." in md


def test_markdown_partial_without_char_totals_still_explicit():
    md = report_to_markdown(
        _report(truncated=True, eligible_chars=None, analyzed_chars=None)
    )
    assert "(partial analysis)" in md.split("\n", 1)[0]
    assert "**Analysis scope:** PARTIAL" in md
    assert "char totals unavailable" in md.lower() or "unavailable" in md


def test_run_scan_records_coverage_fields_and_partial_markdown():
    sample = "1 Intro\n\n" + ("word " * 200) + "\n\n2 More\n\n" + ("word " * 200)
    report = run_scan(
        sample,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        scope=AnalysisScope(max_sections=1),
    )
    assert report.run.scope_truncated is True
    assert report.run.eligible_chars is not None
    assert report.run.analyzed_chars is not None
    assert report.run.analyzed_chars < report.run.eligible_chars
    md = report_to_markdown(report)
    assert "**Analysis scope:** PARTIAL" in md
    assert "of eligible text analyzed" in md
    assert "(partial:" in md.split("\n", 1)[0]
