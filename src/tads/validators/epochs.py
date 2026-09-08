# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Well-known epoch resolution for deterministic horizon arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tads.schemas.horizon import EpochKind

# Nominal civil UTC midnights used as calendar anchors for arithmetic.
# These are not leap-second / TAI-scale conversions.
WELL_KNOWN_EPOCHS: dict[EpochKind, datetime] = {
    EpochKind.UNIX: datetime(1970, 1, 1, tzinfo=timezone.utc),
    EpochKind.NTP: datetime(1900, 1, 1, tzinfo=timezone.utc),
    EpochKind.GPS: datetime(1980, 1, 6, tzinfo=timezone.utc),
    EpochKind.MJD: datetime(1858, 11, 17, tzinfo=timezone.utc),
    EpochKind.NTFS: datetime(1601, 1, 1, tzinfo=timezone.utc),
    EpochKind.UUID: datetime(1582, 10, 15, tzinfo=timezone.utc),
    EpochKind.TAI_1958: datetime(1958, 1, 1, tzinfo=timezone.utc),
}

_EPOCH_KIND_ALIASES: dict[str, EpochKind] = {
    "unix": EpochKind.UNIX,
    "posix": EpochKind.UNIX,
    "unix_epoch": EpochKind.UNIX,
    "ntp": EpochKind.NTP,
    "ntp_epoch": EpochKind.NTP,
    "gps": EpochKind.GPS,
    "gps_epoch": EpochKind.GPS,
    "mjd": EpochKind.MJD,
    "modified_julian": EpochKind.MJD,
    "modified_julian_date": EpochKind.MJD,
    "modified_julian_day": EpochKind.MJD,
    "ntfs": EpochKind.NTFS,
    "windows": EpochKind.NTFS,
    "uuid": EpochKind.UUID,
    "gregorian_uuid": EpochKind.UUID,
    "tai_1958": EpochKind.TAI_1958,
    "tai1958": EpochKind.TAI_1958,
    "other": EpochKind.OTHER,
}


def parse_epoch_kind(value: object) -> Optional[EpochKind]:
    """Parse a document/LLM epoch_kind token; return None if unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, EpochKind):
        return value
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return _EPOCH_KIND_ALIASES.get(text)


def resolve_epoch_datetime(
    *,
    epoch_kind: Optional[EpochKind],
    epoch: Optional[datetime],
) -> tuple[Optional[datetime], list[str], Optional[str]]:
    """
    Resolve an epoch for horizon arithmetic.

    Returns ``(datetime_or_none, notes, error_message)``.
    ``error_message`` is set when resolution fails hard (e.g. ``other`` without
    an explicit datetime). Named epochs use the well-known civil UTC midnight;
    they do not model TAI/GPS leap-second timescales.
    """
    notes: list[str] = []
    if epoch_kind is None:
        return epoch, notes, None

    if epoch_kind == EpochKind.OTHER:
        if epoch is None:
            return (
                None,
                notes,
                "epoch_kind is 'other' but epoch datetime is missing.",
            )
        notes.append(
            "epoch_kind=other; using explicit epoch datetime "
            "(civil/calendar arithmetic only)."
        )
        return epoch, notes, None

    known = WELL_KNOWN_EPOCHS.get(epoch_kind)
    if known is None:
        return None, notes, f"unsupported epoch_kind: {epoch_kind!r}"

    notes.append(
        f"Resolved epoch_kind={epoch_kind.value} to {known.isoformat()} "
        "(nominal civil UTC midnight; not a leap-second timescale conversion)."
    )
    if epoch is not None:
        epoch_cmp = epoch
        if epoch_cmp.tzinfo is None:
            epoch_cmp = epoch_cmp.replace(tzinfo=timezone.utc)
        else:
            epoch_cmp = epoch_cmp.astimezone(timezone.utc)
        if epoch_cmp != known:
            notes.append(
                "Explicit epoch datetime differs from the well-known value for "
                f"epoch_kind={epoch_kind.value}; using the well-known value."
            )
    return known, notes, None
