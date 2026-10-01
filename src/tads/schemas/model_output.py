# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for the model-facing findings JSON contract.

These describe provider-native structured-output schemas. Local parsing and
validation in ``tads.pipeline.parse_findings`` remain authoritative and
stricter (calendar validity, lexical horizon forms, duplicate keys).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from tads.schemas.findings import FindingType, ScopeRelevance, Severity
from tads.schemas.horizon import ClaimedHorizon, EpochKind
from tads.schemas.taxonomy import Confidence, TimeDomain

MODEL_TIME_UNITS = (
    "seconds",
    "milliseconds",
    "microseconds",
    "nanoseconds",
    "days",
    "weeks",
    "ticks",
)

TimeUnit = Literal[
    "seconds",
    "milliseconds",
    "microseconds",
    "nanoseconds",
    "days",
    "weeks",
    "ticks",
]


class ModelEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1)
    note: Optional[str] = None


class ModelTimeRepresentation(BaseModel):
    """Document-established counter parameters. All fields may be null."""

    model_config = ConfigDict(extra="forbid")

    width_bits: Optional[int] = None
    signed: Optional[bool] = None
    epoch_kind: Optional[EpochKind] = None
    epoch: Optional[str] = None
    unit: Optional[TimeUnit] = None
    ticks_per_second: Optional[float] = None
    claimed_horizon: Optional[ClaimedHorizon] = None
    rollover_behavior: Optional[str] = None


class ModelFindingItem(BaseModel):
    """One model-authored finding. TADS-owned fields are not included."""

    model_config = ConfigDict(extra="forbid")

    finding_type: FindingType
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: Severity
    confidence: Confidence
    domains: list[TimeDomain]
    evidence: list[ModelEvidence]
    machine_interpretation: str = Field(min_length=1)
    scope_relevance: ScopeRelevance
    scope_rationale: str = Field(min_length=1)
    section_id: Optional[str] = None
    section_title: Optional[str] = None
    recommendation_level1: Optional[str] = None
    time_representation: Optional[ModelTimeRepresentation] = None


class ModelFindingsResponse(BaseModel):
    """Top-level model JSON object: ``{"findings": [...]}``."""

    model_config = ConfigDict(extra="forbid")

    findings: list[ModelFindingItem]
