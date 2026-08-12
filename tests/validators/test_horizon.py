# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pure fixed-width / epoch horizon calculator."""

from __future__ import annotations

from datetime import date, datetime, timezone

from tads.validators import (
    TimeRepresentation,
    integer_bounds,
    validate_time_representation,
)

UTC = timezone.utc
EPOCH_1970 = datetime(1970, 1, 1, tzinfo=UTC)
EPOCH_1900 = datetime(1900, 1, 1, tzinfo=UTC)


def test_integer_bounds_signed_and_unsigned():
    assert integer_bounds(32, signed=True) == (-(2**31), 2**31 - 1)
    assert integer_bounds(32, signed=False) == (0, 2**32 - 1)
    assert integer_bounds(16, signed=True) == (-32768, 32767)
    assert integer_bounds(16, signed=False) == (0, 65535)


def test_signed_32_bit_unix_time_from_1970():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.minimum_value == -(2**31)
    assert result.maximum_value == 2**31 - 1
    assert result.last_representable == datetime(2038, 1, 19, 3, 14, 7, tzinfo=UTC)
    assert result.first_out_of_range == datetime(2038, 1, 19, 3, 14, 8, tzinfo=UTC)
    assert result.earliest_representable == datetime(1901, 12, 13, 20, 45, 52, tzinfo=UTC)


def test_unsigned_32_bit_seconds_from_1900():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1900,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.minimum_value == 0
    assert result.maximum_value == 4_294_967_295
    assert result.earliest_representable == EPOCH_1900
    assert result.last_representable == datetime(2036, 2, 7, 6, 28, 15, tzinfo=UTC)
    assert result.first_out_of_range == datetime(2036, 2, 7, 6, 28, 16, tzinfo=UTC)


def test_unsigned_32_bit_seconds_from_1970():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.maximum_value == 4_294_967_295
    assert result.last_representable == datetime(2106, 2, 7, 6, 28, 15, tzinfo=UTC)
    assert result.first_out_of_range == datetime(2106, 2, 7, 6, 28, 16, tzinfo=UTC)


def test_unsigned_16_bit_day_counter_from_1970():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=16,
            signed=False,
            epoch=EPOCH_1970,
            unit="days",
        )
    )
    assert result.status == "verified"
    assert result.minimum_value == 0
    assert result.maximum_value == 65_535
    assert result.last_representable == datetime(2149, 6, 6, 0, 0, 0, tzinfo=UTC)
    assert result.first_out_of_range == datetime(2149, 6, 7, 0, 0, 0, tzinfo=UTC)


def test_signed_16_bit_seconds():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=16,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "verified"
    assert result.minimum_value == -32_768
    assert result.maximum_value == 32_767
    assert result.last_representable == datetime(1970, 1, 1, 9, 6, 7, tzinfo=UTC)
    assert result.first_out_of_range == datetime(1970, 1, 1, 9, 6, 8, tzinfo=UTC)
    assert result.earliest_representable == datetime(1969, 12, 31, 14, 53, 52, tzinfo=UTC)


def test_millisecond_representation_unsigned_32():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1970,
            unit="milliseconds",
        )
    )
    assert result.status == "verified"
    assert result.maximum_value == 4_294_967_295
    # 4294967295 ms = 4294967.295 s → 1970-02-19 17:02:47.295 UTC
    assert result.last_representable == datetime(
        1970, 2, 19, 17, 2, 47, 295_000, tzinfo=UTC
    )
    assert result.first_out_of_range == datetime(
        1970, 2, 19, 17, 2, 47, 296_000, tzinfo=UTC
    )


def test_fixed_rate_tick_counter():
    # 16-bit unsigned counter at 1000 ticks/second ≡ milliseconds, short span.
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=16,
            signed=False,
            epoch=EPOCH_1970,
            unit="ticks",
            ticks_per_second=1000.0,
        )
    )
    assert result.status == "verified"
    assert result.maximum_value == 65_535
    assert result.last_representable == datetime(
        1970, 1, 1, 0, 1, 5, 535_000, tzinfo=UTC
    )
    assert result.first_out_of_range == datetime(
        1970, 1, 1, 0, 1, 5, 536_000, tzinfo=UTC
    )


def test_missing_epoch():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch=None,
            unit="seconds",
        )
    )
    assert result.status == "insufficient_parameters"
    assert result.minimum_value == -(2**31)
    assert result.maximum_value == 2**31 - 1
    assert result.last_representable is None
    assert any("epoch" in n.lower() for n in result.notes)


def test_missing_width():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=None,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
        )
    )
    assert result.status == "insufficient_parameters"
    assert result.minimum_value is None
    assert result.maximum_value is None


def test_unsupported_unit():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1970,
            unit="fortnights",
        )
    )
    assert result.status == "unsupported"
    assert result.minimum_value == 0
    assert result.maximum_value == 4_294_967_295
    assert result.last_representable is None
    assert any("unsupported unit" in n for n in result.notes)


def test_model_claim_agrees_with_deterministic_result():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=True,
            epoch=EPOCH_1970,
            unit="seconds",
            claimed_horizon=date(2038, 1, 19),
        )
    )
    assert result.status == "verified"
    assert result.claim_consistent is True
    assert result.last_representable == datetime(2038, 1, 19, 3, 14, 7, tzinfo=UTC)


def test_model_claim_disagrees_with_deterministic_result():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1900,
            unit="seconds",
            claimed_horizon=date(2038, 1, 19),
        )
    )
    assert result.status == "contradicted"
    assert result.claim_consistent is False
    assert result.last_representable == datetime(2036, 2, 7, 6, 28, 15, tzinfo=UTC)


def test_exact_datetime_claim_for_ntp_boundary():
    result = validate_time_representation(
        TimeRepresentation(
            width_bits=32,
            signed=False,
            epoch=EPOCH_1900,
            unit="seconds",
            claimed_horizon=datetime(2036, 2, 7, 6, 28, 16, tzinfo=UTC),
        )
    )
    assert result.status == "verified"
    assert result.claim_consistent is True
