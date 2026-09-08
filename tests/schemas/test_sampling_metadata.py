# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""RunMetadata sampling / reproducibility field tests."""

from tads.export import load_report_json, write_report_json
from tads.llm.base import DEFAULT_LLM_TEMPERATURE
from tads.pipeline import run_scan
from tads.schemas.report import RunMetadata


SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds. The era advances after 2036-02-07.

2. Details

More text.
"""


def test_run_scan_records_sampling_parameters(tmp_path):
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        max_output_tokens=2048,
    )
    assert report.run.temperature == DEFAULT_LLM_TEMPERATURE
    assert report.run.max_output_tokens == 2048
    assert report.run.provider == "mock"
    assert report.run.model
    assert report.run.prompt_framework_version
    assert report.run.scanner_version

    path = tmp_path / "r.json"
    write_report_json(report, path)
    restored = load_report_json(path)
    assert restored.run.temperature == DEFAULT_LLM_TEMPERATURE
    assert restored.run.max_output_tokens == 2048


def test_legacy_run_metadata_omits_sampling_fields():
    run = RunMetadata(scanner_version="0.4.0")
    assert run.temperature is None
    assert run.max_output_tokens is None
    dumped = run.model_dump()
    assert dumped["temperature"] is None
    assert dumped["max_output_tokens"] is None
