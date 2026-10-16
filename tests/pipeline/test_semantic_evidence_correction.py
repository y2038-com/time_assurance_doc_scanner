# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Bounded section-local evidence semantic correction (offline, mock only)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from tads import __version__
from tads.cost import MODEL_PRICING_USD_PER_MTIME, estimate_cost_usd_for_requests
from tads.llm.base import ChatMessage, LLMResponse, ModelOutputTruncatedError
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import SemanticCorrectionError, plan_scan, run_scan
from tads.pipeline.parse_findings import (
    EVIDENCE_QUOTE_NOT_IN_CLAIMED_SECTION,
    FindingParseError,
    parse_findings_payload,
)
from tads.prompts import (
    PROMPT_FRAMEWORK_VERSION,
    SEMANTIC_CORRECTION_PREAMBLE,
    build_section_chunk_prompt,
    build_semantic_correction_prompt,
    build_whole_document_prompt,
)
from tads.schemas.report import AnalysisMode, RequestBatching

BODY_CANARY = "CORR_PROVIDER_BODY_CANARY_xyz"
HEADER_CANARY = "CORR_HDR_AUTH_CANARY_xyz"
QUERY_CANARY = "CORR_QUERY_CANARY_xyz"
USERINFO_CANARY = "CORR_USERINFO_CANARY_xyz"
DOC_CANARY = "CORR_DOCUMENT_TEXT_CANARY_xyz"
PROMPT_CANARY = "CORR_PROMPT_TEXT_CANARY_xyz"
QUOTE_CANARY = "CORR_REJECTED_QUOTE_CANARY_xyz"
PRIMARY_RAW_MARKER = "PrimaryRawMarkerForOptInOnly"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TWO_SECTION = FIXTURES / "semantic_correction_two_section.txt"
SIXTEEN = (
    Path(__file__).resolve().parents[1]
    / "parsing"
    / "fixtures"
    / "w3c_sixteen_sections_synth.txt"
)

ALPHA_QUOTE = (
    "Alpha warehouse records that the backup agent finishes the nightly batch "
    "with a restart-safe checkpoint before retention jobs begin carefully enough "
    "for tests."
)
BETA_QUOTE = (
    "Beta inventory records that each floor terminal reconciles stock deltas "
    "before applying them to a second terminal carefully enough for tests."
)


def _item(section_id: str, quote: str, **overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Gap",
        "description": "A synthetic time assurance gap for correction tests.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["persistence"],
        "section_id": section_id,
        "section_title": None,
        "evidence": [{"quote": quote, "note": None}],
        "machine_interpretation": "Synthetic interpretation for correction tests.",
        "recommendation_level1": "Document the synthetic boundary clearly.",
        "scope_relevance": "core",
        "scope_rationale": (
            "This matters to time assurance because synthetic ordering affects replay."
        ),
        "time_representation": None,
    }
    item.update(overrides)
    return item


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


class _RecordingProvider(MockProvider):
    def __init__(self, payloads: list) -> None:
        super().__init__()
        self._payloads = list(payloads)
        self.calls: list[list[ChatMessage]] = []
        self.complete_kwargs: list[dict] = []

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
        self.complete_kwargs.append(
            {
                "model": model,
                "max_output_tokens": max_output_tokens,
                "reasoning_effort": reasoning_effort,
            }
        )
        idx = min(len(self.calls) - 1, len(self._payloads) - 1)
        payload = self._payloads[idx]
        if isinstance(payload, Exception):
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


def _two_section_text() -> str:
    return TWO_SECTION.read_text(encoding="utf-8")


def test_prompt_framework_is_091():
    assert PROMPT_FRAMEWORK_VERSION == "0.9.1"
    assert __version__ == "0.6.0rc4"


