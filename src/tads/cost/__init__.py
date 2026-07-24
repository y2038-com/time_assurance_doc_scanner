"""Cost estimation and budget enforcement."""

from __future__ import annotations

from typing import Optional

from tads.llm.base import LLMProvider
from tads.schemas.cost import CostBudget, CostEstimate

# Indicative list prices (USD per 1M tokens). Update as models/pricing change.
# None means unknown / usage-based without a published simple rate.
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


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[Optional[float], list[str]]:
    notes: list[str] = []
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
    that will be analyzed independently.
    """
    model_id = model or provider.default_model()
    input_per_unit = provider.estimate_tokens(document_text)
    # For section-aware mode callers should pass concatenated section text or
    # average; Phase 0 uses a simple multiplier on full-doc tokens as a ceiling
    # when units > 1 and document_text is the full document.
    if analysis_units <= 1:
        est_input = input_per_unit
        est_output = expected_output_tokens_per_unit
    else:
        # Conservative ceiling: each section call may include overlap/prompt
        # overhead; approximate as full-doc tokens + per-section outputs.
        # Callers in Phase 1 should sum per-section estimates instead.
        est_input = input_per_unit + analysis_units * 500
        est_output = expected_output_tokens_per_unit * analysis_units

    usd, notes = estimate_cost_usd(
        provider=provider.provider_id,
        model=model_id,
        input_tokens=est_input,
        output_tokens=est_output,
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
                f"Estimated cost ${usd:.4f} exceeds max_cost_usd=${budget.max_cost_usd:.4f}."
            )
        if budget.max_cost_usd is not None and usd is None:
            notes.append(
                "Cost cap set but USD estimate unavailable; token cap (if any) still applies."
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
