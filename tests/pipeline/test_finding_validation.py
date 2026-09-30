# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Adversarial Policy A finding-validation tests (mock provider only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.llm.base import ChatMessage, LLMResponse
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import FindingParseError, extract_json_object
from tads.schemas.findings import Disposition, ValidationStatus
from tads.schemas.report import AnalysisMode

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds.

2. Details

More text about epochs.

3. Appendix

Closing notes about eras.
"""

CANARY = "CANARY_SECRET_xyz_not_in_logs"


def _valid_item(**overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Era wrap guidance may be incomplete",
        "description": "Document discusses 32-bit seconds and eras.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["y2036", "rollover"],
        "section_id": "model-section",
        "section_title": "Model Title",
        "evidence": [
            {
                "quote": "Timestamps are 32-bit seconds",
                "note": "Representation width mentioned",
            }
        ],
        "machine_interpretation": "Potential Y2036-related assurance gap.",
        "recommendation_level1": "Clarify era rollover and supported operational horizon.",
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
            "claimed_horizon": "2036-02-07",
            "rollover_behavior": "wrap",
        },
    }
    item.update(overrides)
    return item


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


class _JsonProvider(MockProvider):
    def __init__(self, payload) -> None:
        super().__init__()
        self._payload = payload
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages, *, model=None, max_output_tokens=4096):
        self.calls.append(list(messages))
        content = (
            self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        )
        return LLMResponse(content=content, model=model or self.default_model())


class _SequencedProvider(MockProvider):
    def __init__(self, payloads: list) -> None:
        super().__init__()
        self._payloads = payloads
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages, *, model=None, max_output_tokens=4096):
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self._payloads) - 1)
        payload = self._payloads[index]
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return LLMResponse(content=content, model=model or self.default_model())


def test_extract_rejects_duplicate_object_keys():
    with pytest.raises(FindingParseError, match="Duplicate JSON object key"):
        extract_json_object('{"findings": [], "findings": []}')
    with pytest.raises(FindingParseError, match="Duplicate JSON object key"):
        extract_json_object(
            '{"findings": [{"finding_type": "time_assurance_gap", '
            '"finding_type": "explicit_defect"}]}'
        )


def test_genuine_empty_findings_completes(restore_mock_provider):
    provider = _JsonProvider({"findings": []})
    register_provider(provider)
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
    )
    assert report.findings == []
    assert len(provider.calls) == 1


def test_canonical_mock_output_still_promotes_tads_fields_only(restore_mock_provider):
    report = run_scan(SAMPLE, doc_id="RFC9999", provider="mock")
    assert report.findings
    finding = report.findings[0]
    assert finding.disposition == Disposition.NEW or finding.disposition.value == "new"
    assert finding.id.startswith("F-")
    assert finding.scope_relevance.value == "core"
    assert finding.time_representation is not None


def test_item_failure_does_not_invoke_repair(restore_mock_provider):
    provider = _JsonProvider({"findings": [_valid_item(severity="banana")]})
    register_provider(provider)
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
        )
    assert len(provider.calls) == 1


def test_repair_returning_malformed_items_fails_closed(restore_mock_provider):
    provider = _SequencedProvider(
        [
            "Here you go:\n```json\n{\"findings\": []}\n```",
            {"findings": [_valid_item(), _valid_item(severity="banana")]},
        ]
    )
    register_provider(provider)
    with pytest.raises(FindingParseError, match=r"Finding item 1:"):
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
        )
    assert len(provider.calls) == 2


def test_whole_document_and_section_aware_item_failure_parity(
    restore_mock_provider,
):
    payload = {"findings": [_valid_item(title="ok"), _valid_item(severity="banana")]}
    for mode in (AnalysisMode.WHOLE_DOCUMENT, AnalysisMode.SECTION_AWARE):
        register_provider(_JsonProvider(payload))
        with pytest.raises(FindingParseError, match=r"Finding item 1:"):
            run_scan(
                SAMPLE,
                doc_id="RFC9999",
                provider="mock",
                force_mode=mode,
            )


def test_section_aware_uses_tads_locators(restore_mock_provider):
    register_provider(_JsonProvider({"findings": [_valid_item()]}))
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert report.findings
    loc = report.findings[0].location
    assert loc is not None
    assert loc.section_id != "model-section"
    assert loc.section_title != "Model Title"


def test_section_aware_model_cannot_redirect_to_another_section(
    restore_mock_provider,
):
    from tads.pipeline import plan_scan

    plan = plan_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert len(plan.scoped.sections) >= 2
    first, second = plan.scoped.sections[0], plan.scoped.sections[1]
    spoofed = _valid_item(section_id=second.id, section_title=second.title)
    register_provider(_JsonProvider({"findings": [spoofed]}))
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    located = [f for f in report.findings if f.location and f.location.section_id]
    assert located
    first_finding = next(
        f for f in report.findings if f.location.section_id == first.id
    )
    assert first_finding.location.section_title == first.title
    assert first_finding.location.section_id != second.id


def test_whole_document_keeps_model_section_strings(restore_mock_provider):
    register_provider(_JsonProvider({"findings": [_valid_item()]}))
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
    )
    loc = report.findings[0].location
    assert loc is not None
    assert loc.section_id == "model-section"
    assert loc.section_title == "Model Title"


@pytest.mark.parametrize("bad_index", [0, 1, 2])
def test_item_failure_in_first_middle_last_section(
    bad_index: int, restore_mock_provider
):
    from tads.pipeline import plan_scan

    plan = plan_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert len(plan.scoped.sections) >= 3
    empty = {"findings": []}
    bad = {"findings": [_valid_item(severity="banana")]}
    payloads = [empty, empty, empty]
    payloads[bad_index] = bad
    register_provider(_SequencedProvider(payloads))
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )


def test_item_failure_writes_raw_on_error_and_omits_canary_from_errors(
    tmp_path: Path, restore_mock_provider
):
    payload = {"findings": [_valid_item(title=CANARY, severity="banana")]}
    register_provider(_JsonProvider(payload))
    raw = tmp_path / "fail.raw.txt"
    seen: list[str] = []
    with pytest.raises(FindingParseError) as exc:
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
            save_raw_on_error=str(raw),
            on_progress=seen.append,
        )
    assert raw.is_file()
    raw_text = raw.read_text(encoding="utf-8")
    assert CANARY in raw_text
    joined = "\n".join(seen) + str(exc.value)
    assert CANARY not in joined
    assert "Finding item 0" in str(exc.value)
    assert "RFC9999" not in str(exc.value)


def test_item_failure_does_not_write_reports(tmp_path: Path, restore_mock_provider):
    register_provider(_JsonProvider({"findings": [_valid_item(severity="banana")]}))
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()
    assert CANARY not in result.output


def test_overwrite_then_item_failure_leaves_existing_reports(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_JsonProvider({"findings": [_valid_item(severity="banana")]}))
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"
    json_path.write_text('{"keep": "json"}', encoding="utf-8")
    md_path.write_text("keep markdown", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    assert json_path.read_text(encoding="utf-8") == '{"keep": "json"}'
    assert md_path.read_text(encoding="utf-8") == "keep markdown"


def test_nullable_fields_scan_still_writes_reports(
    tmp_path: Path, restore_mock_provider
):
    item = _valid_item(section_id=None, section_title=None)
    del item["time_representation"]
    del item["recommendation_level1"]
    register_provider(_JsonProvider({"findings": [item]}))
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert Path(str(out) + ".json").is_file()
    assert Path(str(out) + ".md").is_file()


def test_tads_owned_fields_stay_at_defaults_on_valid_items():
    from tads.pipeline.parse_findings import parse_findings_payload

    finding = parse_findings_payload({"findings": [_valid_item()]})[0]
    assert finding.disposition == Disposition.NEW
    assert finding.validation_status == ValidationStatus.UNVERIFIED
    assert finding.source_verified is False
    assert finding.horizon_validation is None
    assert finding.reviewer_notes is None
