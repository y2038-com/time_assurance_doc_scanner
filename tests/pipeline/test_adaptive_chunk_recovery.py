# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Bounded adaptive section-chunk recovery (offline, mock providers only)."""

from __future__ import annotations

import inspect
import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app, scan_cmd
from tads.errors import GENERIC_SCAN_FAILURE, ProviderRequestError
from tads.export import report_to_json, report_to_markdown
from tads.llm.base import ChatMessage, LLMResponse, ModelOutputTruncatedError
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import FindingParseError
from tads.pipeline.recovery import (
    DEFAULT_MAX_RECOVERY_REQUESTS,
    MAX_MAX_RECOVERY_REQUESTS,
    RawDiagnosticSink,
    bisect_bindings,
    is_recoverable_model_output_failure,
)
from tads.schemas.report import AnalysisMode, AnalysisStatus, RequestBatching

CANARY = "RECOVERY_SECRET_CANARY_xyz"
BODY_CANARY = "RECOVERY_DOC_BODY_CANARY_xyz"
URL_CANARY = "https://evil.example/steal?token=abc"


def _doc(n: int) -> str:
    parts = [
        f"{i}. Section {i}\n\n"
        f"Unique quote {i:02d} timestamps are 32-bit seconds for tests.\n"
        for i in range(1, n + 1)
    ]
    return "\n".join(parts)


def _quote(i: int) -> str:
    return f"Unique quote {i:02d} timestamps are 32-bit seconds for tests."


def _item(section_id: str, quote: str, title: str = "Gap", **overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": title,
        "description": "A synthetic time assurance gap for recovery tests.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["persistence"],
        "section_id": section_id,
        "section_title": None,
        "evidence": [{"quote": quote, "note": None}],
        "machine_interpretation": "Synthetic interpretation for recovery tests.",
        "recommendation_level1": "Document the synthetic boundary clearly.",
        "scope_relevance": "core",
        "scope_rationale": (
            "This matters to time assurance because synthetic ordering affects replay."
        ),
        "time_representation": None,
    }
    item.update(overrides)
    return item


def _ok(ref: str, section_number: int, title: str | None = None) -> dict:
    return {
        "findings": [
            _item(
                ref,
                _quote(section_number),
                title=title or f"Finding {ref}",
            )
        ]
    }


def _invalid(title: str = "PARENT-SHOULD-NOT-SURVIVE") -> dict:
    return {
        "findings": [
            {
                "finding_type": "time_assurance_gap",
                "title": title,
            }
        ]
    }


EMPTY = {"findings": []}


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


class _RecordingProvider(MockProvider):
    def __init__(self, payloads: list) -> None:
        super().__init__()
        self._payloads = list(payloads)
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self,
        messages,
        *,
        model=None,
        max_output_tokens=4096,
        reasoning_effort=None,
    ):
        from tads.llm.base import reject_unsupported_reasoning_effort
        from tads.schemas.cost import TokenUsage

        reject_unsupported_reasoning_effort(reasoning_effort)
        self.calls.append(list(messages))
        idx = min(len(self.calls) - 1, len(self._payloads) - 1)
        payload = self._payloads[idx]
        if isinstance(payload, BaseException):
            raise payload
        if callable(payload):
            content = payload(messages)
        elif isinstance(payload, str):
            content = payload
        else:
            content = json.dumps(payload)
        return LLMResponse(
            content=content,
            model=model or self.default_model(),
            usage=TokenUsage(
                input_tokens=11 + len(self.calls),
                output_tokens=7 + len(self.calls),
                reasoning_tokens=len(self.calls),
            ),
        )


