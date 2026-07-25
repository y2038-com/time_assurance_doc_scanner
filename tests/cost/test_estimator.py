# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Cost estimation tests."""

from tads.cost import BudgetExceededError, assert_within_budget, estimate_scan_cost
from tads.llm import get_provider
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
