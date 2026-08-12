# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""
Deterministic fixed-width / epoch horizon calculator.

Model-independent: no LLM calls. Answers factual range and horizon questions
only; does not judge standards defects or compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from typing import Optional, Union

ClaimedHorizon = Union[datetime, date]

# Practical bit-width ceiling for this module (wider widths still return numeric
# bounds when datetime conversion is impossible).
_MAX_WIDTH_BITS = 128

_UNIT_SECONDS: dict[str, Fraction] = {
    "seconds": Fraction(1),
    "second": Fraction(1),
    "s": Fraction(1),
    "milliseconds": Fraction(1, 1000),
    "millisecond": Fraction(1, 1000),
    "ms": Fraction(1, 1000),
    "microseconds": Fraction(1, 1_000_000),
    "microsecond": Fraction(1, 1_000_000),
    "us": Fraction(1, 1_000_000),
    "µs": Fraction(1, 1_000_000),
    "nanoseconds": Fraction(1, 1_000_000_000),
    "nanosecond": Fraction(1, 1_000_000_000),
    "ns": Fraction(1, 1_000_000_000),
    "days": Fraction(86_400),
    "day": Fraction(86_400),
    "d": Fraction(86_400),
    "weeks": Fraction(7 * 86_400),
    "week": Fraction(7 * 86_400),
    "w": Fraction(7 * 86_400),
}


@dataclass
class TimeRepresentation:
    """Structured parameters for a fixed-width time counter (from LLM or tests)."""

    width_bits: Optional[int] = None
    signed: Optional[bool] = None
    epoch: Optional[datetime] = None
    unit: Optional[str] = None
    ticks_per_second: Optional[float] = None
    claimed_horizon: Optional[ClaimedHorizon] = None
    rollover_behavior: Optional[str] = None


@dataclass
class HorizonValidationResult:
    """Factual result of a deterministic horizon / range check."""

    validation_type: str
    status: str
    minimum_value: Optional[int] = None
    maximum_value: Optional[int] = None
    earliest_representable: Optional[datetime] = None
    last_representable: Optional[datetime] = None
    first_out_of_range: Optional[datetime] = None
    claimed_horizon: Optional[ClaimedHorizon] = None
    claim_consistent: Optional[bool] = None
    notes: list[str] = field(default_factory=list)


def integer_bounds(width_bits: int, *, signed: bool) -> tuple[int, int]:
    """Return (minimum, maximum) for an N-bit two's-complement or unsigned field."""
    if width_bits < 1:
        raise ValueError(f"width_bits must be >= 1, got {width_bits}")
    if width_bits > _MAX_WIDTH_BITS:
        raise ValueError(f"width_bits must be <= {_MAX_WIDTH_BITS}, got {width_bits}")
    if signed:
        return -(1 << (width_bits - 1)), (1 << (width_bits - 1)) - 1
    return 0, (1 << width_bits) - 1


def seconds_per_unit(
    unit: Optional[str],
    ticks_per_second: Optional[float] = None,
) -> tuple[Optional[Fraction], Optional[str]]:
    """
    Resolve unit to seconds per counter tick.

    Returns (fraction, error_note). error_note set when unsupported / incomplete.
    """
    if unit is None or not str(unit).strip():
        return None, "unit is missing"
    key = str(unit).strip().lower()
    if key in {"ticks", "tick", "fixed_rate", "fixed-rate"}:
        if ticks_per_second is None:
            return None, "ticks_per_second is required for fixed-rate ticks"
        try:
            rate = Fraction(Decimal(str(ticks_per_second)))
        except Exception:  # noqa: BLE001
            return None, f"invalid ticks_per_second: {ticks_per_second!r}"
        if rate <= 0:
            return None, "ticks_per_second must be positive"
        return Fraction(1) / rate, None
    if key in _UNIT_SECONDS:
        return _UNIT_SECONDS[key], None
    return None, f"unsupported unit: {unit!r}"


def ensure_utc(moment: datetime, *, notes: list[str]) -> datetime:
    """Normalize to timezone-aware UTC."""
    if moment.tzinfo is None:
        notes.append("Naive epoch interpreted as UTC.")
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def add_seconds(
    epoch: datetime,
    seconds: Fraction,
    *,
    notes: list[str],
    label: str,
) -> Optional[datetime]:
    """
    Add an exact Fraction of seconds to epoch using microsecond timedelta.

    Sub-microsecond remainders are truncated toward zero with a note.
    Returns None if the result is outside datetime's supported range.
    """
    micros_frac = seconds * 1_000_000
    whole_micros = int(micros_frac)  # trunc toward zero
    if Fraction(whole_micros) != micros_frac:
        notes.append(
            f"{label}: sub-microsecond remainder truncated when converting to datetime."
        )
    try:
        return epoch + timedelta(microseconds=whole_micros)
    except OverflowError:
        notes.append(
            f"{label}: computed instant is outside datetime range; "
            "numeric bounds are still reported."
        )
        return None