def test_bisect_is_nonempty_contiguous_and_balanced():
    from tads.parsing.chunks import SectionBinding
    from tads.parsing.document import Section

    def _bindings(n: int):
        out = []
        for i in range(n):
            section = Section(id=f"s-{i+1}", title=f"{i+1}", text="x", level=1)
            out.append(
                SectionBinding(
                    opaque_ref=f"S{i+1:04d}",
                    section=section,
                    canonical_id=section.id,
                    canonical_title=section.title,
                )
            )
        return tuple(out)

    left, right = bisect_bindings(_bindings(8))
    assert [b.canonical_id for b in left] == [f"s-{i}" for i in range(1, 5)]
    assert [b.canonical_id for b in right] == [f"s-{i}" for i in range(5, 9)]
    left, right = bisect_bindings(_bindings(7))
    assert len(left) == 3 and len(right) == 4
    with pytest.raises(ValueError):
        bisect_bindings(_bindings(1))


def test_eligibility_is_type_allowlist_not_substring():
    assert is_recoverable_model_output_failure(
        FindingParseError("Finding item 0: invalid field evidence.quote")
    )
    assert is_recoverable_model_output_failure(
        ModelOutputTruncatedError("Model output truncated: completion token limit reached")
    )
    assert not is_recoverable_model_output_failure(
        ProviderRequestError("openai HTTP 401")
    )
    class _Weird(Exception):
        def __str__(self) -> str:
            return "Finding item 0: invalid field evidence.quote"

    assert not is_recoverable_model_output_failure(_Weird())


def test_multi_section_failure_recovers_two_halves(restore_mock_provider):
    provider = _RecordingProvider(
        [_invalid(), _ok("S0001", 1, "Left"), _ok("S0002", 2, "Right")]
    )
    register_provider(provider)
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=8,
    )
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert report.run.coverage_complete is True
    assert report.run.sections_failed == 0
    assert report.run.recovery_split_count == 1
    assert report.run.recovery_request_count == 2
    assert report.run.analysis_request_count == 3
    assert report.run.total_provider_request_count == 3
    assert [f.title for f in report.findings] == ["Left", "Right"]
    assert "PARENT-SHOULD-NOT-SURVIVE" not in report_to_json(report)


def test_quarter_level_recovery_after_one_failed_half(restore_mock_provider):
    provider = _RecordingProvider(
        [
            _invalid("P"),
            _invalid("L"),
            _ok("S0001", 1, "Q1"),
            _ok("S0002", 2, "Q2"),
            _ok("S0003", 3, "Right"),
        ]
    )
    register_provider(provider)
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=8,
    )
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert report.run.recovery_split_count == 2
    assert report.run.sections_succeeded == 4
    assert [f.title for f in report.findings] == ["Q1", "Q2", "Right"]
    assert "P" not in [f.title for f in report.findings]
    assert "L" not in [f.title for f in report.findings]


