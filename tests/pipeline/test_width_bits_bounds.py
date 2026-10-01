# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Model-output width_bits type and range policy (Policy A)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from tads.cli import app
from tads.export import report_to_markdown
from tads.jsonutil import MAX_JSON_INT_DIGITS
from tads.llm.base import LLMResponse
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import (
    FindingParseError,
    extract_json_object,
    parse_findings_payload,
)
from tads.pipeline.validate import apply_horizon_validation
from tads.schemas.findings import Finding, FindingType, Severity
from tads.schemas.horizon import TimeRepresentationParams
from tads.schemas.model_output import (
    MAX_MODEL_WIDTH_BITS,
    MIN_MODEL_WIDTH_BITS,
    ModelTimeRepresentation,
)
from tads.schemas.report import AnalysisMode, DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence
from tads.validators.horizon import _MAX_WIDTH_BITS

SAMPLE = "1. Introduction\n\nTimestamps are 32-bit seconds.\n"
CANARY = "CANARY_WIDTH_BITS_xyz"
CPYTHON_LIMIT = "4300 digits"
HUGE_DIGIT_COUNT = 13067


def _digits(n: int) -> str:
    return "1" * n


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
        "evidence": [{"quote": "Timestamps are 32-bit seconds", "note": None}],
        "machine_interpretation": "Potential wrap.",
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


def _payload_with_width_token(token: str) -> str:
    text = json.dumps({"findings": [_valid_item()]})
    return text.replace('"width_bits": 32', '"width_bits": ' + token, 1)


class _CountingProvider(MockProvider):
    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content
        self.calls = 0

    def complete(self, messages, *, model=None, max_output_tokens=4096):
        self.calls += 1
        _ = (messages, model, max_output_tokens)
        return LLMResponse(content=self._content, model=model or self.default_model())


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


@pytest.mark.parametrize("width", [1, 16, 32, 64, 128, 256])
def test_ordinary_and_boundary_widths_are_accepted(width: int):
    item = _valid_item(time_representation={"width_bits": width, "unit": "seconds"})
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.time_representation is not None
    assert finding.time_representation.width_bits == width


def test_null_and_omitted_width_bits_mean_unknown():
    null_item = _valid_item(time_representation={"width_bits": None, "unit": "seconds"})
    assert (
        parse_findings_payload({"findings": [null_item]})[0].time_representation.width_bits
        is None
    )
    omitted = _valid_item(time_representation={"unit": "seconds"})
    assert (
        parse_findings_payload({"findings": [omitted]})[0].time_representation.width_bits
        is None
    )


@pytest.mark.parametrize("width", [0, -1, 257, 3200, 4096])
def test_out_of_range_width_bits_fails_policy_a(width: int):
    item = _valid_item(time_representation={"width_bits": width, "unit": "seconds"})
    with pytest.raises(FindingParseError) as exc_info:
        parse_findings_payload({"findings": [item]})
    text = str(exc_info.value)
    assert exc_info.value.repairable is False
    assert "width_bits" in text
    assert text != "Invalid JSON number"
    assert CANARY not in text
    if width != 0:
        assert str(width) not in text


@pytest.mark.parametrize("value", [True, False, "32", 32.0])
def test_non_integer_width_bits_rejected(value: object):
    item = _valid_item(time_representation={"width_bits": value, "unit": "seconds"})
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid type field width_bits"):
        parse_findings_payload({"findings": [item]})


def test_forty_digit_width_bits_decodes_then_policy_a_rejects():
    token = _digits(MAX_JSON_INT_DIGITS)
    assert len(token) == 40
    data = extract_json_object(_payload_with_width_token(token))
    decoded = data["findings"][0]["time_representation"]["width_bits"]
    assert isinstance(decoded, int)
    assert decoded != 0
    with pytest.raises(FindingParseError) as exc_info:
        parse_findings_payload(data)
    text = str(exc_info.value)
    assert exc_info.value.repairable is False
    assert "width_bits" in text
    assert text != "Invalid JSON number"
    assert token not in text


