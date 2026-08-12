# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Serializable models for deterministic horizon validation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Union

from pydantic import BaseModel, Field

ClaimedHorizon = Union[datetime, date]


class TimeRepresentationParams(BaseModel):
    """
    Structured time-representation parameters (from LLM or manual edit).

    Use null when the document does not establish a value. Do not invent
    widths, epochs, units, or signedness.
    """

    width_bits: Optional[int] = None
    signed: Optional[bool] = None
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