def test_recovery_down_to_successful_single_section(restore_mock_provider):
    provider = _RecordingProvider(
        [
            _invalid(),
            _ok("S0001", 1, "Only"),
            _invalid(),
            _ok("S0002", 2, "Two"),
            _ok("S0003", 3, "Three"),
        ]
    )
    register_provider(provider)
    report = run_scan(
        _doc(3),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert [f.title for f in report.findings] == ["Only", "Two", "Three"]
    assert report.run.recovery_split_count >= 2


def test_one_failed_leaf_with_siblings_and_later_chunks(restore_mock_provider):
    # 6 sections, 3+3 packing with max 3. Chunk 1 recovers a failed leaf;
    # chunk 2 still runs.
    provider = _RecordingProvider(
        [
            _invalid("C1"),
            _ok("S0001", 1, "A"),
            _invalid("Leaf"),  # right half of 2 sections → split again
            _ok("S0002", 2, "B"),
            _invalid("Permaleaf"),  # singleton S0003 fails
            _ok("S0004", 4, "Later"),
        ]
    )
    register_provider(provider)
    report = run_scan(
        _doc(6),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=3,
    )
    assert report.run.analysis_status == AnalysisStatus.PARTIAL
    assert report.run.coverage_complete is False
    assert report.run.sections_failed == 1
    assert report.run.sections_succeeded == 5
    assert report.run.chunk_section_counts == [3, 3]
    titles = [f.title for f in report.findings]
    assert "A" in titles and "B" in titles and "Later" in titles
    assert "C1" not in titles and "Leaf" not in titles and "Permaleaf" not in titles
    md = report_to_markdown(report)
    assert "PARTIAL" in md
    assert "incomplete" in md.lower()
    assert report.run.failed_section_ids
    assert all(
        item.category.value in {"invalid_model_output", "truncated_output"}
        for item in report.run.unanalyzed_sections
    )


def test_rfc5905_shape_last_chunk_failure(restore_mock_provider):
    # 15 sections / 8 per request → [8, 7]. Last chunk invalid, halves succeed.
    payloads: list = [EMPTY]
    payloads.append(_invalid("LastChunk"))
    payloads.extend([EMPTY, EMPTY])
    provider = _RecordingProvider(payloads)
    register_provider(provider)
    report = run_scan(
        _doc(15),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=8,
    )
    assert report.run.chunk_section_counts == [8, 7]
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert report.run.recovery_split_count == 1
    assert report.run.analysis_request_count == 4  # original + failed parent + two halves
    assert report.run.recovery_request_count == 2
    assert report.run.total_provider_request_count == 4
    assert "LastChunk" not in report_to_json(report)


def test_ieee1588_shape_continues_after_mid_failure(restore_mock_provider):
    # 16 sections / 2 per request → 8 chunks. Fail chunk 6, recover one leaf,
    # continue with 7–8.
    payloads: list = [EMPTY] * 5
    payloads.append(_invalid("Mid"))
    payloads.append(EMPTY)  # left half of chunk 6
    payloads.append(_invalid("BadLeaf"))  # right singleton fails
    payloads.extend([EMPTY, EMPTY])  # chunks 7 and 8
    provider = _RecordingProvider(payloads)
    register_provider(provider)
    report = run_scan(
        _doc(16),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
    )
    assert report.run.chunk_section_counts == [2] * 8
    assert report.run.analysis_status == AnalysisStatus.PARTIAL
    assert report.run.sections_succeeded == 15
    assert report.run.sections_failed == 1
    assert report.run.analysis_request_count == 10  # 5 ok + failed mid + left + failed leaf + 2 later
    assert report.run.total_provider_request_count == 10
    assert report.run.recovery_request_count == 2
    assert len(provider.calls) >= 10


def test_evidence_correction_success_skips_split(restore_mock_provider):
    bad = {
        "findings": [
            _item("S0002", _quote(1), title="Cross"),
        ]
    }
    fix = EMPTY
    provider = _RecordingProvider([bad, fix, EMPTY])
    register_provider(provider)
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
    )
    assert report.run.recovery_split_count == 0
    assert report.run.recovery_request_count == 0
    assert report.run.repair_request_count == 1
    assert report.run.analysis_request_count == 2
    assert report.run.analysis_status == AnalysisStatus.COMPLETE


def test_deterministic_order_after_recursive_recovery(restore_mock_provider):
    provider = _RecordingProvider(
        [
            _invalid(),
            _ok("S0001", 1, "First"),
            _ok("S0002", 2, "Second"),
        ]
    )
    register_provider(provider)
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    assert [f.id for f in report.findings] == ["F-001", "F-002"]
    assert [f.title for f in report.findings] == ["First", "Second"]


def test_request_accounting_separates_recovery(restore_mock_provider):
    provider = _RecordingProvider([_invalid(), EMPTY, EMPTY])
    register_provider(provider)
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    assert report.run.analysis_request_count == 3
    assert report.run.repair_request_count == 0
    assert report.run.recovery_request_count == 2
    assert report.run.total_provider_request_count == 3
    assert report.run.recovery_split_count == 1
    assert report.actual_usage is not None
    assert report.actual_usage.input_tokens == 12 + 13 + 14
    dumped = json.loads(report_to_json(report))
    assert dumped["run"]["recovery_request_count"] == 2
    assert dumped["run"]["total_provider_request_count"] == 3
    assert dumped["schema_version"] == "0.6.0"
    md = report_to_markdown(report)
    assert "subset, not additional" in md


