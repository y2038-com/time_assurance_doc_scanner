"""Shared heuristic helpers for Phase 0 provider stubs."""

from tads.parsing.sections import estimate_tokens


def heuristic_token_count(text: str) -> int:
    return estimate_tokens(text)
