# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Bounded JSON decoding for untrusted input.

Numeric tokens are checked before conversion. This does not cap HTTP response
bytes or overall JSON document size. Do not call ``sys.set_int_max_str_digits``.
"""

from __future__ import annotations

import json
import math
from typing import Any

# 40 decimal digits covers signed 128-bit representation bounds (39 digits)
# and ordinary counters. The CPython 4,300-digit process limit is not used.
MAX_JSON_INT_DIGITS = 40

# IEEE doubles need ~24 characters in ordinary scientific form. 64 characters
# admits verbose finite literals without allowing megabyte mantissas.
MAX_JSON_FLOAT_CHARS = 64


class JsonNumberError(ValueError):
    """Internal numeric-policy failure. Public code maps this to a safe error."""


class JsonLoadError(ValueError):
    """Controlled failure loading untrusted JSON from a file or similar source."""


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a key and rejection is requested."""


def loads(text: str, *, reject_duplicate_keys: bool = False) -> Any:
    """Decode JSON with bounded numbers and optional duplicate-key rejection.

    Does not mutate ``json`` module defaults or interpreter integer limits.
    Integers that exceed the digit cap never become Python ``int`` values, so
    they cannot later reach ``json.dumps`` or report serialization.
    """
    if not isinstance(text, str):
        raise JsonNumberError()
    hooks: dict[str, Any] = {
        "parse_int": _parse_int,
        "parse_float": _parse_float,
        "parse_constant": _parse_constant,
    }
    if reject_duplicate_keys:
        hooks["object_pairs_hook"] = _reject_duplicate_keys
    try:
        return json.loads(text, **hooks)
    except (JsonNumberError, DuplicateJsonKeyError, json.JSONDecodeError):
        raise
    except ValueError:
        raise JsonNumberError() from None


def _parse_int(token: str) -> int:
    if not isinstance(token, str) or not token:
        raise JsonNumberError()
    digits = token[1:] if token.startswith("-") else token
    if not digits.isdigit():
        raise JsonNumberError()
    if len(digits) > MAX_JSON_INT_DIGITS:
        raise JsonNumberError()
    return int(token)


def _parse_float(token: str) -> float:
    if not isinstance(token, str) or not token:
        raise JsonNumberError()
    if len(token) > MAX_JSON_FLOAT_CHARS:
        raise JsonNumberError()
    try:
        value = float(token)
    except ValueError:
        raise JsonNumberError() from None
    if not math.isfinite(value):
        raise JsonNumberError()
    return value


def _parse_constant(_name: str) -> Any:
    raise JsonNumberError()


def _reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateJsonKeyError()
        out[key] = value
    return out
