# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Cost estimation tests."""

from tads.cost import (
    PRICING_REQUEST_INPUT_OVERHEAD_TOKENS,
    BudgetExceededError,
    assert_within_budget,
    estimate_cost_usd_for_requests,
    estimate_request_cost_usd,
    estimate_scan_cost,
    estimated_request_input_tokens,
)
from tads.llm import get_provider
from tads.llm.openai_models import GPT_6_LUNA, LUNA_LONG_CONTEXT_INPUT_THRESHOLD
from tads.schemas.cost import CostBudget, CostEstimate


def test_estimate_known_model():
    provider = get_provider("openai")
    estimate = estimate_scan_cost(
        provider,
        model="gpt-4.1-mini",
        document_text="hello world " * 100,
        analysis_units=1,
        budget=CostBudget(max_cost_usd=1.0, max_tokens=100_000),
    )
    assert estimate.estimated_cost_usd is not None
    assert estimate.within_budget is True


def test_budget_exceeded():
    estimate = CostEstimate(
        provider="openai",
        model="gpt-4.1-mini",
        estimated_input_tokens=1000,
        estimated_output_tokens=1000,
        estimated_cost_usd=5.0,
        within_budget=False,
        notes=["over budget"],
    )
    try:
        assert_within_budget(estimate)
        assert False, "expected BudgetExceededError"
    except BudgetExceededError:
        pass


def test_luna_pricing_below_at_and_above_272k():
    out = 1000
    below_in = LUNA_LONG_CONTEXT_INPUT_THRESHOLD - 1
    at_in = LUNA_LONG_CONTEXT_INPUT_THRESHOLD
    above_in = LUNA_LONG_CONTEXT_INPUT_THRESHOLD + 1

    below, below_notes = estimate_request_cost_usd(
        provider="openai", model=GPT_6_LUNA, input_tokens=below_in, output_tokens=out
    )
    at, at_notes = estimate_request_cost_usd(
        provider="openai", model=GPT_6_LUNA, input_tokens=at_in, output_tokens=out
    )
    above, above_notes = estimate_request_cost_usd(
        provider="openai", model=GPT_6_LUNA, input_tokens=above_in, output_tokens=out
    )

    expected_short_below = round(
        (below_in / 1_000_000) * 0.10 + (out / 1_000_000) * 0.50, 6
    )
    expected_short_at = round(
        (at_in / 1_000_000) * 0.10 + (out / 1_000_000) * 0.50, 6
    )
    expected_long = round(
        (above_in / 1_000_000) * 0.20 + (out / 1_000_000) * 0.75, 6
    )
    assert below == expected_short_below
    assert at == expected_short_at
    assert above == expected_long
    assert any("short-context" in n for n in below_notes)
    assert any("short-context" in n for n in at_notes)
    assert any("long-context" in n for n in above_notes)
    assert any("lower bound" in n for n in below_notes)


def test_luna_section_aware_prices_requests_independently():
    # Document-sized total would be long-context; each section request is short.
    short = 10_000
    long_total = LUNA_LONG_CONTEXT_INPUT_THRESHOLD + 50_000
    section_requests = [(short, 1500), (short, 1500), (short, 1500)]
    assert sum(i for i, _ in section_requests) < long_total

    section_usd, section_notes = estimate_cost_usd_for_requests(
        provider="openai", model=GPT_6_LUNA, requests=section_requests
    )
    whole_usd, whole_notes = estimate_request_cost_usd(
        provider="openai",
        model=GPT_6_LUNA,
        input_tokens=long_total,
        output_tokens=4500,
    )
    expected_section = round(
        3 * ((short / 1_000_000) * 0.10 + (1500 / 1_000_000) * 0.50), 6
    )
    assert section_usd == expected_section
    assert any("short-context" in n for n in section_notes)
    assert any("per-request" in n or "multi-call" in n for n in section_notes)
    assert whole_usd is not None and whole_usd > section_usd
    assert any("long-context" in n for n in whole_notes)


def test_gpt41_mini_pricing_unchanged():
    cost, notes = estimate_request_cost_usd(
        provider="openai",
        model="gpt-4.1-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == round(0.40 + 1.60, 6)
    assert any("indicative pricing table" in n for n in notes)


def test_pricing_and_planning_share_request_overhead():
    from tads.parsing.sections import (
        DEFAULT_PROMPT_OVERHEAD_TOKENS,
        REQUEST_OVERHEAD_TOKENS,
    )

    assert PRICING_REQUEST_INPUT_OVERHEAD_TOKENS == REQUEST_OVERHEAD_TOKENS
    assert DEFAULT_PROMPT_OVERHEAD_TOKENS == REQUEST_OVERHEAD_TOKENS
    assert REQUEST_OVERHEAD_TOKENS == 6_000


def test_luna_tier_boundary_after_pricing_overhead():
    """Tier uses body+overhead; bodies just under 272k can still be long-tier."""
    out = 1000
    overhead = PRICING_REQUEST_INPUT_OVERHEAD_TOKENS
    threshold = LUNA_LONG_CONTEXT_INPUT_THRESHOLD

    # Body such that body + overhead == threshold → still short (≤).
    body_at = threshold - overhead
    req_at = estimated_request_input_tokens(body_at)
    assert req_at == threshold
    at_cost, at_notes = estimate_request_cost_usd(
        provider="openai", model=GPT_6_LUNA, input_tokens=req_at, output_tokens=out
    )
    assert any("short-context" in n for n in at_notes)
    assert at_cost == round(
        (threshold / 1_000_000) * 0.10 + (out / 1_000_000) * 0.50, 6
    )

    # One body token more → request input threshold+1 → long.
    body_above = body_at + 1
    req_above = estimated_request_input_tokens(body_above)
    assert req_above == threshold + 1
    above_cost, above_notes = estimate_request_cost_usd(
        provider="openai",
        model=GPT_6_LUNA,
        input_tokens=req_above,
        output_tokens=out,
    )
    assert any("long-context" in n for n in above_notes)
    assert above_cost == round(
        ((threshold + 1) / 1_000_000) * 0.20 + (out / 1_000_000) * 0.75, 6
    )

    # Immediately below threshold after overhead.
    body_below = body_at - 1
    req_below = estimated_request_input_tokens(body_below)
    assert req_below == threshold - 1
    _, below_notes = estimate_request_cost_usd(
        provider="openai",
        model=GPT_6_LUNA,
        input_tokens=req_below,
        output_tokens=out,
    )
    assert any("short-context" in n for n in below_notes)


def test_plan_scan_applies_pricing_overhead_not_bare_body():
    from tads.pipeline import plan_scan
    from tads.parsing.sections import estimate_tokens

    text = "Timestamps are 32-bit seconds.\n" * 20
    plan = plan_scan(
        text,
        doc_id="RFC9999",
        provider="openai",
        model=GPT_6_LUNA,
    )
    body = estimate_tokens(text)
    # Whole-document path: estimated input is body + pricing overhead.
    assert plan.cost_estimate.estimated_input_tokens == estimated_request_input_tokens(
        body
    )
    assert (
        plan.cost_estimate.estimated_input_tokens
        == body + PRICING_REQUEST_INPUT_OVERHEAD_TOKENS
    )
    assert plan.cost_estimate.estimated_input_tokens != body + 2000