def test_grounding_miss_exposes_structured_code_not_in_message():
    plan = plan_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    bindings = plan.chunks[0].allowlist()
    with pytest.raises(FindingParseError) as caught:
        parse_findings_payload(
            {
                "findings": [
                    _item("S0002", ALPHA_QUOTE),  # quote from sibling
                ]
            },
            section_bindings=bindings,
            document_text=plan.document.text,
        )
    err = caught.value
    assert err.code == EVIDENCE_QUOTE_NOT_IN_CLAIMED_SECTION
    assert str(err) == "Finding item 0: invalid field evidence.quote"
    assert ALPHA_QUOTE not in str(err)
    assert "S0001" not in str(err)
    assert err.__cause__ is None


def test_wrong_section_quote_triggers_one_correction(restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title="Wrong section")]}
    good = {
        "findings": [
            _item("S0001", ALPHA_QUOTE, title="Corrected alpha"),
            _item("S0002", BETA_QUOTE, title="Corrected beta"),
        ]
    }
    provider = _RecordingProvider([bad, good])
    register_provider(provider)
    report = run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        max_output_tokens=2048,
        reasoning_effort=None,
    )
    assert len(provider.calls) == 2
    assert report.run.analysis_request_count == 1
    assert report.run.repair_request_count == 1
    assert [f.title for f in report.findings] == ["Corrected alpha", "Corrected beta"]
    assert report.findings[0].location.section_id == "s-1"
    assert report.findings[1].location.section_id == "s-2"
    # Original invalid response not attached to correction channels.
    corr_blob = "\n".join(m.content for m in provider.calls[1])
    assert "Wrong section" not in corr_blob
    assert json.dumps(bad) not in corr_blob
    assert ALPHA_QUOTE not in provider.calls[1][0].content  # system trusted
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in provider.calls[1][0].content
    # User envelopes byte-identical.
    assert provider.calls[0][1].content == provider.calls[1][1].content
    # Same model / budget / reasoning kwargs.
    assert provider.complete_kwargs[0]["max_output_tokens"] == 2048
    assert provider.complete_kwargs[1]["max_output_tokens"] == 2048
    assert provider.complete_kwargs[0]["reasoning_effort"] == provider.complete_kwargs[1][
        "reasoning_effort"
    ]


def test_correction_discards_original_siblings(restore_mock_provider):
    # Primary has a valid sibling plus a bad cross-section item; correction
    # returns only one finding — original sibling must not be merged.
    bad = {
        "findings": [
            _item("S0001", ALPHA_QUOTE, title="Keep me?"),
            _item("S0002", ALPHA_QUOTE, title="Bad cross"),
        ]
    }
    replacement = {"findings": [_item("S0002", BETA_QUOTE, title="Only beta")]}
    register_provider(_RecordingProvider([bad, replacement]))
    report = run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert [f.title for f in report.findings] == ["Only beta"]
    assert report.run.repair_request_count == 1


def test_no_local_reassignment_or_evidence_rewrite(restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    # Correction still wrong-section → abort; scanner must not rewrite locally.
    still_bad = {"findings": [_item("S0002", ALPHA_QUOTE, title="Still bad")]}
    register_provider(_RecordingProvider([bad, still_bad]))
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )


def test_correction_prompt_omits_diagnostics(restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title="SecretTitleXYZ")]}
    good = {"findings": [_item("S0001", ALPHA_QUOTE)]}
    provider = _RecordingProvider([bad, good])
    register_provider(provider)
    run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    system = provider.calls[1][0].content
    user = provider.calls[1][1].content
    blob = system + "\n" + user
    assert "SecretTitleXYZ" not in blob
    assert "evidence.quote.not_in_claimed_section" not in blob
    assert "Finding item" not in system
    assert "Traceback" not in blob
    assert "Stack" not in blob