def claim_matches_horizon(
    claimed: ClaimedHorizon,
    *,
    last_representable: Optional[datetime],
    first_out_of_range: Optional[datetime],
) -> bool:
    """
    Compare a model-stated horizon to deterministic boundaries.

    A date claim matches if it equals the UTC calendar date of last_representable
    or first_out_of_range (models often cite the day, not the exact second).
    A datetime claim matches either boundary exactly (UTC-normalized).
    """
    if isinstance(claimed, datetime):
        claimed_dt = (
            claimed.replace(tzinfo=timezone.utc)
            if claimed.tzinfo is None
            else claimed.astimezone(timezone.utc)
        )
        for boundary in (last_representable, first_out_of_range):
            if boundary is not None and claimed_dt == boundary:
                return True
        return False

    # date
    for boundary in (last_representable, first_out_of_range):
        if boundary is not None and claimed == boundary.astimezone(timezone.utc).date():
            return True
    return False


def validate_time_representation(
    representation: TimeRepresentation,
) -> HorizonValidationResult:
    """
    Independently compute numeric bounds and optional epoch horizons.

    Does not decide whether a standard is defective. Status values:
    verified, contradicted, insufficient_parameters, not_applicable,
    unsupported, error.
    """
    notes: list[str] = []
    validation_type = "fixed_width_epoch_range"
    claimed = representation.claimed_horizon

    if representation.rollover_behavior:
        notes.append(
            f"Rollover behavior noted as {representation.rollover_behavior!r}; "
            "modulo wrap is reported as a boundary, not a failure."
        )

    if representation.width_bits is None or representation.signed is None:
        return HorizonValidationResult(
            validation_type=validation_type,
            status="insufficient_parameters",
            claimed_horizon=claimed,
            claim_consistent=None,
            notes=notes
            + [
                "width_bits and signed are required for numeric range validation."
            ],
        )

    try:
        minimum, maximum = integer_bounds(
            representation.width_bits, signed=representation.signed
        )
    except ValueError as exc:
        return HorizonValidationResult(
            validation_type=validation_type,
            status="error",
            claimed_horizon=claimed,
            notes=notes + [str(exc)],
        )

    result = HorizonValidationResult(
        validation_type=validation_type,
        status="insufficient_parameters",  # may upgrade once instants/claims resolved
        minimum_value=minimum,
        maximum_value=maximum,
        claimed_horizon=claimed,
        notes=notes,
    )

    # Instants require epoch + unit.
    if representation.epoch is None and representation.unit is None:
        result.notes.append(
            "Numeric range computed; epoch and unit omitted so instants were not derived."
        )
        if claimed is not None:
            result.notes.append(
                "claimed_horizon present but cannot be checked without epoch and unit."
            )
            result.status = "insufficient_parameters"
        else:
            result.status = "verified"  # numeric bounds alone are deterministic facts
            result.notes.append(
                "Status verified refers to integer min/max only (no epoch horizon)."
            )
        return result

    if representation.epoch is None:
        result.notes.append("epoch is missing; cannot compute representable instants.")
        result.status = "insufficient_parameters"
        return result

    seconds_unit, unit_error = seconds_per_unit(
        representation.unit, representation.ticks_per_second
    )
    if unit_error:
        # Distinguish unsupported unit vs missing params.
        if representation.unit and "unsupported unit" in unit_error:
            result.status = "unsupported"
        else:
            result.status = "insufficient_parameters"
        result.notes.append(unit_error)
        return result

    assert seconds_unit is not None
    epoch = ensure_utc(representation.epoch, notes=result.notes)

    earliest_secs = Fraction(minimum) * seconds_unit
    last_secs = Fraction(maximum) * seconds_unit
    # First integer value beyond the positive end of the range.
    first_oor_secs = Fraction(maximum + 1) * seconds_unit

    result.earliest_representable = add_seconds(
        epoch, earliest_secs, notes=result.notes, label="earliest_representable"
    )
    result.last_representable = add_seconds(
        epoch, last_secs, notes=result.notes, label="last_representable"
    )
    result.first_out_of_range = add_seconds(
        epoch, first_oor_secs, notes=result.notes, label="first_out_of_range"
    )

    if (
        result.earliest_representable is None
        and result.last_representable is None
        and result.first_out_of_range is None
    ):
        result.status = "verified"
        result.notes.append(
            "Integer bounds verified; all calendar conversions exceeded datetime range."
        )
        if claimed is not None:
            result.claim_consistent = None
            result.notes.append(
                "claimed_horizon could not be compared because instants were not representable."
            )
        return result

    if claimed is None:
        result.status = "verified"
        result.claim_consistent = None
        result.notes.append(
            "Deterministic bounds/instants computed; no model claimed_horizon to compare."
        )
        return result

    if result.last_representable is None and result.first_out_of_range is None:
        result.status = "insufficient_parameters"
        result.notes.append(
            "claimed_horizon could not be compared because horizon instants were unavailable."
        )
        return result

    consistent = claim_matches_horizon(
        claimed,
        last_representable=result.last_representable,
        first_out_of_range=result.first_out_of_range,
    )
    result.claim_consistent = consistent
    if consistent:
        result.status = "verified"
        result.notes.append(
            "Model-stated horizon agrees with last_representable and/or "
            "first_out_of_range (date or exact instant)."
        )
    else:
        result.status = "contradicted"
        result.notes.append(
            "Model-stated horizon does not match last_representable or "
            "first_out_of_range."
        )
    return result