@pytest.mark.parametrize("count", [MAX_JSON_INT_DIGITS + 1, HUGE_DIGIT_COUNT])
def test_overlong_width_bits_rejected_by_decoder(count: int):
    token = _digits(count)
    assert len(token) == count
    payload = _payload_with_width_token(token)
    with pytest.raises(FindingParseError) as exc_info:
        extract_json_object(payload)
    assert str(exc_info.value) == "Invalid JSON number"
    assert exc_info.value.repairable is False
    assert token not in str(exc_info.value)
    assert CPYTHON_LIMIT not in str(exc_info.value)
    assert str(count) not in str(exc_info.value)


def test_range_failure_does_not_invoke_repair_or_write_reports(
    tmp_path: Path, restore_mock_provider
):
    content = json.dumps({"findings": [_valid_item(time_representation={"width_bits": 3200, "unit": "seconds"})]})
    provider = _CountingProvider(content)
    register_provider(provider)
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
    assert provider.calls == 1
    assert "3200" not in result.output
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()


def test_decoder_failure_does_not_invoke_repair_or_write_reports(
    tmp_path: Path, restore_mock_provider
):
    token = _digits(HUGE_DIGIT_COUNT)
    content = _payload_with_width_token(token)
    provider = _CountingProvider(content)
    register_provider(provider)
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
    assert provider.calls == 1
    assert "Invalid JSON number" in result.output
    assert token not in result.output
    assert CPYTHON_LIMIT not in result.output
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()


def test_forty_digit_scan_does_not_repair(restore_mock_provider):
    token = _digits(40)
    provider = _CountingProvider(_payload_with_width_token(token))
    register_provider(provider)
    with pytest.raises(FindingParseError) as exc_info:
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
        )
    assert provider.calls == 1
    assert exc_info.value.repairable is False
    assert token not in str(exc_info.value)


def test_model_dto_strict_integer_rejects_boolean():
    with pytest.raises(ValidationError):
        ModelTimeRepresentation(width_bits=True)
    with pytest.raises(ValidationError):
        ModelTimeRepresentation(width_bits=False)
    parsed = ModelTimeRepresentation(width_bits=32)
    assert parsed.width_bits == 32


def test_parser_bounds_are_independent_of_horizon_calculator_cap():
    assert MIN_MODEL_WIDTH_BITS == 1
    assert MAX_MODEL_WIDTH_BITS == 256
    assert _MAX_WIDTH_BITS == 128
    item = _valid_item(
        time_representation={"width_bits": 256, "signed": True, "unit": "seconds"}
    )
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.time_representation is not None
    assert finding.time_representation.width_bits == 256
    out = apply_horizon_validation(finding)
    assert out.horizon_validation is not None
    assert out.horizon_validation.status == "error"


def test_public_report_model_remains_unbounded():
    params = TimeRepresentationParams(width_bits=10000)
    assert params.width_bits == 10000
    finding = Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Legacy wide counter",
        description="Historical report with an unbounded stored width.",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        machine_interpretation="Stored width only.",
        time_representation=params,
    )
    restored = Finding.model_validate(finding.model_dump())
    assert restored.time_representation is not None
    assert restored.time_representation.width_bits == 10000
    rendered = apply_horizon_validation(restored)
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC1"),
        run=RunMetadata(scanner_version="0.6.0rc3"),
        findings=[rendered],
    )
    markdown = report_to_markdown(report)
    assert "10000 bits" in markdown


def test_gemini_rfc868_concatenated_width_bits_fails_policy_a():
    payload = json.loads(
        (
            Path(__file__).resolve().parent
            / "fixtures"
            / "gemini_rfc868_concatenated_width_bits.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["findings"][0]["time_representation"]["width_bits"] == 3200
    with pytest.raises(FindingParseError) as exc_info:
        parse_findings_payload(payload)
    text = str(exc_info.value)
    assert exc_info.value.repairable is False
    assert "width_bits" in text
    assert text != "Invalid JSON number"


def test_dto_field_uses_shared_width_maximum():
    fragment = ModelTimeRepresentation.model_json_schema()["properties"]["width_bits"]
    integer = next(part for part in fragment["anyOf"] if part.get("type") == "integer")
    assert integer["minimum"] == MIN_MODEL_WIDTH_BITS
    assert integer["maximum"] == MAX_MODEL_WIDTH_BITS
    assert MAX_MODEL_WIDTH_BITS == 256
