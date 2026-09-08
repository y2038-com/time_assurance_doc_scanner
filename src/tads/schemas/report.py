# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Scan report schema — canonical JSON shape for Phase 1 outputs."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field

from tads.schemas.cost import CostEstimate, TokenUsage
from tads.schemas.findings import Finding


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


class Report(BaseModel):
    """One scan report. JSON is canonical; Markdown is a projection."""

    schema_version: str = "0.1.0"
    document: DocumentIdentity
    run: RunMetadata
    cost_estimate: Optional[CostEstimate] = None
    actual_usage: Optional[TokenUsage] = None
    actual_cost_usd: Optional[float] = None
    findings: list[Finding] = Field(default_factory=list)
    reviewed_at: Optional[datetime] = None
    reviewer: Optional[str] = None

    def finding_by_id(self, finding_id: str) -> Optional[Finding]:
        for finding in self.findings:
            if finding.id == finding_id:
                return finding
        return None
