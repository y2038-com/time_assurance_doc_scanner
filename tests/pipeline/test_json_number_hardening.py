# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Bounded JSON-number decoding for untrusted model, report, and label input."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.eval import load_labels
from tads.export import load_report_json, write_report_json
from tads.jsonutil import (
    MAX_JSON_FLOAT_CHARS,
    MAX_JSON_INT_DIGITS,
    JsonLoadError,
    JsonNumberError,
    loads,
)
from tads.llm.base import LLMResponse
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import FindingParseError, extract_json_object
from tads.schemas.report import AnalysisMode, DocumentIdentity, Report, RunMetadata

SAMPLE = "1. Introduction\n\nTimestamps are 32-bit seconds.\n"
CANARY = "CANARY_JSON_NUMBER_xyz"
CPYTHON_LIMIT = "4300 digits"


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
            "ticks_per_second": 1.5,
            "claimed_horizon": {"value": "2036-02-07", "precision": "day"},
            "rollover_behavior": "wrap",
        },
    }
    item.update(overrides)
    return item


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


def _assert_number_error(exc: BaseException) -> None:
    assert isinstance(exc, FindingParseError)
    assert exc.repairable is False
    assert str(exc) == "Invalid JSON number"
    assert CPYTHON_LIMIT not in str(exc)
    assert "7365" not in str(exc)
    assert CANARY not in str(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None


def test_decoder_accepts_ordinary_integers_and_floats():
    data = loads('{"n": 0, "neg": -1, "width": 32, "usage": 4096, "bits": -0}')
    assert data["n"] == 0
    assert data["neg"] == -1
    assert data["width"] == 32
    assert data["usage"] == 4096
    assert data["bits"] == 0
    floats = loads('{"a": 1.5, "b": 2.5e1, "c": -0.0}')
    assert floats["a"] == 1.5
    assert floats["b"] == 25.0
    assert floats["c"] == 0.0
    assert math.copysign(1.0, floats["c"]) == -1.0


def test_decoder_accepts_limit_integer_and_rejects_over():
    assert loads(_digits(MAX_JSON_INT_DIGITS)) == int(_digits(MAX_JSON_INT_DIGITS))
    assert loads("-" + _digits(MAX_JSON_INT_DIGITS)) == -int(
        _digits(MAX_JSON_INT_DIGITS)
    )
    with pytest.raises(JsonNumberError):
        loads(_digits(MAX_JSON_INT_DIGITS + 1))
    with pytest.raises(JsonNumberError):
        loads("-" + _digits(MAX_JSON_INT_DIGITS + 1))


@pytest.mark.parametrize("count", [4300, 4301, 7365])
def test_decoder_rejects_cpython_scale_integers(count):
    with pytest.raises(JsonNumberError):
        loads(_digits(count))
    with pytest.raises(JsonNumberError):
        loads("-" + _digits(count))


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_decoder_rejects_nonstandard_constants(token):
    with pytest.raises(JsonNumberError):
        loads(token)


def test_decoder_rejects_non_finite_exponent_and_overlong_float():
    with pytest.raises(JsonNumberError):
        loads("1e999999")
    with pytest.raises(JsonNumberError):
        loads("1." + ("0" * MAX_JSON_FLOAT_CHARS))
    with pytest.raises(JsonNumberError):
        loads("1e" + ("0" * MAX_JSON_FLOAT_CHARS))


def test_duplicate_keys_only_when_requested():
    text = '{"a": 1, "a": 2}'
    assert loads(text)["a"] == 2
    with pytest.raises(Exception):
        loads(text, reject_duplicate_keys=True)


def test_extract_rejects_huge_integer_in_finding_fields():
    payload = (
        '{"findings":[{"finding_type":"time_assurance_gap","title":"x",'
        '"width_placeholder":'
        + _digits(7365)
        + "}]}"
    )
    with pytest.raises(FindingParseError) as exc_info:
        extract_json_object(payload)
    _assert_number_error(exc_info.value)
    assert exc_info.value.raw == payload


def test_extract_rejects_huge_integer_in_evidence_and_time_rep():
    evidence = (
        '{"findings":[{"evidence":[{"quote":"q","line":'
        + _digits(41)
        + "}]}]}"
    )
    time_rep = (
        '{"findings":[{"time_representation":{"width_bits":'
        + _digits(41)
        + "}}]}"
    )
    for payload in (evidence, time_rep):
        with pytest.raises(FindingParseError) as exc_info:
            extract_json_object(payload)
        _assert_number_error(exc_info.value)


def test_extract_rejects_nan_and_infinity_without_repair_flag():
    for token in ("NaN", "Infinity", "-Infinity", "1e999999"):
        payload = '{"findings":[],"n":' + token + "}"
        with pytest.raises(FindingParseError) as exc_info:
            extract_json_object(payload)
        _assert_number_error(exc_info.value)


def test_local_trailing_comma_repair_still_works():
    assert extract_json_object('{"findings": [],}') == {"findings": []}


def test_numeric_failure_does_not_invoke_llm_repair(restore_mock_provider):
    content = '{"findings":[],"n":' + _digits(7365) + "}"
    provider = _CountingProvider(content)
    register_provider(provider)
    with pytest.raises(FindingParseError) as exc_info:
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
        )
    _assert_number_error(exc_info.value)
    assert provider.calls == 1