def test_recovery_limit_exhaustion_publishes_partial(restore_mock_provider):
    provider = _RecordingProvider([_invalid(), EMPTY, _invalid("Right")])
    register_provider(provider)
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_recovery_requests=1,
    )
    assert report.run.analysis_status == AnalysisStatus.PARTIAL
    assert report.run.recovery_request_count == 1
    assert any(
        item.category.value == "recovery_limit_exhausted"
        for item in report.run.unanalyzed_sections
    )
    assert report.run.sections_succeeded >= 1
    dumped = json.loads(report_to_json(report))
    cats = [item["category"] for item in dumped["run"]["unanalyzed_sections"]]
    assert "recovery_limit_exhausted" in cats
    md = report_to_markdown(report)
    assert "PARTIAL" in md
    assert "recovery" in md.lower() and "limit" in md.lower()


def test_every_eligible_section_fails_raises(restore_mock_provider):
    register_provider(_RecordingProvider([_invalid()]))
    with pytest.raises(FindingParseError):
        run_scan(
            _doc(4),
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
            max_recovery_requests=0,
        )


def test_ineligible_provider_error_does_not_split(restore_mock_provider):
    provider = _RecordingProvider(
        [ProviderRequestError("openai HTTP 401 for https://api.openai.com/v1/chat/completions (non-retryable)")]
    )
    register_provider(provider)
    with pytest.raises(ProviderRequestError):
        run_scan(
            _doc(4),
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
        )
    assert len(provider.calls) == 1


def test_unknown_exception_stays_generic_at_cli(tmp_path: Path, restore_mock_provider):
    class _Boom(RuntimeError):
        def __repr__(self) -> str:
            return f"<Boom {CANARY} {URL_CANARY}>"

    register_provider(_RecordingProvider([_Boom(CANARY)]))
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    assert GENERIC_SCAN_FAILURE in result.output
    assert CANARY not in result.output
    assert URL_CANARY not in result.output
    assert not (tmp_path / "out.json").exists()


def test_keyboard_interrupt_exit_130(tmp_path: Path, restore_mock_provider):
    register_provider(_RecordingProvider([KeyboardInterrupt()]))
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 130
    assert "Interrupted after" in result.output


