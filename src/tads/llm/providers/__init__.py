# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared heuristic helpers for Phase 0 provider stubs."""

from tads.parsing.sections import estimate_tokens


def heuristic_token_count(text: str) -> int:
    return estimate_tokens(text)
