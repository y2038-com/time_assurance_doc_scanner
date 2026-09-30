# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Serializable models for deterministic horizon validation."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MIN_YEAR = 1
_MAX_YEAR = 9999
_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_ALLOWED_CLAIM_KEYS = frozenset({"value", "precision"})


class HorizonPrecision(StrEnum):
    """Lexical precision of a source-stated horizon claim."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    INSTANT = "instant"


class ClaimedHorizon(BaseModel):
    """
    Horizon explicitly stated by the source document.

    This is not a model-computed rollover and not a TADS-computed bound.
    ``value`` is a lexical form that must match ``precision``. Year and month
    claims are not exact instants.
    """

    model_config = ConfigDict(extra="forbid")

    value: str
    precision: HorizonPrecision

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("claimed_horizon value must be a string")
        text = value.strip()
        if not text:
            raise ValueError("claimed_horizon value must be nonempty")
        return text

    @model_validator(mode="after")
    def _lexical_agrees_with_precision(self) -> ClaimedHorizon:
        if not claimed_horizon_lexical_is_valid(self.value, self.precision):
            raise ValueError("claimed_horizon value does not match precision")
        return self


def claimed_horizon_lexical_is_valid(value: str, precision: HorizonPrecision) -> bool:
    """True when ``value`` is exactly the lexical form required by ``precision``."""
    try:
        _parse_claimed_horizon_lexical(value, precision)
    except ValueError:
        return False
    return True


def _parse_claimed_horizon_lexical(
    value: str, precision: HorizonPrecision
) -> Union[int, tuple[int, int], date, datetime]:
    if precision == HorizonPrecision.YEAR:
        match = _YEAR_RE.fullmatch(value)
        if match is None:
            raise ValueError("year value must be exactly YYYY")
        year = int(match.group(1))
        if year < _MIN_YEAR or year > _MAX_YEAR:
            raise ValueError("year out of range")
        return year
    if precision == HorizonPrecision.MONTH:
        match = _MONTH_RE.fullmatch(value)
        if match is None:
            raise ValueError("month value must be exactly YYYY-MM")
        year = int(match.group(1))
        if year < _MIN_YEAR or year > _MAX_YEAR:
            raise ValueError("year out of range")
        return year, int(match.group(2))
    if precision == HorizonPrecision.DAY:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("day value must be YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid calendar date") from exc
        if parsed.year < _MIN_YEAR or parsed.year > _MAX_YEAR:
            raise ValueError("year out of range")
        return parsed
    if precision == HorizonPrecision.INSTANT:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("invalid datetime") from exc
        if parsed.tzinfo is None:
            raise ValueError("instant requires a timezone offset")
        if parsed.year < _MIN_YEAR or parsed.year > _MAX_YEAR:
            raise ValueError("year out of range")
        return parsed
    raise ValueError("unknown precision")


def exact_comparable_moment(
    claimed: Optional[ClaimedHorizon],
) -> Union[date, datetime, None]:
    """Return a date/datetime only for day/instant precision; never invent one."""
    if claimed is None:
        return None
    if claimed.precision == HorizonPrecision.DAY:
        parsed = _parse_claimed_horizon_lexical(claimed.value, claimed.precision)
        assert isinstance(parsed, date)
        return parsed
    if claimed.precision == HorizonPrecision.INSTANT:
        parsed = _parse_claimed_horizon_lexical(claimed.value, claimed.precision)
        assert isinstance(parsed, datetime)
        return parsed
    return None


def is_partial_horizon_precision(claimed: Optional[ClaimedHorizon]) -> bool:
    return claimed is not None and claimed.precision in {
        HorizonPrecision.YEAR,
        HorizonPrecision.MONTH,
    }


def coerce_legacy_claimed_horizon(value: Any) -> Optional[ClaimedHorizon]:
    """Load schema 0.1.0/0.2.0 scalar date/datetime claims into the 0.3.0 object.

    New model-output parsing must not use this helper.
    """
    if value is None:
        return None
    if isinstance(value, ClaimedHorizon):
        return value
    if isinstance(value, dict):
        extra = [key for key in value if key not in _ALLOWED_CLAIM_KEYS]
        if extra:
            raise ValueError("extra claimed_horizon key")
        return ClaimedHorizon.model_validate(value)
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return ClaimedHorizon(
            value=_canonical_instant_text(moment),
            precision=HorizonPrecision.INSTANT,
        )
    if isinstance(value, date):
        return ClaimedHorizon(value=value.isoformat(), precision=HorizonPrecision.DAY)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty claimed_horizon")
        return _claimed_horizon_from_legacy_iso(text)
    raise ValueError("invalid claimed_horizon")


def _claimed_horizon_from_legacy_iso(text: str) -> ClaimedHorizon:
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        tail = text[10:]
        if tail == "" or tail.upper() in {"Z"}:
            parsed_date = date.fromisoformat(text[:10])
            return ClaimedHorizon(
                value=parsed_date.isoformat(), precision=HorizonPrecision.DAY
            )
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        try:
            parsed_date = date.fromisoformat(text[:10])
        except ValueError:
            raise ValueError("invalid claimed_horizon") from exc
        return ClaimedHorizon(
            value=parsed_date.isoformat(), precision=HorizonPrecision.DAY
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return ClaimedHorizon(
        value=_canonical_instant_text(dt),
        precision=HorizonPrecision.INSTANT,
    )


def _canonical_instant_text(moment: datetime) -> str:
    aware = (
        moment.replace(tzinfo=timezone.utc)
        if moment.tzinfo is None
        else moment.astimezone(timezone.utc)
    )
    text = aware.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


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

    ``claimed_horizon`` is a source-stated horizon (object with value/precision,
    or null). It is not derived from width, epoch, signedness, or unit.
    """

    width_bits: Optional[int] = None
    signed: Optional[bool] = None
    epoch_kind: Optional[EpochKind] = None
    epoch: Optional[datetime] = None
    unit: Optional[str] = None
    ticks_per_second: Optional[float] = None
    claimed_horizon: Optional[ClaimedHorizon] = Field(
        default=None,
        description=(
            "Horizon explicitly stated by the source document. Not a "
            "model-computed or TADS-computed rollover. Null when unstated."
        ),
    )
    rollover_behavior: Optional[str] = None

    @field_validator("claimed_horizon", mode="before")
    @classmethod
    def _coerce_claimed_horizon(cls, value: Any) -> Any:
        # Python date/datetime constructors only. JSON scalars stay uncoerced so
        # schema 0.3.0 loads reject the old string/number form.
        if value is None or isinstance(value, (ClaimedHorizon, dict)):
            return value
        if isinstance(value, datetime):
            return coerce_legacy_claimed_horizon(value)
        if isinstance(value, date):
            return coerce_legacy_claimed_horizon(value)
        return value


class HorizonValidation(BaseModel):
    """
    Structured deterministic horizon / range check result.

    Separate from semantic candidate status: verifying arithmetic does not
    confirm that a candidate is a standards defect.

    When signedness is unresolved, ``status`` is ``ambiguous_signedness`` and
    both ``signed_interpretation`` and ``unsigned_interpretation`` may be set.
    Nested interpretations are one level deep only.

    ``claim_consistent`` is true/false only for day/instant source claims.
    Year/month claims leave it null (not exactly comparable).
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

    @field_validator("claimed_horizon", mode="before")
    @classmethod
    def _coerce_claimed_horizon(cls, value: Any) -> Any:
        if value is None or isinstance(value, (ClaimedHorizon, dict)):
            return value
        if isinstance(value, datetime):
            return coerce_legacy_claimed_horizon(value)
        if isinstance(value, date):
            return coerce_legacy_claimed_horizon(value)
        return value
