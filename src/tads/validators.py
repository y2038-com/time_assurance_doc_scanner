"""Deterministic validator scaffold (expanded in Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


# Known horizons used for documentation and simple checks.
Y2036_NTP_ERA = date(2036, 2, 7)
Y2038_SIGNED32 = date(2038, 1, 19)
Y2106_UNSIGNED32 = date(2106, 2, 7)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    detail: str


def unix_signed32_max_datetime() -> datetime:
    """Last representable second for 32-bit signed Unix time (UTC)."""
    return datetime(2038, 1, 19, 3, 14, 7, tzinfo=timezone.utc)


def assert_rollover_date(label: str, claimed: date) -> ValidationResult:
    """Compare a claimed rollover date to the canonical calendar date."""
    expected = {
        "y2036": Y2036_NTP_ERA,
        "y2038": Y2038_SIGNED32,
        "y2106": Y2106_UNSIGNED32,
    }.get(label.lower())
    if expected is None:
        return ValidationResult(ok=False, detail=f"Unknown rollover label '{label}'.")
    if claimed == expected:
        return ValidationResult(ok=True, detail=f"{label} matches {expected.isoformat()}.")
    return ValidationResult(
        ok=False,
        detail=f"{label} claimed {claimed.isoformat()}, expected {expected.isoformat()}.",
    )


def parse_iso_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
