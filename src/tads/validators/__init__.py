# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic validators (model-independent).

Known-horizon helpers remain available for scaffolds and tests. The horizon
calculator in ``tads.validators.horizon`` is the general fixed-width engine.
"""

from tads.validators.horizon import (
    HorizonValidationResult,
    TimeRepresentation,
    claim_matches_horizon,
    integer_bounds,
    seconds_per_unit,
    validate_time_representation,
)
from tads.validators.known import (
    Y2036_NTP_ERA,
    Y2038_SIGNED32,
    Y2106_UNSIGNED32,
    ValidationResult,
    assert_rollover_date,
    parse_iso_date,
    unix_signed32_max_datetime,
)

__all__ = [
    "HorizonValidationResult",
    "TimeRepresentation",
    "ValidationResult",
    "Y2036_NTP_ERA",
    "Y2038_SIGNED32",
    "Y2106_UNSIGNED32",
    "assert_rollover_date",
    "claim_matches_horizon",
    "integer_bounds",
    "parse_iso_date",
    "seconds_per_unit",
    "unix_signed32_max_datetime",
    "validate_time_representation",
]