def test_ordinary_envelope_repair_still_invokes_second_complete(restore_mock_provider):
    class RepairProvider(MockProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def complete(self, messages, *, model=None, max_output_tokens=4096):
            self.calls += 1
            _ = (messages, model, max_output_tokens)
            if self.calls == 1:
                return LLMResponse(content="not json " + CANARY)
            return LLMResponse(content='{"findings": []}')

    provider = RepairProvider()
    register_provider(provider)
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
    )
    assert provider.calls == 2
    assert report.findings == []


def test_numeric_failure_writes_raw_only_when_opted_in(
    tmp_path: Path, restore_mock_provider
):
    content = '{"findings":[],"n":' + _digits(41) + "}"
    register_provider(_CountingProvider(content))
    dest = tmp_path / "diag.raw.txt"
    with pytest.raises(FindingParseError):
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
            save_raw_on_error=str(dest),
        )
    assert dest.is_file()
    assert content in dest.read_text(encoding="utf-8")
    if os.name == "posix":
        assert (dest.stat().st_mode & 0o777) == 0o600

    register_provider(_CountingProvider(content))
    skipped = tmp_path / "skipped.raw.txt"
    with pytest.raises(FindingParseError):
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
        )
    assert not skipped.exists()


def test_numeric_scan_cli_writes_no_reports(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_CountingProvider('{"findings":[],"n":' + _digits(41) + "}"))
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
    assert "Invalid JSON number" in result.output
    assert CPYTHON_LIMIT not in result.output
    assert _digits(41) not in result.output
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()


def _minimal_report() -> Report:
    return Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(scanner_version="0.6.0rc3"),
        findings=[],
    )


def test_report_load_rejects_huge_integer(tmp_path: Path):
    path = tmp_path / "r.json"
    write_report_json(_minimal_report(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = json.dumps(data).replace('"findings": []', '"findings": [], "n": ' + _digits(41))
    path.write_text(raw + "\n", encoding="utf-8")
    with pytest.raises(JsonLoadError, match="Could not load report JSON") as exc_info:
        load_report_json(path)
    assert exc_info.value.__cause__ is None
    assert _digits(41) not in str(exc_info.value)
    assert CPYTHON_LIMIT not in str(exc_info.value)


def test_report_render_cli_rejects_huge_integer_without_writing_md(tmp_path: Path):
    path = tmp_path / "r.json"
    write_report_json(_minimal_report(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(data).replace('"findings": []', '"findings": [], "n": ' + _digits(4300)),
        encoding="utf-8",
    )
    md = tmp_path / "r.md"
    result = CliRunner().invoke(app, ["render", str(path), "-o", str(md), "--overwrite"])
    assert result.exit_code != 0
    assert "Could not load report JSON" in result.output
    assert not md.exists()
    assert _digits(40) not in result.output


def test_eval_labels_reject_huge_integer(tmp_path: Path):
    path = tmp_path / "labels.json"
    path.write_text('{"findings":[{"id":' + _digits(41) + "}]}\n", encoding="utf-8")
    with pytest.raises(JsonLoadError, match="Could not load evaluation labels") as exc_info:
        load_labels(path)
    assert _digits(41) not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_safe_decode_prevents_oversized_int_reaching_dumps():
    with pytest.raises(JsonNumberError):
        loads('{"width_bits":' + _digits(41) + "}")
    accepted = loads('{"width_bits":' + _digits(40) + "}")
    dumped = json.dumps(accepted)
    assert _digits(40) in dumped
    assert _digits(41) not in dumped
