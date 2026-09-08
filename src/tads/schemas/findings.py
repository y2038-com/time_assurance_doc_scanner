# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Finding schema — canonical unit of scanner output."""

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field

from tads.schemas.horizon import HorizonValidation, TimeRepresentationParams
from tads.schemas.taxonomy import Confidence, TimeDomain


class FindingType(StrEnum):
    EXPLICIT_DEFECT = "explicit_defect"
    INTERNAL_INCONSISTENCY = "internal_inconsistency"
    MISSING_DOCUMENTATION = "missing_documentation"
    IMPLIED_ASSUMPTION = "implied_assumption"
    TIME_ASSURANCE_GAP = "time_assurance_gap"
    LIFETIME_REPRESENTATION_MISMATCH = "lifetime_representation_mismatch"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ValidationStatus(StrEnum):
    """
    Outcome of a deterministic checker only — not human validation.

    ``verified`` means an automated check passed (public label: deterministically
    checked candidate). It is **not** a validated finding; that phrase is reserved
    for ``disposition=accepted`` (human-confirmed).
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class Disposition(StrEnum):
    """Human-review disposition (Phase 1). Phase 7 may add registry states."""

    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    EDITED = "edited"


class ScopeRelevance(StrEnum):
    """
    Relevance of a candidate to TADS's time-assurance mission.

    Independent of severity and confidence. ``out_of_scope`` does not mean
    technically unimportant — only that it is not a TADS time-assurance concern.
    """

    CORE = "core"
    SUPPORTING = "supporting"
    INCIDENTAL = "incidental"
    OUT_OF_SCOPE = "out_of_scope"


class FindingLocation(BaseModel):
    """
    Locator within analyzed document text.

    ``start_char`` / ``end_char`` use half-open ``[start, end)`` offsets into the
    **analyzed (scoped) document text** used for source verification — not raw
    PDF bytes or pre-scope file offsets. Both may be null when verification fails
    or the match is ambiguous (duplicate identical quotes).
    """

    section_id: Optional[str] = None
    section_title: Optional[str] = None
    clause: Optional[str] = None
    start_char: Optional[int] = Field(default=None, ge=0)
    end_char: Optional[int] = Field(default=None, ge=0)
    page: Optional[int] = Field(default=None, ge=1)


class Evidence(BaseModel):
    quote: str
    location: Optional[FindingLocation] = None
    note: Optional[str] = None


class Finding(BaseModel):
    """One time-assurance finding with evidence and human-review fields."""

    id: str
    finding_type: FindingType
    title: str
    description: str
    severity: Severity
    confidence: Confidence
    domains: list[TimeDomain] = Field(default_factory=list)
    location: Optional[FindingLocation] = None
    evidence: list[Evidence] = Field(default_factory=list)
    machine_interpretation: str = ""
    validation_status: ValidationStatus = ValidationStatus.UNVERIFIED
    validation_detail: Optional[str] = None
    time_representation: Optional[TimeRepresentationParams] = Field(
        default=None,
        description=(
            "Structured counter/epoch parameters for deterministic horizon checks. "
            "Null fields mean the document (or extractor) did not establish them."
        ),
    )
    horizon_validation: Optional[HorizonValidation] = Field(
        default=None,
        description=(
            "Result of the fixed-width/epoch calculator. Independent of disposition; "
            "arithmetic verification does not confirm a standards defect."
        ),
    )
    source_verified: bool = Field(
        default=False,
        description=(
            "True when at least one evidence quote was found in the analyzed "
            "document text (normalized substring match)."
        ),
    )
    source_verification_detail: Optional[str] = None
    recommendation_level1: Optional[str] = Field(
        default=None,
        description="Remediation direction only (Level 1).",
    )
    scope_relevance: ScopeRelevance = Field(
        default=ScopeRelevance.CORE,
        description=(
            "Relevance to time assurance (core/supporting/incidental/out_of_scope). "
            "Independent of severity and confidence. Default core for older reports."
        ),
    )
    scope_rationale: Optional[str] = Field(
        default=None,
        description="One or two sentences explaining the scope classification.",
    )
    disposition: Disposition = Disposition.NEW
    reviewer_notes: Optional[str] = None
