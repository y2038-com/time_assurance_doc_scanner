# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Scope relevance parse, orthogonality, and Markdown filtering."""

from __future__ import annotations

from tads.export import report_to_markdown
from tads.pipeline.parse_findings import parse_findings_payload
from tads.schemas.assurance import AssuranceStatus, derive_assurance_status
from tads.schemas.findings import (
    Disposition,
    Finding,
    FindingType,
    ScopeRelevance,
    Severity,
    ValidationStatus,
)
from tads.schemas.report import DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence


def _base_item(**overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Sample",
        "description": "desc",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["other"],
        "evidence": [{"quote": "enough quote text here for verification"}],
        "machine_interpretation": "interp",
        "recommendation_level1": None,
    }
    item.update(overrides)
    return item


def _parse_one(**overrides):
    findings = parse_findings_payload({"findings": [_base_item(**overrides)]})
    assert len(findings) == 1
    return findings[0]


# --- Phase D classification fixtures (crafted payloads; no live LLM) ---


def test_unix32_rollover_is_core():
    f = _parse_one(
        title="Unix 32-bit signed overflow",
        description="time_t wraps in 2038.",
        severity="high",
        domains=["y2038", "rollover"],
        scope_relevance="core",
        scope_rationale=(
            "This matters to time assurance because a fixed-width signed second "
            "counter overflows on a known horizon."
        ),
    )
    assert f.scope_relevance == ScopeRelevance.CORE


def test_ntp_era_ambiguity_is_core():
    f = _parse_one(
        title="NTP era ambiguity",
        description="32-bit seconds without era context.",
        domains=["y2036"],
        scope_relevance="core",
        scope_rationale="Era disambiguation is required to interpret timestamps correctly.",
    )
    assert f.scope_relevance == ScopeRelevance.CORE


def test_wrong_claimed_horizon_is_core():
    f = _parse_one(
        title="Incorrect wrap date stated",
        description="Document claims wrap in 2035; arithmetic says 2036.",
        scope_relevance="core",
        scope_rationale="Incorrect horizon claims affect long-term operational planning.",
    )
    assert f.scope_relevance == ScopeRelevance.CORE


def test_loss_of_era_in_storage_is_supporting():
    f = _parse_one(
        title="Era not persisted",
        description="On-wire era is discarded when storing timestamps.",
        scope_relevance="supporting",
        scope_rationale=(
            "Persistence of era state materially affects recovering correct "
            "absolute time after storage."
        ),
    )
    assert f.scope_relevance == ScopeRelevance.SUPPORTING


def test_wide_to_narrow_conversion_is_supporting():
    f = _parse_one(
        title="64-bit to 32-bit truncation",
        description="Wide timestamps truncated to 32-bit fields.",
        scope_relevance="supporting",
        scope_rationale="Narrowing conversion can recreate rollover risk for time values.",
    )
    assert f.scope_relevance == ScopeRelevance.SUPPORTING


def test_terminology_nit_is_incidental():
    f = _parse_one(
        title="UTC vs UT1 wording",
        description="Informative sentence uses UTC loosely.",
        severity="info",
        scope_relevance="incidental",
        scope_rationale="Editorial terminology without material assurance consequence.",
    )
    assert f.scope_relevance == ScopeRelevance.INCIDENTAL


def test_crypto_weakness_is_out_of_scope():
    f = _parse_one(
        title="MD5 RefID collision risk",
        description="IPv6 RefID uses MD5; collisions possible.",
        severity="high",
        finding_type="explicit_defect",
        domains=["other"],
        scope_relevance="out_of_scope",
        scope_rationale=(
            "Hash-identifier collision is a security/protocol concern without a "
            "causal link to time representation or long-horizon correctness."
        ),
    )
    assert f.scope_relevance == ScopeRelevance.OUT_OF_SCOPE
    assert f.severity == Severity.HIGH


def test_networking_defect_without_time_effect_is_out_of_scope():
    f = _parse_one(
        title="Missing IPv6 address family note",
        description="Packet layout omits address-family clarity.",
        severity="medium",
        scope_relevance="out_of_scope",
        scope_rationale="Networking packaging issue with no time-assurance effect.",
    )
    assert f.scope_relevance == ScopeRelevance.OUT_OF_SCOPE


def test_high_severity_non_time_security_still_out_of_scope():
    f = _parse_one(
        title="Unauthenticated control messages",
        description="Critical auth gap in control plane.",
        severity="critical",
        confidence="high",
        scope_relevance="out_of_scope",
        scope_rationale="Security severity does not make this a time-assurance finding.",
    )
    assert f.scope_relevance == ScopeRelevance.OUT_OF_SCOPE
    assert f.severity == Severity.CRITICAL
    assert f.confidence == Confidence.HIGH


def test_low_severity_rollover_docs_still_core():
    f = _parse_one(
        title="Rollover behavior not stated",
        description="Informative appendix omits wrap semantics.",
        severity="low",
        scope_relevance="core",
        scope_rationale="Missing rollover documentation is still a time-assurance gap.",
    )
    assert f.scope_relevance == ScopeRelevance.CORE
    assert f.severity == Severity.LOW