def test_partial_cli_exit_3_writes_reports(tmp_path: Path, restore_mock_provider):
    register_provider(
        _RecordingProvider([EMPTY, _invalid(), EMPTY, _invalid("Leaf")])
    )
    src = tmp_path / "doc.txt"
    src.write_text(_doc(4), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 3, result.output
    assert "partial coverage" in result.output.lower()
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"
    assert json_path.is_file() and md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["run"]["analysis_status"] == "partial"
    assert payload["run"]["coverage_complete"] is False
    md = md_path.read_text(encoding="utf-8")
    assert "PARTIAL" in md
    assert "incomplete" in md.lower()
    assert CANARY not in json_path.read_text(encoding="utf-8")
    assert CANARY not in md


def test_multiple_raw_diagnostics_do_not_overwrite(
    tmp_path: Path, restore_mock_provider
):
    provider = _RecordingProvider(
        [_invalid("A"), _invalid("B1"), _invalid("B2")]
    )
    register_provider(provider)
    raw = tmp_path / "fail.raw.txt"
    with pytest.raises(FindingParseError):
        run_scan(
            _doc(2),
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
            save_raw_on_error=str(raw),
        )
    files = sorted(tmp_path.glob("fail.raw*.txt"))
    assert len(files) >= 2
    texts = [p.read_text(encoding="utf-8") for p in files]
    assert any("A" in text or "PARENT" in text or "time_assurance_gap" in text for text in texts)
    names = {p.name for p in files}
    assert "fail.raw.txt" in names
    if os.name == "posix":
        for path in files:
            assert not path.is_symlink()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_public_output_omits_sensitive_recovery_material(
    restore_mock_provider,
):
    secret = FindingParseError("Finding item 0: invalid field evidence.quote", raw=CANARY)
    provider = _RecordingProvider([_invalid(), EMPTY, secret])
    register_provider(provider)
    progress: list[str] = []
    report = run_scan(
        _doc(2) + "\n\n" + BODY_CANARY,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        on_progress=progress.append,
    )
    dumped = report_to_json(report)
    md = report_to_markdown(report)
    joined = dumped + md + "\n".join(progress)
    assert CANARY not in joined
    assert BODY_CANARY not in dumped
    assert BODY_CANARY not in md
    assert URL_CANARY not in joined
    for item in report.run.unanalyzed_sections:
        assert item.category.value in {
            "invalid_model_output",
            "truncated_output",
            "recovery_limit_exhausted",
        }


def test_successful_scan_without_recovery_unchanged(restore_mock_provider):
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    assert report.schema_version == "0.6.0"
    assert report.run.analysis_status == AnalysisStatus.COMPLETE
    assert report.run.recovery_request_count == 0
    assert report.run.recovery_split_count == 0
    assert report.run.analysis_request_count == 1
    assert report.run.total_provider_request_count == 1
    assert report.run.request_batching == RequestBatching.SECTION_CHUNKS
    assert "Adaptive recovery" not in report_to_markdown(report)


def test_scan_help_documents_recovery_option():
    param = inspect.signature(scan_cmd).parameters["max_recovery_requests"]
    option = param.default
    decls = " ".join(getattr(option, "param_decls", ()) or ())
    assert "max-recovery-requests" in decls
    help_text = option.help or ""
    assert str(DEFAULT_MAX_RECOVERY_REQUESTS) in help_text
    assert "unestimated" in help_text.lower()
    assert "preflight" in help_text.lower()
    result = CliRunner().invoke(app, ["scan", "--help"])
    assert result.exit_code == 0


def test_default_recovery_limit_covers_eight_section_worst_case():
    # Full binary tree extras for k=8: 2k-2 = 14 recovery primaries (no repair).
    assert DEFAULT_MAX_RECOVERY_REQUESTS >= 14


def _assert_request_counts(
    report,
    *,
    analysis: int,
    repair: int,
    recovery: int,
    splits: int | None = None,
) -> None:
    assert report.run.analysis_request_count == analysis
    assert report.run.repair_request_count == repair
    assert report.run.recovery_request_count == recovery
    assert report.run.total_provider_request_count == analysis + repair
    assert report.run.recovery_request_count <= report.run.total_provider_request_count
    if splits is not None:
        assert report.run.recovery_split_count == splits
    dumped = json.loads(report_to_json(report))
    assert dumped["run"]["analysis_request_count"] == analysis
    assert dumped["run"]["repair_request_count"] == repair
    assert dumped["run"]["recovery_request_count"] == recovery
    assert dumped["run"]["total_provider_request_count"] == analysis + repair


def test_counters_no_recovery(restore_mock_provider):
    register_provider(_RecordingProvider([EMPTY, EMPTY]))
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
    )
    _assert_request_counts(report, analysis=2, repair=0, recovery=0, splits=0)
    assert report.run.chunk_section_counts == [2, 2]
    assert report.run.analysis_status == AnalysisStatus.COMPLETE


def test_counters_failed_parent_two_successful_children(restore_mock_provider):
    register_provider(_RecordingProvider([_invalid(), EMPTY, EMPTY]))
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    _assert_request_counts(report, analysis=3, repair=0, recovery=2, splits=1)


def test_counters_failed_parent_with_correction_then_two_children(
    restore_mock_provider,
):
    bad = {"findings": [_item("S0002", _quote(1), title="Cross")]}
    still_bad = {"findings": [_item("S0002", _quote(1), title="Still")]}
    provider = _RecordingProvider(
        [bad, still_bad, _ok("S0001", 1, "Left"), _ok("S0002", 2, "Right")]
    )
    register_provider(provider)
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    _assert_request_counts(report, analysis=3, repair=1, recovery=2, splits=1)
    assert [f.title for f in report.findings] == ["Left", "Right"]
    assert report.actual_usage is not None
    assert report.actual_usage.input_tokens == 12 + 13 + 14 + 15
    assert len(provider.calls) == 4


