# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Scan report schema — canonical JSON shape for Phase 1 outputs."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from tads.schemas.cost import CostEstimate, TokenUsage
from tads.schemas.findings import Finding
from tads.schemas.horizon import coerce_legacy_claimed_horizon

# claimed_horizon scalar coercion applies only to these versions.
_LEGACY_REPORT_SCHEMAS = frozenset({"0.1.0", "0.2.0"})
# Reports that predate structured request-batching provenance.
PRE_BATCHING_REPORT_SCHEMAS = frozenset({"0.1.0", "0.2.0", "0.3.0", "0.4.0"})
BATCHING_RUN_FIELDS = (
    "request_batching",
    "analysis_request_count",
    "repair_request_count",
    "chunk_section_counts",
    "coverage_complete",
)

# Effective reasoning effort recorded on new scans (schema 0.4.0+).
REASONING_EFFORT_VALUES = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)


class AnalysisMode(StrEnum):
    WHOLE_DOCUMENT = "whole_document"
    SECTION_AWARE = "section_aware"
    HYBRID = "hybrid"


class RequestBatching(StrEnum):
    """How analyzed content was batched into primary model requests."""

    WHOLE_DOCUMENT = "whole_document"
    PER_SECTION = "per_section"
    SECTION_CHUNKS = "section_chunks"


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


def _reject_bool_int(value: Any, *, field: str) -> Any:
    """Reject bool before Pydantic coerces True/False to 1/0."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean")
    return value


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
    # Effective reasoning effort for models that support it (schema 0.4.0+).
    # Null for non-reasoning models and for legacy reports that omit the field.
    reasoning_effort: Optional[str] = None
    # Structured execution provenance (schema 0.5.0). Null/absent on legacy loads.
    request_batching: Optional[RequestBatching] = None
    analysis_request_count: Optional[int] = Field(default=None, ge=0)
    repair_request_count: Optional[int] = Field(default=None, ge=0)
    chunk_section_counts: Optional[list[int]] = None
    coverage_complete: Optional[bool] = None

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_reasoning_effort(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or value not in REASONING_EFFORT_VALUES:
            allowed = ", ".join(sorted(REASONING_EFFORT_VALUES))
            raise ValueError(
                f"reasoning_effort must be one of {allowed} or null"
            )
        return value

    @field_validator("analysis_request_count", "repair_request_count", mode="before")
    @classmethod
    def _reject_bool_request_counts(cls, value: Any) -> Any:
        if value is None:
            return None
        return _reject_bool_int(value, field="request count")

    @field_validator("chunk_section_counts", mode="before")
    @classmethod
    def _validate_chunk_section_counts_before(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("chunk_section_counts must be a list of integers")
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(
                    "chunk_section_counts entries must be positive integers"
                )
            if item < 1:
                raise ValueError(
                    "chunk_section_counts entries must be positive integers"
                )
        return value


class Report(BaseModel):
    """One scan report. JSON is canonical; Markdown is a projection."""

    schema_version: str = "0.5.0"
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
    def _coerce_legacy_and_strip_batching(cls, data: Any) -> Any:
        """Legacy load: coerce claimed_horizon; never invent batching provenance."""
        if not isinstance(data, dict):
            return data
        version = data.get("schema_version")
        if version in PRE_BATCHING_REPORT_SCHEMAS:
            run = data.get("run")
            if isinstance(run, dict):
                for key in BATCHING_RUN_FIELDS:
                    run.pop(key, None)
        if version not in _LEGACY_REPORT_SCHEMAS:
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
                _upgrade_legacy_claimed_horizon(
                    horizon.get("unsigned_interpretation")
                )
        return data

    @model_validator(mode="after")
    def _validate_schema_050_batching(self) -> "Report":
        """Require present, typed, cross-field-consistent batching on 0.5.0."""
        if self.schema_version in PRE_BATCHING_REPORT_SCHEMAS:
            # Ensure in-memory legacy reports never retain 0.5.0 batching facts.
            if any(
                getattr(self.run, key) is not None for key in BATCHING_RUN_FIELDS
            ):
                return self.model_copy(
                    update={
                        "run": self.run.model_copy(
                            update={key: None for key in BATCHING_RUN_FIELDS}
                        )
                    }
                )
            return self
        if self.schema_version != "0.5.0":
            return self
        run = self.run
        if run.request_batching is None:
            raise ValueError("request_batching is required for schema 0.5.0")
        if run.analysis_request_count is None:
            raise ValueError("analysis_request_count is required for schema 0.5.0")
        if run.repair_request_count is None:
            raise ValueError("repair_request_count is required for schema 0.5.0")
        if run.chunk_section_counts is None:
            raise ValueError("chunk_section_counts is required for schema 0.5.0")
        if run.coverage_complete is None:
            raise ValueError("coverage_complete is required for schema 0.5.0")

        analysis_n = run.analysis_request_count
        repair_n = run.repair_request_count
        counts = run.chunk_section_counts
        if analysis_n < 0 or repair_n < 0:
            raise ValueError("request counts must be nonnegative")
        if repair_n > analysis_n:
            raise ValueError(
                "repair_request_count cannot exceed analysis_request_count"
            )
        if analysis_n != len(counts):
            raise ValueError(
                "analysis_request_count must equal len(chunk_section_counts)"
            )
        if any(
            (not isinstance(item, int)) or isinstance(item, bool) or item < 1
            for item in counts
        ):
            raise ValueError(
                "chunk_section_counts entries must be positive integers"
            )

        mode = run.analysis_mode
        batching = run.request_batching
        if batching == RequestBatching.WHOLE_DOCUMENT:
            if mode is not None and mode != AnalysisMode.WHOLE_DOCUMENT:
                raise ValueError(
                    "whole_document batching requires whole_document analysis_mode"
                )
        elif batching == RequestBatching.PER_SECTION:
            if mode is not None and mode != AnalysisMode.SECTION_AWARE:
                raise ValueError(
                    "per_section batching requires section_aware analysis_mode"
                )
            if any(item != 1 for item in counts):
                raise ValueError(
                    "per_section batching requires every chunk_section_counts entry to be 1"
                )
        elif batching == RequestBatching.SECTION_CHUNKS:
            if mode is not None and mode != AnalysisMode.SECTION_AWARE:
                raise ValueError(
                    "section_chunks batching requires section_aware analysis_mode"
                )
            if not any(item > 1 for item in counts):
                raise ValueError(
                    "section_chunks batching requires at least one chunk with "
                    "more than one section"
                )

        if mode == AnalysisMode.SECTION_AWARE:
            analyzed = run.sections_analyzed
            if analyzed is None:
                raise ValueError(
                    "sections_analyzed is required for section_aware schema 0.5.0"
                )
            if sum(counts) != analyzed:
                raise ValueError(
                    "sum(chunk_section_counts) must equal sections_analyzed"
                )
        return self

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


def strip_batching_fields_for_legacy_dump(payload: dict[str, Any]) -> None:
    """Remove schema 0.5.0 batching keys when serializing pre-0.5.0 reports."""
    run = payload.get("run")
    if not isinstance(run, dict):
        return
    for key in BATCHING_RUN_FIELDS:
        run.pop(key, None)