def test_rfc5905_style_era_core_and_md5_refid_out_of_scope():
    """RFC 5905–shaped labels without hard-coding by document id."""
    findings = parse_findings_payload(
        {
            "findings": [
                _base_item(
                    title="NTP 32-bit seconds / ~34-year era",
                    description="Seconds field wraps; era must be known.",
                    domains=["y2036", "rollover"],
                    scope_relevance="core",
                    scope_rationale="Fixed-width NTP seconds require era for absolute time.",
                ),
                _base_item(
                    title="MD5-based IPv6 RefID",
                    description="RefID derived from MD5 of IPv6 address.",
                    severity="high",
                    domains=["other"],
                    scope_relevance="out_of_scope",
                    scope_rationale="Identifier collision risk is not a time-assurance issue.",
                ),
            ]
        }
    )
    assert findings[0].scope_relevance == ScopeRelevance.CORE
    assert findings[1].scope_relevance == ScopeRelevance.OUT_OF_SCOPE


# --- Parse defaults / aliases ---


def test_missing_scope_defaults_to_core():
    f = _parse_one()
    assert f.scope_relevance == ScopeRelevance.CORE
    assert f.scope_rationale is None


def test_invalid_scope_defaults_to_core():
    f = _parse_one(scope_relevance="banana")
    assert f.scope_relevance == ScopeRelevance.CORE


def test_scope_aliases():
    assert _parse_one(scope_relevance="primary").scope_relevance == ScopeRelevance.CORE
    assert (
        _parse_one(scope_relevance="support").scope_relevance
        == ScopeRelevance.SUPPORTING
    )
    assert _parse_one(scope_relevance="oos").scope_relevance == ScopeRelevance.OUT_OF_SCOPE
    assert (
        _parse_one(scope_relevance="unrelated").scope_relevance
        == ScopeRelevance.OUT_OF_SCOPE
    )


# --- Orthogonality ---


def test_scope_does_not_change_severity_confidence_validation_disposition():
    f = _parse_one(
        severity="critical",
        confidence="high",
        scope_relevance="out_of_scope",
        scope_rationale="Not time-related.",
    )
    # Parse path does not set validation/disposition; defaults remain.
    assert f.severity == Severity.CRITICAL
    assert f.confidence == Confidence.HIGH
    assert f.validation_status == ValidationStatus.UNVERIFIED
    assert f.disposition == Disposition.NEW
    assert f.scope_relevance == ScopeRelevance.OUT_OF_SCOPE

    # Mutating scope on a rich finding must not rewrite other axes.
    finding = Finding(
        id="F-009",
        finding_type=FindingType.EXPLICIT_DEFECT,
        title="Auth",
        description="d",
        severity=Severity.CRITICAL,
        confidence=Confidence.HIGH,
        validation_status=ValidationStatus.VERIFIED,
        disposition=Disposition.ACCEPTED,
        scope_relevance=ScopeRelevance.CORE,
        scope_rationale="was core",
    )
    before = (
        finding.severity,
        finding.confidence,
        finding.validation_status,
        finding.disposition,
    )
    finding.scope_relevance = ScopeRelevance.OUT_OF_SCOPE
    finding.scope_rationale = "now oos"
    assert (
        finding.severity,
        finding.confidence,
        finding.validation_status,
        finding.disposition,
    ) == before
    assert derive_assurance_status(finding) == AssuranceStatus.HUMAN_CONFIRMED


def test_old_json_without_scope_loads_as_core():
    raw = """
    {
      "schema_version": "0.1.0",
      "document": {"corpus": "ietf", "doc_id": "RFC9999"},
      "run": {"scanner_version": "0.4.0"},
      "findings": [
        {
          "id": "F-001",
          "finding_type": "time_assurance_gap",
          "title": "Era",
          "description": "d",
          "severity": "medium",
          "confidence": "medium"
        }
      ]
    }
    """
    report = Report.model_validate_json(raw)
    assert report.findings[0].scope_relevance == ScopeRelevance.CORE


# --- Markdown presentation ---


def _report_with(*findings: Finding) -> Report:
    return Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC-SCOPE"),
        run=RunMetadata(scanner_version="0.0.0"),
        findings=list(findings),
    )


def _finding(fid: str, scope: ScopeRelevance, title: str) -> Finding:
    return Finding(
        id=fid,
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title=title,
        description=f"{title} body",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        scope_relevance=scope,
        scope_rationale=f"rationale for {fid}",
    )


def test_markdown_filters_and_summarizes_scope():
    report = _report_with(
        _finding("F-001", ScopeRelevance.CORE, "Era wrap"),
        _finding("F-002", ScopeRelevance.SUPPORTING, "Era persistence"),
        _finding("F-003", ScopeRelevance.INCIDENTAL, "Wording nit"),
        _finding("F-004", ScopeRelevance.OUT_OF_SCOPE, "MD5 RefID"),
    )
    md = report_to_markdown(report)
    assert "Candidates (JSON): **4**" in md
    assert "Scope: core=1, supporting=1, incidental=1, out_of_scope=1" in md
    assert "F-001: Era wrap" in md
    assert "F-002: Era persistence _(supporting)_" in md
    assert "## Incidental observations" in md
    assert "F-003: Wording nit _(incidental)_" in md
    assert "F-004" not in md
    assert "MD5 RefID" not in md
    assert "- **Scope:** `core`" in md
    assert "rationale for F-001" in md


def test_markdown_only_out_of_scope_shows_empty_primary():
    report = _report_with(
        _finding("F-010", ScopeRelevance.OUT_OF_SCOPE, "Crypto only"),
    )
    md = report_to_markdown(report)
    assert "No in-scope candidates to show" in md
    assert "JSON retains 1 item" in md
    assert "F-010" not in md
    dumped = report.model_dump_json()
    assert "F-010" in dumped
    assert "out_of_scope" in dumped