def test_counters_nested_recovery_to_singleton(restore_mock_provider):
    register_provider(
        _RecordingProvider(
            [
                _invalid(),
                _ok("S0001", 1, "One"),
                _invalid(),
                _ok("S0002", 2, "Two"),
                _ok("S0003", 3, "Three"),
            ]
        )
    )
    report = run_scan(
        _doc(3),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    _assert_request_counts(report, analysis=5, repair=0, recovery=4, splits=2)
    assert report.run.analysis_status == AnalysisStatus.COMPLETE


def test_counters_failed_singleton(restore_mock_provider):
    register_provider(_RecordingProvider([EMPTY, _invalid()]))
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=1,
    )
    _assert_request_counts(report, analysis=2, repair=0, recovery=0, splits=0)
    assert report.run.analysis_status == AnalysisStatus.PARTIAL
    assert report.run.sections_failed == 1
    assert report.run.chunk_section_counts == [1, 1]


def test_counters_recovery_limit_exhaustion(restore_mock_provider):
    register_provider(_RecordingProvider([_invalid(), EMPTY, _invalid("Right")]))
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_recovery_requests=1,
    )
    _assert_request_counts(report, analysis=2, repair=0, recovery=1, splits=1)
    assert report.run.analysis_status == AnalysisStatus.PARTIAL


def test_counters_multiple_failing_original_chunks(restore_mock_provider):
    register_provider(
        _RecordingProvider(
            [_invalid(), EMPTY, EMPTY, _invalid(), EMPTY, EMPTY]
        )
    )
    report = run_scan(
        _doc(4),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
    )
    _assert_request_counts(report, analysis=6, repair=0, recovery=4, splits=2)
    assert report.run.chunk_section_counts == [2, 2]
    assert report.run.analysis_status == AnalysisStatus.COMPLETE


def test_counters_failed_recovery_child_reports_usage(restore_mock_provider):
    provider = _RecordingProvider([_invalid(), EMPTY, _invalid("ChildFail")])
    register_provider(provider)
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    _assert_request_counts(report, analysis=3, repair=0, recovery=2, splits=1)
    assert report.run.analysis_status == AnalysisStatus.PARTIAL
    assert report.actual_usage is not None
    assert report.actual_usage.input_tokens == 12 + 13 + 14
    assert len(provider.calls) == 3


def test_truncated_child_counts_request_without_invented_usage(
    restore_mock_provider,
):
    provider = _RecordingProvider(
        [
            _invalid(),
            EMPTY,
            ModelOutputTruncatedError(
                "Model output truncated: completion token limit reached"
            ),
        ]
    )
    register_provider(provider)
    report = run_scan(
        _doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
    )
    _assert_request_counts(report, analysis=3, repair=0, recovery=2, splits=1)
    assert report.actual_usage is not None
    assert report.actual_usage.input_tokens == 12 + 13
    assert report.run.unanalyzed_sections[0].category.value == "truncated_output"


def test_raw_slug_refuses_traversal_and_stays_in_output_dir(tmp_path: Path):
    dest = tmp_path / "out.raw.txt"
    sink = RawDiagnosticSink(str(dest))
    first = sink.destination("../etc/passwd")
    assert first == str(dest)
    sink.remember(first)
    second = sink.destination("../etc/passwd")
    assert second is not None
    second_path = Path(second)
    assert second_path.parent == dest.parent
    assert ".." not in second_path.name
    assert "/" not in second_path.name
    assert "\\" not in second_path.name
    outside = tmp_path.parent / "escaped.raw.txt"
    assert second_path.resolve() != outside.resolve()
    assert tmp_path in second_path.resolve().parents or second_path.parent == tmp_path