def test_correction_user_envelope_matches_builder():
    plan = plan_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    chunk = plan.chunks[0]
    refs = [b.opaque_ref for b in chunk.bindings]
    sections = [b.section for b in chunk.bindings]
    primary = build_section_chunk_prompt(
        plan.document, opaque_refs=refs, sections=sections, document_summary=""
    )
    correction = build_semantic_correction_prompt(
        plan.document, opaque_refs=refs, sections=sections, document_summary=""
    )
    assert correction.user == primary.user
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in correction.system
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in primary.system


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"findings": [_item("S0099", ALPHA_QUOTE)]}, id="unknown-ref"),
        pytest.param({"findings": [_item(None, ALPHA_QUOTE)]}, id="null-ref"),
        pytest.param(
            {"findings": [_item("S0001", ALPHA_QUOTE, severity="banana")]},
            id="bad-enum",
        ),
        pytest.param(
            {"findings": [_item("S0001", ALPHA_QUOTE, domains=["not-a-domain"])]},
            id="bad-domain",
        ),
        pytest.param(
            {
                "findings": [
                    _item("S0001", ALPHA_QUOTE, extra_key="nope")
                ]
            },
            id="extra-key",
        ),
        pytest.param(
            {"findings": [_item("S0001", "   ")]},
            id="whitespace-quote",
        ),
        pytest.param(
            {
                "findings": [
                    {
                        **_item("S0001", ALPHA_QUOTE),
                        "time_representation": {
                            "claimed_horizon": {"value": 2036, "precision": "year"}
                        },
                    }
                ]
            },
            id="bad-horizon",
        ),
    ],
)
def test_ineligible_failures_skip_semantic_correction(
    payload, restore_mock_provider
):
    # Strip helper artifact if present
    if "extra_key" in payload["findings"][0]:
        item = dict(payload["findings"][0])
        item["not_allowed"] = item.pop("extra_key")
        payload = {"findings": [item]}
    provider = _RecordingProvider([payload, {"findings": []}])
    register_provider(provider)
    with pytest.raises((FindingParseError, Exception)):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert len(provider.calls) == 1


def test_empty_quote_skips_correction(restore_mock_provider):
    payload = {
        "findings": [
            {
                **_item("S0001", ALPHA_QUOTE),
                "evidence": [{"quote": "", "note": None}],
            }
        ]
    }
    provider = _RecordingProvider([payload, {"findings": []}])
    register_provider(provider)
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert len(provider.calls) == 1


def test_duplicate_keys_skip_correction(tmp_path: Path, restore_mock_provider):
    provider = _RecordingProvider(
        ['{"findings": [], "findings": []}', {"findings": []}]
    )
    register_provider(provider)
    json_path = tmp_path / "r.json"
    md_path = tmp_path / "r.md"
    with pytest.raises(FindingParseError, match="Duplicate JSON object key") as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    err = caught.value
    assert err.repairable is False
    assert err.__cause__ is None
    assert err.__context__ is None
    assert str(err) == "Duplicate JSON object key"
    assert len(provider.calls) == 1
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in provider.calls[0][0].content
    assert not json_path.exists()
    assert not md_path.exists()


