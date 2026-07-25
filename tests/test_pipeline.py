# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Pipeline plan_scan tests."""

from tads.pipeline import plan_scan
from tads.schemas.cost import CostBudget
from tads.schemas.report import AnalysisMode

SAMPLE = """\
1. Introduction

A short document about NTP eras and 32-bit seconds.

2. Protocol

Details continue here.
"""


def test_plan_scan_whole_document():
    plan = plan_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="openai",
        model="gpt-4.1-mini",
        context_token_budget=100_000,
    )
    assert plan.analysis_mode == AnalysisMode.WHOLE_DOCUMENT
    assert plan.section_count >= 2
    assert plan.cost_estimate.within_budget is True


def test_plan_scan_respects_token_budget_cap():
    plan = plan_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="openai",
        model="gpt-4.1-mini",
        budget=CostBudget(max_tokens=10),
        context_token_budget=100_000,
    )
    assert plan.cost_estimate.within_budget is False
