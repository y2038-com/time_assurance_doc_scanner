# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Cost estimation and budget enforcement."""

from __future__ import annotations

from typing import Optional, Sequence

from tads.llm.base import LLMProvider
from tads.llm.openai_models import GPT_6_LUNA, openai_model_policy, rates_for_input_tokens
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS
from tads.schemas.cost import CostBudget, CostEstimate

# Re-export the shared heuristic used by planning and Luna tier selection.
# See ``tads.parsing.sections.REQUEST_OVERHEAD_TOKENS``.
PRICING_REQUEST_INPUT_OVERHEAD_TOKENS = REQUEST_OVERHEAD_TOKENS


def estimated_request_input_tokens(content_tokens: int) -> int:
    """Document/section tokens plus shared conservative request overhead.

    Uses ``REQUEST_OVERHEAD_TOKENS`` from planning (system prompt, corpus
    guidance, untrusted-content framing, native structured-output schema).
    """
    return max(0, int(content_tokens)) + REQUEST_OVERHEAD_TOKENS


# Indicative list prices (USD per 1M tokens). Update as models/pricing change.
# None means unknown / usage-based without a published simple rate.
# GPT-6 Luna short/long tiers live in ``tads.llm.openai_models``.
MODEL_PRICING_USD_PER_MTIME: dict[str, dict[str, float]] = {
    "openai:gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "openai:gpt-4.1": {"input": 2.00, "output": 8.00},
    "anthropic:claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "anthropic:claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "gemini:gemini-3.6-flash": {"input": 1.50, "output": 7.50},
    "gemini:gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini:gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini:gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini:gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}


def lookup_pricing(provider: str, model: str) -> Optional[dict[str, float]]:
    key = f"{provider.lower()}:{model.lower()}"
    if key in MODEL_PRICING_USD_PER_MTIME:
        return MODEL_PRICING_USD_PER_MTIME[key]
    # Prefix match on model family
    for priced_key, rates in MODEL_PRICING_USD_PER_MTIME.items():
        p, m = priced_key.split(":", 1)
        if p == provider.lower() and model.lower().startswith(m):
            return rates
    return None


def estimate_request_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[Optional[float], list[str]]:
    """Estimate USD for one provider request (one Chat Completions call)."""
    notes: list[str] = []
    provider_key = provider.lower()
    model_key = model.lower()
    if provider_key == "openai" and model_key == GPT_6_LUNA:
        policy = openai_model_policy(GPT_6_LUNA)
        assert policy.pricing is not None
        in_rate, out_rate = rates_for_input_tokens(policy.pricing, input_tokens)
        cost = (input_tokens / 1_000_000) * in_rate + (
            output_tokens / 1_000_000
        ) * out_rate
        threshold = policy.pricing.long_context_input_threshold
        if threshold is not None and input_tokens > threshold:
            notes.append(
                f"GPT-6 Luna long-context rates applied "
                f"(input tokens {input_tokens} > {threshold})."
            )
        else:
            notes.append(
                "GPT-6 Luna short-context rates applied "
                f"(input tokens {input_tokens} ≤ {threshold})."
            )
        notes.append(
            "USD estimate uses published non-cached input rates; "
            "cached-input discounts are not modeled."
        )
        notes.append(
            "Reasoning tokens are billed as output when used; "
            "non-none reasoning_effort makes the estimate a lower bound."
        )
        notes.append(
            "Short/long tier selection uses the per-request input estimate "
            f"(body plus {REQUEST_OVERHEAD_TOKENS}-token shared request "
            "overhead for system prompt, corpus guidance, untrusted framing, "
            "and native structured-output schema), not body tokens alone."
        )
        return round(cost, 6), notes

    rates = lookup_pricing(provider, model)
    if rates is None:
        notes.append(
            f"No pricing table entry for {provider}:{model}; USD estimate unavailable."
        )
        return None, notes
    cost = (input_tokens / 1_000_000) * rates["input"] + (
        output_tokens / 1_000_000
    ) * rates["output"]
    notes.append("USD estimate uses the built-in indicative pricing table.")
    return round(cost, 6), notes


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[Optional[float], list[str]]:
    """Estimate USD treating the token totals as a single request.

    Prefer :func:`estimate_cost_usd_for_requests` when a scan issues multiple
    completions (section-aware or future chunking) so long-context thresholds
    apply per request.
    """
    return estimate_request_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def estimate_cost_usd_for_requests(
    *,
    provider: str,
    model: str,
    requests: Sequence[tuple[int, int]],
) -> tuple[Optional[float], list[str]]:
    """Sum per-request estimates. Each item is ``(input_tokens, output_tokens)``."""
    if not requests:
        return estimate_request_cost_usd(
            provider=provider, model=model, input_tokens=0, output_tokens=0
        )
    total = 0.0
    notes: list[str] = []
    any_known = False
    for index, (inp, out) in enumerate(requests, start=1):
        cost, request_notes = estimate_request_cost_usd(
            provider=provider,
            model=model,
            input_tokens=inp,
            output_tokens=out,
        )
        if cost is None:
            return None, request_notes
        any_known = True
        total += cost
        for note in request_notes:
            labeled = f"request {index}: {note}"
            if labeled not in notes and note not in notes:
                notes.append(note if len(requests) == 1 else labeled)
    if not any_known:
        return None, notes
    if len(requests) > 1:
        notes.append(
            f"Summed {len(requests)} per-request estimates "
            "(section-aware / multi-call pricing)."
        )
    return round(total, 6), notes


def estimate_scan_cost(
    provider: LLMProvider,
    *,
    model: Optional[str] = None,
    document_text: str,
    analysis_units: int = 1,
    expected_output_tokens_per_unit: int = 1500,
    budget: Optional[CostBudget] = None,
) -> CostEstimate:
    """
    Preflight cost estimate.

    `analysis_units` is 1 for whole-document mode, or the number of sections
    that will be analyzed independently. Long-context model rates are applied
    per unit/request, not to the summed document token count.
    """
    model_id = model or provider.default_model()
    doc_tokens = provider.estimate_tokens(document_text)
    if analysis_units <= 1:
        unit_input = estimated_request_input_tokens(doc_tokens)
        requests = [(unit_input, expected_output_tokens_per_unit)]
        est_input = unit_input
        est_output = expected_output_tokens_per_unit
    else:
        # Approximate each section request from a document share plus the same
        # framing/schema overhead used for Luna tier selection. Prefer
        # ``plan_scan`` with measured per-section sizes when available.
        unit_input = estimated_request_input_tokens(
            max(1, doc_tokens // analysis_units)
        )
        requests = [
            (unit_input, expected_output_tokens_per_unit)
            for _ in range(analysis_units)
        ]
        est_input = unit_input * analysis_units
        est_output = expected_output_tokens_per_unit * analysis_units

    usd, notes = estimate_cost_usd_for_requests(
        provider=provider.provider_id,
        model=model_id,
        requests=requests,
    )
    within = True
    if budget:
        if budget.max_tokens is not None and (est_input + est_output) > budget.max_tokens:
            within = False
            notes.append(
                f"Estimated tokens {est_input + est_output} exceed max_tokens={budget.max_tokens}."
            )
        if (
            budget.max_cost_usd is not None
            and usd is not None
            and usd > budget.max_cost_usd
        ):
            within = False
            notes.append(
                f"Estimated cost ${usd:.4f} exceeds max_cost_usd=${budget.max_cost_usd:.4f} "
                "(estimate-based preflight guard, not a hard billing cap)."
            )
        if budget.max_cost_usd is not None and usd is None:
            notes.append(
                "Cost cap set but USD estimate unavailable; token cap (if any) still applies. "
                "--max-cost-usd is estimate-based, not a hard billing cap."
            )

    return CostEstimate(
        provider=provider.provider_id,
        model=model_id,
        estimated_input_tokens=est_input,
        estimated_output_tokens=est_output,
        estimated_cost_usd=usd,
        within_budget=within,
        notes=notes,
    )


def assert_within_budget(estimate: CostEstimate) -> None:
    if not estimate.within_budget:
        raise BudgetExceededError(
            "Scan estimate exceeds configured budget: " + "; ".join(estimate.notes)
        )


class BudgetExceededError(RuntimeError):
    """Raised when a preflight estimate exceeds the user budget."""
