# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Planner completion-budget and shared request-overhead boundary tests."""

from __future__ import annotations

from tads.cost import (
    PRICING_REQUEST_INPUT_OVERHEAD_TOKENS,
    estimated_request_input_tokens,
)
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.sections import (
    REQUEST_OVERHEAD_TOKENS,
    choose_analysis_mode,
    estimate_tokens,
)
from tads.schemas.report import AnalysisMode


def _doc(text: str) -> ParsedDocument:
    return ParsedDocument(
        corpus="ietf",
        doc_id="RFC9999",
        title="Boundary",
        text=text,
        sections=[
            Section(
                id="s1",
                title="1. Body",
                text=text,
                level=1,
                start_char=0,
                end_char=len(text),
            )
        ],
    )


def test_shared_request_overhead_constant():
    assert REQUEST_OVERHEAD_TOKENS == 6_000
    assert PRICING_REQUEST_INPUT_OVERHEAD_TOKENS == REQUEST_OVERHEAD_TOKENS
    assert estimated_request_input_tokens(100) == 100 + REQUEST_OVERHEAD_TOKENS


def test_exact_boundary_whole_document_at_equality():
    window = 100_000
    max_out = 16_384
    overhead = REQUEST_OVERHEAD_TOKENS
    available = window - max_out - overhead
    text = "x" * (available * 4)
    assert estimate_tokens(text) == available
    doc = _doc(text)
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=max_out,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.WHOLE_DOCUMENT
    )


def test_one_token_over_boundary_is_section_aware():
    window = 100_000
    max_out = 16_384
    overhead = REQUEST_OVERHEAD_TOKENS
    available = window - max_out - overhead
    text = "x" * (available * 4 + 4)
    assert estimate_tokens(text) == available + 1
    doc = _doc(text)
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=max_out,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.SECTION_AWARE
    )


def test_pricing_and_planning_use_same_overhead():
    """available = context - max_output - request_overhead; pricing adds same pad."""
    window = 80_000
    max_out = 16_384
    overhead = REQUEST_OVERHEAD_TOKENS
    assert overhead == PRICING_REQUEST_INPUT_OVERHEAD_TOKENS
    available = window - max_out - overhead
    text = "x" * (available * 4)
    doc = _doc(text)
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=max_out,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.WHOLE_DOCUMENT
    )
    body = estimate_tokens(text)
    assert estimated_request_input_tokens(body) == body + overhead
    # One token over planning boundary is section-aware; pricing pad still matches.
    over = "x" * ((available + 1) * 4)
    assert (
        choose_analysis_mode(
            _doc(over),
            context_token_budget=window,
            output_reserve_tokens=max_out,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.SECTION_AWARE
    )
    assert estimated_request_input_tokens(estimate_tokens(over)) == (
        estimate_tokens(over) + overhead
    )


def test_configured_completion_budget_remains_fully_reserved():
    window = 50_000
    overhead = REQUEST_OVERHEAD_TOKENS
    # Fits with 4k completion reserve, not with 40k — overhead held constant.
    available_small = window - 4_000 - overhead
    text = "x" * (available_small * 4)
    doc = _doc(text)
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=4_000,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.WHOLE_DOCUMENT
    )
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=40_000,
            request_overhead_tokens=overhead,
        )
        == AnalysisMode.SECTION_AWARE
    )
    # Default path (no explicit overhead kwarg) uses REQUEST_OVERHEAD_TOKENS.
    assert (
        choose_analysis_mode(
            doc,
            context_token_budget=window,
            output_reserve_tokens=4_000,
        )
        == AnalysisMode.WHOLE_DOCUMENT
    )