def test_raw_slug_collision_and_separators(tmp_path: Path):
    dest = tmp_path / "out.raw.txt"
    sink = RawDiagnosticSink(str(dest))
    sink.remember(str(dest))
    paths: list[Path] = []
    for slug in ("S0001!!!", "S0001@@@", "foo/../../etc", "foo\\bar"):
        chosen = sink.destination(slug)
        assert chosen is not None
        sink.remember(chosen)
        paths.append(Path(chosen))
    assert len(paths) == 4
    names = [p.name for p in paths]
    assert len(set(names)) == 4
    for path in paths:
        assert path.parent == dest.parent
        assert "/" not in path.name
        assert "\\" not in path.name
        assert ".." not in path.name


def test_raw_persist_uses_known_truncation_raw_only(
    tmp_path: Path, restore_mock_provider
):
    class _NoExtraTrunc(ModelOutputTruncatedError):
        pass

    dest = tmp_path / "fail.raw.txt"
    register_provider(
        _RecordingProvider(
            [_NoExtraTrunc("Model output truncated: completion token limit reached")]
        )
    )
    with pytest.raises(ModelOutputTruncatedError):
        run_scan(
            _doc(1),
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
            save_raw_on_error=str(dest),
        )
    assert dest.is_file()
    if os.name == "posix":
        assert not dest.is_symlink()
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600


def test_cli_complete_scan_exit_0(tmp_path: Path, restore_mock_provider):
    register_provider(_RecordingProvider([EMPTY]))
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "ok"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ok.json").is_file()
    assert (tmp_path / "ok.md").is_file()
    payload = json.loads((tmp_path / "ok.json").read_text(encoding="utf-8"))
    assert payload["run"]["analysis_status"] == "complete"
    md = (tmp_path / "ok.md").read_text(encoding="utf-8")
    assert "COMPLETE" in md


def test_cli_all_sections_fail_writes_no_report(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_RecordingProvider([_invalid()]))
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-recovery-requests",
            "0",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "fail"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "fail.json").exists()
    assert not (tmp_path / "fail.md").exists()
    assert CANARY not in result.output


def test_cli_invalid_recovery_limit_exits_2_before_provider(
    tmp_path: Path, restore_mock_provider
):
    class _MustNotCall(MockProvider):
        def complete(self, *args, **kwargs):
            raise AssertionError("provider must not be contacted")

    register_provider(_MustNotCall())
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-recovery-requests",
            str(MAX_MAX_RECOVERY_REQUESTS + 1),
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "badlimit"),
        ],
    )
    assert result.exit_code == 2, result.output
    assert not (tmp_path / "badlimit.json").exists()
    assert not (tmp_path / "badlimit.md").exists()


def test_cli_non_recoverable_writes_no_partial_report(
    tmp_path: Path, restore_mock_provider
):
    register_provider(
        _RecordingProvider([ProviderRequestError("openai HTTP 401")])
    )
    src = tmp_path / "doc.txt"
    src.write_text(_doc(2), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "auth"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "auth.md").exists()
    assert "openai HTTP 401" in result.output
    assert "sk-" not in result.output
    assert URL_CANARY not in result.output


def test_cli_tty_clears_live_line_before_partial(
    tmp_path: Path, restore_mock_provider, monkeypatch
):
    from tads.progress import ScanProgressReporter

    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=True
        ),
    )
    register_provider(
        _RecordingProvider([EMPTY, _invalid(), EMPTY, _invalid("Leaf")])
    )
    src = tmp_path / "doc.txt"
    src.write_text(_doc(4), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "tty"),
        ],
    )
    assert result.exit_code == 3, result.output
    assert "\r" in result.output
    partial_at = result.output.lower().rfind("partial coverage")
    assert partial_at != -1
    assert result.output[:partial_at].count("\r") >= 1
    payload = json.loads((tmp_path / "tty.json").read_text(encoding="utf-8"))
    assert payload["run"]["analysis_status"] == "partial"
    md = (tmp_path / "tty.md").read_text(encoding="utf-8")
    assert "PARTIAL" in md
    assert "incomplete" in md.lower()
    assert "Traceback" not in result.output
    assert CANARY not in result.output + md
    assert "OPENAI_API_KEY" not in result.output
