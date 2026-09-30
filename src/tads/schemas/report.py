# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Scan report schema — canonical JSON shape for Phase 1 outputs."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from tads.schemas.cost import CostEstimate, TokenUsage
from tads.schemas.findings import Finding
from tads.schemas.horizon import coerce_legacy_claimed_horizon

_LEGACY_REPORT_SCHEMAS = frozenset({"0.1.0", "0.2.0"})


class AnalysisMode(StrEnum):
    WHOLE_DOCUMENT = "whole_document"
    SECTION_AWARE = "section_aware"
    HYBRID = "hybrid"


class PrivacyMode(StrEnum):
    EPHEMERAL = "ephemeral"
    PERSIST_OUTPUTS = "persist_outputs"
    WORKSPACE = "workspace"


class DocumentIdentity(BaseModel):
    corpus: str
    doc_id: str
    title: Optional[str] = None
    version: Optional[str] = None
    source_uri: Optional[str] = None
    source_path: Optional[str] = None
    retrieved_uri: Optional[str] = None
    content_sha256: Optional[str] = None
    media_type: Optional[str] = None


class RunMetadata(BaseModel):
    scanner_version: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    analysis_mode: Optional[AnalysisMode] = None
    privacy_mode: PrivacyMode = PrivacyMode.EPHEMERAL
    prompt_framework_version: str = "0.1.0"
    # Analysis scope (post-parse filters/caps)
    include_front_matter: bool = False
    include_index_and_acknowledgments: bool = False
    sections_total: Optional[int] = None
    sections_analyzed: Optional[int] = None
    skipped_front_matter_sections: Optional[int] = None
    skipped_index_ack_sections: Optional[int] = None
    scope_truncated: bool = False
    max_sections: Optional[int] = None
    max_chars: Optional[int] = None
    max_input_tokens: Optional[int] = None
    scope_notes: list[str] = Field(default_factory=list)
    # Coverage vs eligible text (after skip filters; before/after caps).
    # Optional so older saved reports remain loadable.
    eligible_sections: Optional[int] = None
    eligible_chars: Optional[int] = None
    analyzed_chars: Optional[int] = None
    document_chars: Optional[int] = None
    # Sampling / generation parameters actually set by TADS (not provider defaults).
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None


class Report(BaseModel):
    """One scan report. JSON is canonical; Markdown is a projection."""

    schema_version: str = "0.3.0"
    document: DocumentIdentity
    run: RunMetadata
    cost_estimate: Optional[CostEstimate] = None
    actual_usage: Optional[TokenUsage] = None
    actual_cost_usd: Optional[float] = None
    findings: list[Finding] = Field(default_factory=list)
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_claimed_horizons(cls, data: Any) -> Any:
        """Accept schema 0.1.0/0.2.0 scalar claimed_horizon on load only."""
        if not isinstance(data, dict):
            return data
        if data.get("schema_version") not in _LEGACY_REPORT_SCHEMAS:
            return data
        findings = data.get("findings")
        if not isinstance(findings, list):
            return data
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            _upgrade_legacy_claimed_horizon(finding.get("time_representation"))
            horizon = finding.get("horizon_validation")
            _upgrade_legacy_claimed_horizon(horizon)
            if isinstance(horizon, dict):
                _upgrade_legacy_claimed_horizon(horizon.get("signed_interpretation"))
                _upgrade_legacy_claimed_horizon(horizon.get("unsigned_interpretation"))
        return data

    def finding_by_id(self, finding_id: str) -> Optional[Finding]:
        for finding in self.findings:
            if finding.id == finding_id:
                return finding
        return None


def _upgrade_legacy_claimed_horizon(container: Any) -> None:
    if not isinstance(container, dict) or "claimed_horizon" not in container:
        return
    raw = container["claimed_horizon"]
    if raw is None or isinstance(raw, dict):
        return
    container["claimed_horizon"] = coerce_legacy_claimed_horizon(raw)
