# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Schema 0.4.0 reasoning_effort / reasoning_tokens provenance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tads.export import load_report_json, report_to_json, report_to_markdown, write_report_json
from tads.llm.openai_models import GPT_6_LUNA
from tads.pipeline import plan_scan, run_scan
from tads.schemas.cost import TokenUsage
from tads.schemas.report import DocumentIdentity, Report, RunMetadata

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds until 2036-02-07.

2. Details

More text about epochs.
"""


def test_new_scan_writes_schema_040_and_null_effort_for_mock():
    report = run_scan(SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False)
    assert report.schema_version == "0.4.0"
    assert report.run.reasoning_effort is None
    dumped = json.loads(report_to_json(report))
    assert dumped["schema_version"] == "0.4.0"
    assert dumped["run"]["reasoning_effort"] is None
    md = report_to_markdown(report)
    assert "Reasoning effort" not in md


def test_reasoning_effort_round_trip(tmp_path: Path):
    report = Report(
        schema_version="0.4.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model=GPT_6_LUNA,
            reasoning_effort="low",
        ),
        actual_usage=TokenUsage(
            input_tokens=10, output_tokens=20, reasoning_tokens=7
        ),
    )
    path = tmp_path / "r.json"
    write_report_json(report, path)
    restored = load_report_json(path)
    assert restored.schema_version == "0.4.0"
    assert restored.run.reasoning_effort == "low"
    assert restored.actual_usage is not None
    assert restored.actual_usage.reasoning_tokens == 7
    assert restored.actual_usage.output_tokens == 20
    md = report_to_markdown(restored)
    assert "Reasoning effort" in md
    assert "`low`" in md
    assert "reasoning=7" in md


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_run_metadata_accepts_documented_efforts(effort: str):
    run = RunMetadata(scanner_version="0.6.0rc3", reasoning_effort=effort)
    assert run.reasoning_effort == effort


def test_run_metadata_rejects_invalid_effort():
    with pytest.raises(ValidationError):
        RunMetadata(scanner_version="0.6.0rc3", reasoning_effort="minimal")


def test_token_usage_rejects_bool_reasoning_tokens():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=1, output_tokens=1, reasoning_tokens=True)


def test_legacy_030_load_render_does_not_rewrite(tmp_path: Path):
    payload = {
        "schema_version": "0.3.0",
        "document": {"corpus": "ietf", "doc_id": "RFC868"},
        "run": {"scanner_version": "0.6.0rc3", "provider": "openai", "model": "gpt-4.1-mini"},
        "findings": [],
    }
    path = tmp_path / "old.json"
    original = json.dumps(payload, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    before = path.read_bytes()
    report = load_report_json(path)
    assert report.schema_version == "0.3.0"
    assert report.run.reasoning_effort is None
    report_to_markdown(report)
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0"])
def test_legacy_versions_load_with_null_effort(version: str, tmp_path: Path):
    payload = {
        "schema_version": version,
        "document": {"corpus": "ietf", "doc_id": "RFC868"},
        "run": {"scanner_version": "0.5.0"},
        "findings": [],
    }
    path = tmp_path / f"{version}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = load_report_json(path)
    assert report.schema_version == version
    assert report.run.reasoning_effort is None
    assert "Reasoning effort" not in report_to_markdown(report)


def test_plan_rejects_mock_reasoning_effort():
    with pytest.raises(Exception, match="reasoning_effort"):
        plan_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            reasoning_effort="low",
        )


def test_plan_records_luna_effort_in_notes():
    plan = plan_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="openai",
        model=GPT_6_LUNA,
        reasoning_effort=None,
    )
    assert any("reasoning_effort for this plan: none" in n for n in plan.cost_estimate.notes)
    assert any("estimate-based" in n or "completion tokens" in n for n in plan.cost_estimate.notes)
