"""Pipeline orchestration stubs for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import get_adapter
from tads.cost import estimate_cost_usd
from tads.llm.base import LLMProvider
from tads.llm.registry import get_provider
from tads.parsing.document import ParsedDocument
from tads.parsing.sections import choose_analysis_mode
from tads.privacy import DEFAULT_POLICY, PrivacyPolicy
from tads.schemas.cost import CostBudget, CostEstimate
from tads.schemas.report import AnalysisMode


@dataclass
class ScanPlan:
    """Phase 0 planning result — no LLM calls yet."""

    ref: CorpusDocumentRef
    document: ParsedDocument
    analysis_mode: AnalysisMode
    provider_id: str
    model: str
    cost_estimate: CostEstimate
    privacy: PrivacyPolicy
    section_count: int


def plan_scan(
    text: str,
    *,
    doc_id: str,
    corpus: str = "ietf",
    provider: str = "openai",
    model: Optional[str] = None,
    budget: Optional[CostBudget] = None,
    privacy: PrivacyPolicy = DEFAULT_POLICY,
    context_token_budget: Optional[int] = None,
) -> ScanPlan:
    """Parse a document and produce a costed analysis plan (no side effects)."""
    adapter: CorpusAdapter = get_adapter(corpus)
    ref = adapter.resolve(doc_id)
    document = adapter.parse(text, ref)
    llm: LLMProvider = get_provider(provider)
    model_id = model or llm.default_model()
    window = context_token_budget or llm.context_window_tokens(model_id)
    mode = choose_analysis_mode(document, context_token_budget=window)

    if mode == AnalysisMode.SECTION_AWARE and document.sections:
        input_tokens = sum(llm.estimate_tokens(section.text) for section in document.sections)
        input_tokens += len(document.sections) * 500  # prompt overhead per section
        output_tokens = 1500 * len(document.sections)
    else:
        input_tokens = llm.estimate_tokens(document.text) + 2000
        output_tokens = 1500

    usd, notes = estimate_cost_usd(
        provider=llm.provider_id,
        model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    within = True
    if budget:
        if budget.max_tokens is not None and (input_tokens + output_tokens) > budget.max_tokens:
            within = False
            notes.append(
                f"Estimated tokens {input_tokens + output_tokens} exceed max_tokens={budget.max_tokens}."
            )
        if budget.max_cost_usd is not None and usd is not None and usd > budget.max_cost_usd:
            within = False
            notes.append(
                f"Estimated cost ${usd:.4f} exceeds max_cost_usd=${budget.max_cost_usd:.4f}."
            )
        if budget.max_cost_usd is not None and usd is None:
            notes.append(
                "Cost cap set but USD estimate unavailable; token cap (if any) still applies."
            )

    estimate = CostEstimate(
        provider=llm.provider_id,
        model=model_id,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=usd,
        within_budget=within,
        notes=notes,
    )

    return ScanPlan(
        ref=ref,
        document=document,
        analysis_mode=mode,
        provider_id=llm.provider_id,
        model=model_id,
        cost_estimate=estimate,
        privacy=privacy,
        section_count=len(document.sections),
    )
