# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Validation and bounded I/O helpers for ingest resource limits."""

from __future__ import annotations

import math
from pathlib import Path
from typing import BinaryIO

from tads.ingest.fetch import IngestError
from tads.ingest.types import IngestOptions

_READ_CHUNK = 64 * 1024


def validate_ingest_options(options: IngestOptions) -> IngestOptions:
    """Reject zero, negative, or non-finite ingest resource limits."""
    options.max_download_bytes = require_positive_int(
        "max_download_bytes", options.max_download_bytes
    )
    options.max_archive_member_bytes = require_positive_int(
        "max_archive_member_bytes", options.max_archive_member_bytes
    )
    options.max_archive_expansion_ratio = require_positive_float(
        "max_archive_expansion_ratio", options.max_archive_expansion_ratio
    )
    options.max_converted_chars = require_positive_int(
        "max_converted_chars", options.max_converted_chars
    )
    options.max_container_members = require_positive_int(
        "max_container_members", options.max_container_members
    )
    options.max_container_uncompressed_bytes = require_positive_int(
        "max_container_uncompressed_bytes",
        options.max_container_uncompressed_bytes,
    )
    return options


def require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise IngestError(f"{name} must be a positive integer.")
    if isinstance(value, float):
        if not math.isfinite(value) or value <= 0:
            raise IngestError(f"{name} must be a positive integer.")
        if not value.is_integer():
            raise IngestError(f"{name} must be a positive integer.")
        return int(value)
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise IngestError(f"{name} must be a positive integer.") from exc
    if number <= 0:
        raise IngestError(f"{name} must be a positive integer.")
    return number


def require_positive_float(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise IngestError(f"{name} must be a positive finite number.")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise IngestError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(number) or number <= 0:
        raise IngestError(f"{name} must be a positive finite number.")
    return number


def mib_to_bytes(name: str, value: object) -> int:
    """Convert a positive finite MiB quantity to a positive byte count."""
    mib = require_positive_float(name, value)
    try:
        raw = mib * 1024 * 1024
    except OverflowError as exc:
        raise IngestError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(raw) or raw <= 0:
        raise IngestError(f"{name} must be a positive finite number.")
    as_int = int(raw)
    if as_int <= 0:
        raise IngestError(f"{name} must be a positive finite number.")
    return as_int


def assert_converted_text_limit(text: str, max_chars: int) -> None:
    count = len(text)
    if count > max_chars:
        raise IngestError(
            f"Converted text exceeds max_converted_chars ({max_chars}); "
            f"got {count} characters."
        )


def note_converted_chars(piece: str, *, used: int, max_chars: int) -> int:
    """Account for ``piece`` and fail if the converted-text budget is exceeded."""
    next_used = used + len(piece)
    if next_used > max_chars:
        raise IngestError(
            f"Converted text exceeds max_converted_chars ({max_chars}); "
            f"got at least {next_used} characters."
        )
    return next_used


def read_limited(
    handle: BinaryIO,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read a stream, stopping at ``max_bytes + 1`` so oversize input cannot grow unbounded."""
    chunks: list[bytes] = []
    total = 0
    while True:
        budget = max_bytes - total + 1
        if budget <= 0:
            raise IngestError(
                f"{label} exceeded the maximum size ({max_bytes} bytes) while reading."
            )
        chunk = handle.read(min(_READ_CHUNK, budget))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise IngestError(
                f"{label} exceeded the maximum size ({max_bytes} bytes) while reading."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def drain_limited(
    handle: BinaryIO,
    *,
    max_part_bytes: int,
    max_remaining_cumulative: int,
    label: str,
) -> int:
    """Decompress a part while counting bytes, without retaining the payload."""
    actual = 0
    while True:
        part_budget = max_part_bytes - actual + 1
        cumul_budget = max_remaining_cumulative - actual + 1
        budget = min(part_budget, cumul_budget)
        if budget <= 0:
            _raise_drain_limit(
                actual=actual + 1,
                max_part_bytes=max_part_bytes,
                max_remaining_cumulative=max_remaining_cumulative,
                label=label,
            )
        chunk = handle.read(min(_READ_CHUNK, budget))
        if not chunk:
            break
        actual += len(chunk)
        if actual > max_part_bytes:
            raise IngestError(
                f"{label} exceeded the maximum uncompressed size "
                f"({max_part_bytes} bytes) while reading."
            )
        if actual > max_remaining_cumulative:
            raise IngestError(
                f"{label} exceeded the maximum cumulative uncompressed size "
                f"({max_remaining_cumulative} bytes remaining) while reading."
            )
    return actual


def _raise_drain_limit(
    *,
    actual: int,
    max_part_bytes: int,
    max_remaining_cumulative: int,
    label: str,
) -> None:
    if actual > max_part_bytes:
        raise IngestError(
            f"{label} exceeded the maximum uncompressed size "
            f"({max_part_bytes} bytes) while reading."
        )
    raise IngestError(
        f"{label} exceeded the maximum cumulative uncompressed size "
        f"({max_remaining_cumulative} bytes remaining) while reading."
    )


def read_local_file_limited(path: Path, *, max_bytes: int) -> bytes:
    """Reject an oversize local file using stat, then a bounded incremental read."""
    try:
        declared = path.stat().st_size
    except OSError as exc:
        raise IngestError(f"Input not found or not a file: {path}") from exc
    if declared > max_bytes:
        raise IngestError(
            f"Local file exceeds max size ({max_bytes} bytes): {path}"
        )
    with path.open("rb") as handle:
        return read_limited(
            handle,
            max_bytes=max_bytes,
            label=f"Local file {path}",
        )
