# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Serializable models for deterministic horizon validation."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Optional, Union

from pydantic import BaseModel, Field

ClaimedHorizon = Union[datetime, date]


class EpochKind(StrEnum):
    """Conventional epoch names; ``other`` requires an explicit datetime."""

    UNIX = "unix"
    NTP = "ntp"
    GPS = "gps"
    MJD = "mjd"
    NTFS = "ntfs"
    UUID = "uuid"
    TAI_1958 = "tai_1958"
    OTHER = "other"


class TimeRepresentationParams(BaseModel):
    """
    Structured time-representation parameters (from LLM or manual edit).

    Use null when the document does not establish a value. Do not invent
    widths, epochs, units, or signedness.

    Prefer ``epoch_kind`` for conventional epochs (unix, ntp, mjd, …). Use
    ``epoch`` datetime for non-standard epochs (``epoch_kind=other``) or as a
    legacy/explicit instant. Named kinds resolve deterministically to nominal
    civil UTC midnights — not leap-second timescale conversions.
    """

    width_bits: Optional[int] = None
    signed: Optional[bool] = None
    epoch_kind: Optional[EpochKind] = None
    epoch: Optional[datetime] = None
    unit: Optional[str] = None
    ticks_per_second: Optional[float] = None
    claimed_horizon: Optional[ClaimedHorizon] = None
    rollover_behavior: Optional[str] = None


class HorizonValidation(BaseModel):
    """
    Structured deterministic horizon / range check result.

    Separate from semantic candidate status: verifying arithmetic does not
    confirm that a candidate is a standards defect.

    When signedness is unresolved, ``status`` is ``ambiguous_signedness`` and
    both ``signed_interpretation`` and ``unsigned_interpretation`` may be set.
    Nested interpretations are one level deep only.
    """

    validation_type: str = "fixed_width_epoch_range"
    status: str
    minimum_value: Optional[int] = None
    maximum_value: Optional[int] = None
    earliest_representable: Optional[datetime] = None
    last_representable: Optional[datetime] = None
    first_out_of_range: Optional[datetime] = None
    claimed_horizon: Optional[ClaimedHorizon] = None
    claim_consistent: Optional[bool] = None
    notes: list[str] = Field(default_factory=list)
    signedness_resolved: Optional[bool] = None
    signed_interpretation: Optional[HorizonValidation] = None
    unsigned_interpretation: Optional[HorizonValidation] = None
