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
# Reports that predate adaptive-recovery / partial-execution provenance.
PRE_RECOVERY_REPORT_SCHEMAS = frozenset(
    {"0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"}
)
CURRENT_REPORT_SCHEMA = "0.6.0"
BATCHING_RUN_FIELDS = (
    "request_batching",
    "analysis_request_count",
    "repair_request_count",
    "chunk_section_counts",
    "coverage_complete",
)
RECOVERY_RUN_FIELDS = (
    "analysis_status",
    "sections_succeeded",
    "succeeded_chars",
    "sections_failed",
    "failed_chars",
    "failed_section_ids",
    "unanalyzed_sections",
    "recovery_split_count",
    "recovery_request_count",
    "total_provider_request_count",
)
UNANALYZED_FAILURE_CATEGORIES = frozenset(
    {
        "invalid_model_output",
        "truncated_output",
        "recovery_limit_exhausted",
    }
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


class AnalysisStatus(StrEnum):
    """Whether every planned analyzed section completed successfully."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class UnanalyzedFailureCategory(StrEnum):
    """Bounded public category for a section that could not be analyzed."""

    INVALID_MODEL_OUTPUT = "invalid_model_output"
    TRUNCATED_OUTPUT = "truncated_output"
    RECOVERY_LIMIT_EXHAUSTED = "recovery_limit_exhausted"


class UnanalyzedSection(BaseModel):
    """Sanitized provenance for one unrecoverable analyzed-scope section."""

    section_id: str
    category: UnanalyzedFailureCategory


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
    total_provider_request_count: Optional[int] = Field(default=None, ge=0)
    chunk_section_counts: Optional[list[int]] = None
    coverage_complete: Optional[bool] = None
    # Adaptive recovery / partial execution provenance (schema 0.6.0).
    # Null/absent on legacy loads; never invented for pre-0.6.0 reports.
    analysis_status: Optional[AnalysisStatus] = None
    sections_succeeded: Optional[int] = Field(default=None, ge=0)
    succeeded_chars: Optional[int] = Field(default=None, ge=0)
    sections_failed: Optional[int] = Field(default=None, ge=0)
    failed_chars: Optional[int] = Field(default=None, ge=0)
    failed_section_ids: Optional[list[str]] = None
    unanalyzed_sections: Optional[list[UnanalyzedSection]] = None
    recovery_split_count: Optional[int] = Field(default=None, ge=0)
    recovery_request_count: Optional[int] = Field(default=None, ge=0)

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

    @field_validator(
        "analysis_request_count",
        "repair_request_count",
        "total_provider_request_count",
        "sections_succeeded",
        "succeeded_chars",
        "sections_failed",
        "failed_chars",
        "recovery_split_count",
        "recovery_request_count",
        mode="before",
    )
    @classmethod
    def _reject_bool_request_counts(cls, value: Any) -> Any:
        if value is None:
            return None
        return _reject_bool_int(value, field="request count")

    @field_validator("failed_section_ids", mode="before")
    @classmethod
    def _validate_failed_section_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("failed_section_ids must be a list of strings")
        for item in value:
            if not isinstance(item, str) or not item:
                raise ValueError(
                    "failed_section_ids entries must be nonempty strings"
                )
        if len(set(value)) != len(value):
            raise ValueError("failed_section_ids must not contain duplicates")
        return value

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

    schema_version: str = CURRENT_REPORT_SCHEMA
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
        if version in PRE_RECOVERY_REPORT_SCHEMAS:
            run = data.get("run")
            if isinstance(run, dict):
                for key in RECOVERY_RUN_FIELDS:
                    run.pop(key, None)
        effective = version if version is not None else CURRENT_REPORT_SCHEMA
        if effective == CURRENT_REPORT_SCHEMA:
            run = data.get("run")
            if isinstance(run, RunMetadata):
                dumped = run.model_dump()
                data["run"] = dumped
                run = dumped
            if isinstance(run, dict):
                if run.get("total_provider_request_count") is None:
                    analysis_n = run.get("analysis_request_count")
                    repair_n = run.get("repair_request_count")
                    if (
                        isinstance(analysis_n, int)
                        and not isinstance(analysis_n, bool)
                        and isinstance(repair_n, int)
                        and not isinstance(repair_n, bool)
                    ):
                        run["total_provider_request_count"] = analysis_n + repair_n
                if run.get("analysis_status") is None:
                    analyzed = run.get("sections_analyzed")
                    chars = run.get("analyzed_chars")
                    run["analysis_status"] = AnalysisStatus.COMPLETE.value
                    if run.get("sections_succeeded") is None:
                        run["sections_succeeded"] = (
                            analyzed
                            if isinstance(analyzed, int)
                            and not isinstance(analyzed, bool)
                            else 0
                        )
                    if run.get("succeeded_chars") is None:
                        run["succeeded_chars"] = (
                            chars
                            if isinstance(chars, int) and not isinstance(chars, bool)
                            else 0
                        )
                    if run.get("sections_failed") is None:
                        run["sections_failed"] = 0
                    if run.get("failed_chars") is None:
                        run["failed_chars"] = 0
                    if run.get("failed_section_ids") is None:
                        run["failed_section_ids"] = []
                    if run.get("unanalyzed_sections") is None:
                        run["unanalyzed_sections"] = []
                    if run.get("recovery_split_count") is None:
                        run["recovery_split_count"] = 0
                    if run.get("recovery_request_count") is None:
                        run["recovery_request_count"] = 0
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
        """Require present, typed, cross-field-consistent batching on 0.5.0+."""
        if self.schema_version in PRE_BATCHING_REPORT_SCHEMAS:
            # Ensure in-memory legacy reports never retain 0.5.0 batching facts.
            clearing = {
                key: None
                for key in BATCHING_RUN_FIELDS
                if getattr(self.run, key) is not None
            }
            if self.schema_version in PRE_RECOVERY_REPORT_SCHEMAS:
                clearing.update(
                    {
                        key: None
                        for key in RECOVERY_RUN_FIELDS
                        if getattr(self.run, key) is not None
                    }
                )
            if clearing:
                return self.model_copy(
                    update={"run": self.run.model_copy(update=clearing)}
                )
            return self
        if self.schema_version == "0.5.0":
            # Strip any recovery fields that a 0.5.0 payload should not carry.
            if any(
                getattr(self.run, key) is not None for key in RECOVERY_RUN_FIELDS
            ):
                stripped = self.model_copy(
                    update={
                        "run": self.run.model_copy(
                            update={key: None for key in RECOVERY_RUN_FIELDS}
                        )
                    }
                )
                return stripped._validate_schema_050_batching()
            self._validate_batching_fields(schema_label="0.5.0", require_count_match=True)
            return self
        if self.schema_version == CURRENT_REPORT_SCHEMA:
            self._validate_batching_fields(
                schema_label=CURRENT_REPORT_SCHEMA,
                require_count_match=False,
            )
            self._validate_recovery_fields()
            return self
        return self

    def _validate_batching_fields(
        self,
        *,
        schema_label: str,
        require_count_match: bool,
    ) -> None:
        run = self.run
        if run.request_batching is None:
            raise ValueError(f"request_batching is required for schema {schema_label}")
        if run.analysis_request_count is None:
            raise ValueError(
                f"analysis_request_count is required for schema {schema_label}"
            )
        if run.repair_request_count is None:
            raise ValueError(
                f"repair_request_count is required for schema {schema_label}"
            )
        if run.chunk_section_counts is None:
            raise ValueError(
                f"chunk_section_counts is required for schema {schema_label}"
            )
        if run.coverage_complete is None:
            raise ValueError(f"coverage_complete is required for schema {schema_label}")

        analysis_n = run.analysis_request_count
        repair_n = run.repair_request_count
        counts = run.chunk_section_counts
        if analysis_n < 0 or repair_n < 0:
            raise ValueError("request counts must be nonnegative")
        if repair_n > analysis_n:
            raise ValueError(
                "repair_request_count cannot exceed analysis_request_count"
            )
        if schema_label == CURRENT_REPORT_SCHEMA:
            total_n = run.total_provider_request_count
            if total_n is None:
                raise ValueError(
                    "total_provider_request_count is required for schema 0.6.0"
                )
            if total_n != analysis_n + repair_n:
                raise ValueError(
                    "total_provider_request_count must equal "
                    "analysis_request_count + repair_request_count"
                )
        if require_count_match and analysis_n != len(counts):
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
                    f"sections_analyzed is required for section_aware schema {schema_label}"
                )
            if sum(counts) != analyzed:
                raise ValueError(
                    "sum(chunk_section_counts) must equal sections_analyzed"
                )

    def _validate_recovery_fields(self) -> None:
        run = self.run
        if run.analysis_status is None:
            raise ValueError("analysis_status is required for schema 0.6.0")
        for field_name in (
            "sections_succeeded",
            "succeeded_chars",
            "sections_failed",
            "failed_chars",
            "recovery_split_count",
            "recovery_request_count",
        ):
            if getattr(run, field_name) is None:
                raise ValueError(f"{field_name} is required for schema 0.6.0")
        if run.failed_section_ids is None:
            raise ValueError("failed_section_ids is required for schema 0.6.0")
        if run.unanalyzed_sections is None:
            raise ValueError("unanalyzed_sections is required for schema 0.6.0")
        if run.sections_failed != len(run.failed_section_ids):
            raise ValueError("sections_failed must equal len(failed_section_ids)")
        if run.sections_failed != len(run.unanalyzed_sections):
            raise ValueError("sections_failed must equal len(unanalyzed_sections)")
        if list(run.failed_section_ids) != [
            item.section_id for item in run.unanalyzed_sections
        ]:
            raise ValueError(
                "failed_section_ids must match unanalyzed_sections section_id order"
            )
        if run.analysis_status == AnalysisStatus.PARTIAL:
            if run.sections_failed < 1:
                raise ValueError("partial analysis_status requires sections_failed > 0")
            if run.coverage_complete:
                raise ValueError(
                    "coverage_complete must be false when analysis_status is partial"
                )
        if run.analysis_status == AnalysisStatus.COMPLETE and run.sections_failed != 0:
            raise ValueError(
                "complete analysis_status requires sections_failed to be 0"
            )
        if run.analysis_status == AnalysisStatus.COMPLETE and run.failed_section_ids:
            raise ValueError(
                "complete analysis_status cannot contain failed_section_ids"
            )
        if run.coverage_complete and run.analysis_status != AnalysisStatus.COMPLETE:
            raise ValueError(
                "coverage_complete requires analysis_status complete"
            )
        if run.coverage_complete and run.sections_failed:
            raise ValueError(
                "coverage_complete cannot be true when planned sections failed"
            )
        analyzed = run.sections_analyzed
        if analyzed is not None:
            succeeded = run.sections_succeeded or 0
            failed = run.sections_failed or 0
            if succeeded + failed != analyzed:
                raise ValueError(
                    "sections_succeeded + sections_failed must equal sections_analyzed"
                )
        if (
            run.analyzed_chars is not None
            and run.succeeded_chars is not None
            and run.failed_chars is not None
        ):
            if run.succeeded_chars + run.failed_chars != run.analyzed_chars:
                raise ValueError(
                    "succeeded_chars + failed_chars must equal analyzed_chars"
                )
        if run.recovery_split_count is not None and run.recovery_split_count < 0:
            raise ValueError("recovery counts must be nonnegative")
        if run.recovery_request_count is not None and run.recovery_request_count < 0:
            raise ValueError("recovery counts must be nonnegative")
        total_n = run.total_provider_request_count
        if (
            total_n is not None
            and run.recovery_request_count is not None
            and run.recovery_request_count > total_n
        ):
            raise ValueError(
                "recovery_request_count cannot exceed total_provider_request_count"
            )

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


def strip_recovery_fields_for_legacy_dump(payload: dict[str, Any]) -> None:
    """Remove schema 0.6.0 recovery keys when serializing pre-0.6.0 reports."""
    run = payload.get("run")
    if not isinstance(run, dict):
        return
    for key in RECOVERY_RUN_FIELDS:
        run.pop(key, None)