def test_correction_duplicate_keys_abort_no_nested_repair(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    dup = '{"findings": [], "findings": []}'
    provider = _RecordingProvider([bad, dup, {"findings": []}])
    register_provider(provider)
    raw_path = tmp_path / "dup.raw.txt"
    json_path = tmp_path / "dup.json"
    with pytest.raises(FindingParseError, match="Duplicate JSON object key") as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    err = caught.value
    assert err.repairable is False
    assert err.__cause__ is None
    assert err.__context__ is None
    assert len(provider.calls) == 2
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in provider.calls[1][0].content
    assert raw_path.read_text(encoding="utf-8") == dup
    assert PRIMARY_RAW_MARKER not in raw_path.read_text(encoding="utf-8")
    assert not json_path.exists()


def test_json_repair_consumes_slot_blocks_semantic_correction(restore_mock_provider):
    # Call 1: broken JSON → envelope repair. Call 2: repaired but wrong-section.
    # Must abort without a third semantic correction call.
    broken = "Here:\n```json\n{\"findings\": [\n"
    repaired_bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    provider = _RecordingProvider([broken, repaired_bad, {"findings": []}])
    register_provider(provider)
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert len(provider.calls) == 2
    assert all(
        SEMANTIC_CORRECTION_PREAMBLE.strip() not in c[0].content for c in provider.calls
    )


def test_primary_truncation_skips_correction(restore_mock_provider):
    provider = _RecordingProvider(
        [
            ModelOutputTruncatedError("truncated", raw="partial"),
            {"findings": []},
        ]
    )
    register_provider(provider)
    with pytest.raises(ModelOutputTruncatedError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert len(provider.calls) == 1


def test_primary_provider_failure_skips_correction(restore_mock_provider):
    provider = _RecordingProvider([RuntimeError("transport boom"), {"findings": []}])
    register_provider(provider)
    with pytest.raises(RuntimeError, match="transport boom"):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert len(provider.calls) == 1


def test_correction_extract_failure_no_nested_repair(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    not_json = "not json at all {{"
    provider = _RecordingProvider([bad, not_json, {"findings": []}])
    register_provider(provider)
    raw_path = tmp_path / "out.raw.txt"
    with pytest.raises(FindingParseError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    assert len(provider.calls) == 2
    assert raw_path.is_file()
    assert "not json at all" in raw_path.read_text(encoding="utf-8")


def test_correction_semantic_failure_aborts(tmp_path: Path, restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    still = {"findings": [_item("S0002", ALPHA_QUOTE, title="Still")]}
    provider = _RecordingProvider([bad, still])
    register_provider(provider)
    json_path = tmp_path / "r.json"
    md_path = tmp_path / "r.md"
    with pytest.raises(FindingParseError, match=r"evidence\.quote"):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(tmp_path / "r.raw.txt"),
        )
    assert not json_path.exists()
    assert not md_path.exists()


def test_correction_truncation_aborts(tmp_path: Path, restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    provider = _RecordingProvider(
        [bad, ModelOutputTruncatedError("truncated", raw="corr-partial")]
    )
    register_provider(provider)
    raw_path = tmp_path / "t.raw.txt"
    with pytest.raises(ModelOutputTruncatedError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    assert raw_path.read_text(encoding="utf-8") == "corr-partial"


def test_correction_provider_failure_keeps_primary_raw(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    provider = _RecordingProvider([bad, RuntimeError("corr transport SECRET")])
    register_provider(provider)
    raw_path = tmp_path / "p.raw.txt"
    with pytest.raises(SemanticCorrectionError, match="Semantic evidence correction failed") as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    err = caught.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert "corr transport" not in str(err)
    assert "SECRET" not in str(err) and "SECRET" not in repr(err)
    text = raw_path.read_text(encoding="utf-8")
    assert PRIMARY_RAW_MARKER in text
    assert "corr transport" not in text


def test_raw_on_error_mode_and_symlink_safety(tmp_path: Path, restore_mock_provider):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    still = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    register_provider(_RecordingProvider([bad, still]))
    raw_path = tmp_path / "raw.txt"
    with pytest.raises(FindingParseError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    assert raw_path.is_file() and not raw_path.is_symlink()
    mode = stat.S_IMODE(raw_path.stat().st_mode)
    assert mode == 0o600

    # Symlink destination refused.
    target = tmp_path / "target.txt"
    target.write_text("old", encoding="utf-8")
    link = tmp_path / "link.raw.txt"
    os.symlink(target, link)
    register_provider(_RecordingProvider([bad, still]))
    with pytest.raises(FindingParseError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(link),
        )
    assert target.read_text(encoding="utf-8") == "old"


def test_failed_correction_leaves_existing_reports(tmp_path: Path, restore_mock_provider):
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    json_path.write_text('{"schema_version":"0.5.0"}', encoding="utf-8")
    md_path.write_text("old markdown", encoding="utf-8")
    bad = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    still = {"findings": [_item("S0002", ALPHA_QUOTE)]}
    register_provider(_RecordingProvider([bad, still]))
    with pytest.raises(FindingParseError):
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
        )
    assert json_path.read_text(encoding="utf-8") == '{"schema_version":"0.5.0"}'
    assert md_path.read_text(encoding="utf-8") == "old markdown"


def test_two_chunks_one_correction_counts(restore_mock_provider):
    text = SIXTEEN.read_text(encoding="utf-8")
    # First chunk: wrong-section then correction empty; second chunk: empty.
    # Opaque refs for first chunk are S0001..S0008 — use S0002 with a quote from S0001 body.
    plan = plan_scan(
        text,
        doc_id="sixteen",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert plan.chunk_section_counts == (8, 8)
    s1_quote = plan.chunks[0].bindings[0].section.text.strip()[:80]
    # Ensure quote is long enough and unique-ish
    assert len(s1_quote) >= 40
    bad = {
        "findings": [
            _item("S0002", s1_quote, title="Cross in chunk1"),
        ]
    }
    fix = {"findings": []}
    empty = {"findings": []}
    provider = _RecordingProvider([bad, fix, empty])
    register_provider(provider)
    report = run_scan(
        text,
        doc_id="sixteen",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert report.run.analysis_request_count == 2
    assert report.run.repair_request_count == 1
    assert report.run.chunk_section_counts == [8, 8]
    assert report.run.coverage_complete is True
    assert report.schema_version == "0.5.0"
    assert len(provider.calls) == 3
    # Usage includes primary+correction+second primary once each.
    assert report.actual_usage is not None
    # RecordingProvider uses 11+call_n / 7+call_n / reasoning=call_n.
    assert report.actual_usage.input_tokens == 12 + 13 + 14
    assert report.actual_usage.output_tokens == 8 + 9 + 10
    assert report.actual_usage.reasoning_tokens == 1 + 2 + 3


def test_singleton_no_section_chunking_may_correct(restore_mock_provider):
    bad = {"findings": [_item("S0001", BETA_QUOTE)]}  # beta text on alpha-only chunk
    # With max_sections=1 first chunk is only Alpha (S0001).
    good = {"findings": [_item("S0001", ALPHA_QUOTE, title="Fixed singleton")]}
    # Need responses for chunk1 primary+corr, then later chunks may get empty.
    payloads = [bad, good] + [{"findings": []}] * 8
    provider = _RecordingProvider(payloads)
    register_provider(provider)
    report = run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        section_chunking=False,
    )
    assert report.run.request_batching == RequestBatching.PER_SECTION
    assert report.run.repair_request_count == 1
    assert any(f.title == "Fixed singleton" for f in report.findings)


def test_whole_document_does_not_semantic_correct(restore_mock_provider):
    # WD path has no section bindings; a quote miss is soft-verified, not corrected.
    item = _item(
        "S0001",
        "this quote is absent from the whole document entirely here",
    )
    item["section_id"] = None
    provider = _RecordingProvider([{"findings": [item]}, {"findings": []}])
    register_provider(provider)
    report = run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
    )
    assert len(provider.calls) == 1
    assert report.run.repair_request_count == 0
    assert report.run.request_batching == RequestBatching.WHOLE_DOCUMENT
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in provider.calls[0][0].content


def test_whole_document_prompt_excludes_correction_rules():
    from tads.parsing.document import ParsedDocument

    doc = ParsedDocument(
        corpus="ietf",
        doc_id="RFC9999",
        title="T",
        text="Timestamps are 32-bit seconds.",
        sections=[],
    )
    bundle = build_whole_document_prompt(doc)
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in bundle.system
    assert "exact, contiguous, verbatim" not in bundle.system


def test_plan_notes_mention_secondary_contingency():
    plan = plan_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    joined = " ".join(plan.cost_estimate.notes)
    assert "additional repair or section-local evidence-correction" in joined


def test_structured_output_fallback_during_correction_does_not_inflate_counts(
    restore_mock_provider,
):
    from tads.schemas.cost import TokenUsage

    class FallbackCorrection(MockProvider):
        def __init__(self) -> None:
            super().__init__()
            self.complete_calls = 0
            self.http_posts = 0

        def supports_native_structured_output(self, model=None) -> bool:
            return True

        def complete(self, messages, *, model=None, max_output_tokens=4096, reasoning_effort=None):
            from tads.llm.base import reject_unsupported_reasoning_effort

            reject_unsupported_reasoning_effort(reasoning_effort)
            self.complete_calls += 1
            self.http_posts += 2  # native + schema-free fallback inside one complete
            if self.complete_calls == 1:
                content = json.dumps({"findings": [_item("S0002", ALPHA_QUOTE)]})
            else:
                content = json.dumps({"findings": [_item("S0001", ALPHA_QUOTE)]})
            return LLMResponse(
                content=content,
                model=model or self.default_model(),
                usage=TokenUsage(input_tokens=4, output_tokens=4),
            )

    provider = FallbackCorrection()
    register_provider(provider)
    report = run_scan(
        _two_section_text(),
        doc_id="corr-2",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert provider.complete_calls == 2
    assert provider.http_posts == 4
    assert report.run.analysis_request_count == 1
    assert report.run.repair_request_count == 1


def test_no_ignored_benchmark_inputs_referenced():
    root = Path(__file__).resolve().parents[2] / "tests"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if "inputs/hr-time-3.txt" in text or "HR_TIME_INPUT" in text:
            offenders.append(str(path))
    assert offenders == []


def _sixteen_cross_chunk_payloads() -> tuple[str, dict, dict, dict, dict]:
    """Build grounded fix payloads for each of two 8-section chunks."""
    text = SIXTEEN.read_text(encoding="utf-8")
    plan = plan_scan(
        text,
        doc_id="sixteen",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert plan.chunk_section_counts == (8, 8)
    c0_quote = plan.chunks[0].bindings[0].section.text.strip()[:80]
    c1_quote = plan.chunks[1].bindings[0].section.text.strip()[:80]
    assert len(c0_quote) >= 40 and len(c1_quote) >= 40
    bad0 = {"findings": [_item("S0002", c0_quote, title="Cross chunk1")]}
    fix0 = {"findings": []}
    bad1 = {"findings": [_item("S0010", c1_quote, title="Cross chunk2")]}
    fix1 = {"findings": []}
    return text, bad0, fix0, bad1, fix1


def _walk_reachable(root: object, *, limit: int = 800) -> list[object]:
    seen: set[int] = set()
    out: list[object] = []
    stack: list[object] = [root]
    while stack and len(out) < limit:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        out.append(obj)
        if isinstance(obj, BaseException):
            stack.extend([obj.__cause__, obj.__context__, *obj.args])
            stack.extend(getattr(obj, "__dict__", {}).values())
        elif isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        else:
            data = getattr(obj, "__dict__", None)
            if isinstance(data, dict):
                stack.extend(data.values())
    return out


def _assert_clean_terminal_exception(exc: BaseException, *extra_surfaces: object) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    canaries = (
        BODY_CANARY,
        HEADER_CANARY,
        QUERY_CANARY,
        USERINFO_CANARY,
        DOC_CANARY,
        PROMPT_CANARY,
        QUOTE_CANARY,
    )
    surfaces = "\n".join(
        [str(exc), repr(exc), *[str(s) for s in extra_surfaces if s is not None]]
    )
    for canary in canaries:
        assert canary not in surfaces
    for obj in _walk_reachable(exc):
        assert not isinstance(obj, (httpx.Request, httpx.Response))
        assert not isinstance(obj, httpx.HTTPError)
        if isinstance(obj, BaseException) and obj is not exc:
            # Nested provider exceptions must not remain reachable.
            assert not isinstance(obj, (_ContaminatedProviderError, RuntimeError))
            for canary in canaries:
                assert canary not in str(obj)
                assert canary not in repr(obj)


class _ContaminatedProviderError(RuntimeError):
    """Provider-shaped failure with reachable httpx and canary attributes."""

    def __init__(self) -> None:
        super().__init__(f"upstream boom {BODY_CANARY}")
        self.response = httpx.Response(
            500,
            request=httpx.Request(
                "POST",
                f"https://{USERINFO_CANARY}:pw@api.example/v1/chat"
                f"?token={QUERY_CANARY}#frag",
            ),
            content=BODY_CANARY.encode("utf-8"),
            headers={"Authorization": f"Bearer {HEADER_CANARY}"},
        )
        self.request = self.response.request
        self.prompt_leak = PROMPT_CANARY
        self.document_leak = DOC_CANARY
        self.rejected_quote = QUOTE_CANARY
        self.nested = {
            "headers": {"Authorization": f"Bearer {HEADER_CANARY}"},
            "body": BODY_CANARY,
            "url": str(self.request.url),
        }


def _raise_contaminated_provider(_messages=None) -> None:
    try:
        raise ValueError(f"wrap {DOC_CANARY}")
    except ValueError:
        raise _ContaminatedProviderError() from None


def test_chunk1_json_repair_chunk2_semantic_correction(restore_mock_provider):
    text, _bad0, _fix0, bad1, fix1 = _sixteen_cross_chunk_payloads()
    broken = "Here:\n```json\n{\"findings\": [\n"
    repaired_ok = {"findings": []}
    provider = _RecordingProvider([broken, repaired_ok, bad1, fix1])
    register_provider(provider)
    report = run_scan(
        text,
        doc_id="sixteen",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert report.run.analysis_request_count == 2
    assert report.run.repair_request_count == 2
    assert report.run.chunk_section_counts == [8, 8]
    assert report.run.coverage_complete is True
    assert len(provider.calls) == 4
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in provider.calls[1][0].content
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in provider.calls[3][0].content
    assert report.run.repair_request_count <= report.run.analysis_request_count


def test_two_chunks_each_semantic_correction_usage_and_cost(
    restore_mock_provider, monkeypatch
):
    text, bad0, fix0, bad1, fix1 = _sixteen_cross_chunk_payloads()
    provider = _RecordingProvider([bad0, fix0, bad1, fix1])
    register_provider(provider)
    monkeypatch.setitem(
        MODEL_PRICING_USD_PER_MTIME,
        "mock:mock-model",
        {"input": 1.0, "output": 2.0},
    )
    progress: list[str] = []
    report = run_scan(
        text,
        doc_id="sixteen",
        corpus="w3c",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        on_progress=progress.append,
    )
    assert report.run.analysis_request_count == 2
    assert report.run.repair_request_count == 2
    assert report.run.chunk_section_counts == [8, 8]
    assert report.run.coverage_complete is True
    assert len(provider.calls) == 4
    assert report.run.repair_request_count <= report.run.analysis_request_count
    # Exactly four logical completions: P1, C1, P2, C2.
    assert [len(c) for c in provider.calls] == [2, 2, 2, 2]
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in provider.calls[1][0].content
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() in provider.calls[3][0].content
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in provider.calls[0][0].content
    assert SEMANTIC_CORRECTION_PREAMBLE.strip() not in provider.calls[2][0].content
    assert report.actual_usage is not None
    assert report.actual_usage.input_tokens == 12 + 13 + 14 + 15
    assert report.actual_usage.output_tokens == 8 + 9 + 10 + 11
    assert report.actual_usage.reasoning_tokens == 1 + 2 + 3 + 4
    expected_cost, _ = estimate_cost_usd_for_requests(
        provider="mock",
        model="mock-model",
        requests=[(12, 8), (13, 9), (14, 10), (15, 11)],
    )
    assert expected_cost is not None
    assert report.actual_cost_usd == expected_cost
    for canary in (BODY_CANARY, PROMPT_CANARY, DOC_CANARY):
        assert canary not in "\n".join(progress)


def test_correction_provider_failure_exception_graph(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    provider = _RecordingProvider([bad, _raise_contaminated_provider])
    register_provider(provider)
    raw_path = tmp_path / "graph.raw.txt"
    progress: list[str] = []
    with pytest.raises(SemanticCorrectionError) as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
            on_progress=progress.append,
        )
    err = caught.value
    assert str(err) == "Semantic evidence correction failed"
    _assert_clean_terminal_exception(err, "\n".join(progress))
    assert PRIMARY_RAW_MARKER not in str(err)
    assert PRIMARY_RAW_MARKER not in repr(err)
    assert PRIMARY_RAW_MARKER not in "\n".join(progress)
    text = raw_path.read_text(encoding="utf-8")
    assert PRIMARY_RAW_MARKER in text
    assert BODY_CANARY not in text
    mode = stat.S_IMODE(raw_path.stat().st_mode)
    assert mode == 0o600
    assert not raw_path.is_symlink()


def test_correction_refusal_or_unexpected_shape_sanitized(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}

    def _boom(_messages):
        _raise_contaminated_provider()

    provider = _RecordingProvider([bad, _boom])
    register_provider(provider)
    raw_path = tmp_path / "shape.raw.txt"
    with pytest.raises(SemanticCorrectionError) as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
        )
    _assert_clean_terminal_exception(caught.value)
    assert PRIMARY_RAW_MARKER in raw_path.read_text(encoding="utf-8")


def test_correction_truncation_sanitized_exception_graph(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    trunc_raw = f"corr-partial-{BODY_CANARY}"

    class NestedTrunc(ModelOutputTruncatedError):
        pass

    def _trunc(_messages):
        try:
            raise _ContaminatedProviderError()
        except _ContaminatedProviderError:
            raise NestedTrunc("dirty truncation", raw=trunc_raw)

    provider = _RecordingProvider([bad, _trunc])
    register_provider(provider)
    raw_path = tmp_path / "trunc.raw.txt"
    progress: list[str] = []
    with pytest.raises(ModelOutputTruncatedError) as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
            on_progress=progress.append,
        )
    err = caught.value
    assert type(err) is ModelOutputTruncatedError
    assert str(err) == "Model output truncated: completion token limit reached"
    assert err.__cause__ is None
    assert err.__context__ is None
    _assert_clean_terminal_exception(err, "\n".join(progress))
    # Raw-on-error keeps truncated correction text (may include model canaries).
    assert raw_path.read_text(encoding="utf-8") == trunc_raw
    assert PRIMARY_RAW_MARKER not in raw_path.read_text(encoding="utf-8")
    assert BODY_CANARY not in str(err)
    assert BODY_CANARY not in "\n".join(progress)


def test_correction_invalid_response_exception_graph(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    invalid = f"not-json {BODY_CANARY} {{{{ "
    provider = _RecordingProvider([bad, invalid])
    register_provider(provider)
    raw_path = tmp_path / "invalid.raw.txt"
    progress: list[str] = []
    with pytest.raises(FindingParseError) as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(raw_path),
            on_progress=progress.append,
        )
    err = caught.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert BODY_CANARY not in str(err)
    assert BODY_CANARY not in repr(err)
    assert BODY_CANARY not in "\n".join(progress)
    assert BODY_CANARY in raw_path.read_text(encoding="utf-8")
    assert not isinstance(err.raw, (httpx.Request, httpx.Response))


def test_raw_save_failure_does_not_replace_terminal_error(
    tmp_path: Path, restore_mock_provider
):
    bad = {"findings": [_item("S0002", ALPHA_QUOTE, title=PRIMARY_RAW_MARKER)]}
    provider = _RecordingProvider([bad, _raise_contaminated_provider])
    register_provider(provider)
    # Symlink destination: save fails; terminal error must remain correction failure.
    target = tmp_path / "target.txt"
    target.write_text("old", encoding="utf-8")
    link = tmp_path / "link.raw.txt"
    os.symlink(target, link)
    progress: list[str] = []
    with pytest.raises(SemanticCorrectionError) as caught:
        run_scan(
            _two_section_text(),
            doc_id="corr-2",
            corpus="w3c",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            save_raw_on_error=str(link),
            on_progress=progress.append,
        )
    err = caught.value
    assert str(err) == "Semantic evidence correction failed"
    _assert_clean_terminal_exception(err, "\n".join(progress))
    assert any("could not be saved" in msg for msg in progress)
    assert target.read_text(encoding="utf-8") == "old"
