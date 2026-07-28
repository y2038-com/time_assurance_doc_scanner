# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Pipeline orchestration: plan and execute scans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from tads import __version__
from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import get_adapter
from tads.cost import assert_within_budget, estimate_cost_usd
from tads.llm.base import ChatMessage, LLMProvider
from tads.llm.registry import get_provider
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.scope import AnalysisScope, ScopedDocument, apply_analysis_scope
from tads.parsing.sections import choose_analysis_mode
from tads.pipeline.parse_findings import (
    FindingParseError,
    extract_json_object,
    parse_findings_payload,
)
from tads.pipeline.validate import enrich_finding_validation
from tads.privacy import DEFAULT_POLICY, PrivacyPolicy
from tads.prompts import (
    PROMPT_FRAMEWORK_VERSION,
    build_json_repair_prompt,
    build_section_prompt,
    build_whole_document_prompt,
)
from tads.schemas.cost import CostBudget, CostEstimate, TokenUsage
from tads.schemas.findings import Finding
from tads.schemas.report import (
    AnalysisMode,
    DocumentIdentity,
    PrivacyMode,
    Report,
    RunMetadata,
)

ProgressCallback = Callable[[str], None]


@dataclass
class ScanPlan:
    """Costed analysis plan (no LLM calls)."""

    ref: CorpusDocumentRef
    document: ParsedDocument
    scoped: ScopedDocument
    analysis_mode: AnalysisMode
    provider_id: str
    model: str
    cost_estimate: CostEstimate
    privacy: PrivacyPolicy
    section_count: int
    scope: AnalysisScope


def plan_scan(
    text: str,
    *,
    doc_id: str,
    corpus: str = "ietf",
    provider: str = "ollama",
    model: Optional[str] = None,
    budget: Optional[CostBudget] = None,
    privacy: PrivacyPolicy = DEFAULT_POLICY,
    context_token_budget: Optional[int] = None,
    force_mode: Optional[AnalysisMode] = None,
    source_path: Optional[str] = None,
    scope: Optional[AnalysisScope] = None,
) -> ScanPlan:
    """Parse a document and produce a costed analysis plan (no side effects)."""
    analysis_scope = scope or AnalysisScope()
    adapter: CorpusAdapter = get_adapter(corpus)
    ref = adapter.resolve(doc_id)
    if source_path:
        ref = CorpusDocumentRef(
            corpus=ref.corpus,
            doc_id=ref.doc_id,
            source_uri=ref.source_uri,
            source_path=source_path,
            media_type=ref.media_type,
            metadata=dict(ref.metadata),
        )
    document = adapter.parse(text, ref)
    scoped = apply_analysis_scope(document, analysis_scope)

    llm: LLMProvider = get_provider(provider)
    model_id = model or llm.default_model()
    window = context_token_budget or llm.context_window_tokens(model_id)
    mode = force_mode or choose_analysis_mode(
        document,
        context_token_budget=window,
        text_override=scoped.text,
    )

    if mode == AnalysisMode.SECTION_AWARE and scoped.sections:
        input_tokens = sum(llm.estimate_tokens(section.text) for section in scoped.sections)
        input_tokens += len(scoped.sections) * 500
        output_tokens = 1500 * len(scoped.sections)
    else:
        input_tokens = llm.estimate_tokens(scoped.text) + 2000
        output_tokens = 4096

    usd, notes = estimate_cost_usd(
        provider=llm.provider_id,
        model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    notes = list(scoped.notes) + notes
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
        scoped=scoped,
        analysis_mode=mode,
        provider_id=llm.provider_id,
        model=model_id,
        cost_estimate=estimate,
        privacy=privacy,
        section_count=len(scoped.sections),
        scope=analysis_scope,
    )


def run_scan(
    text: str,
    *,
    doc_id: str,
    corpus: str = "ietf",
    provider: str = "ollama",
    model: Optional[str] = None,
    budget: Optional[CostBudget] = None,
    privacy: PrivacyPolicy = DEFAULT_POLICY,
    context_token_budget: Optional[int] = None,
    force_mode: Optional[AnalysisMode] = None,
    source_path: Optional[str] = None,
    max_output_tokens: int = 16384,
    enforce_budget: bool = True,
    on_progress: Optional[ProgressCallback] = None,
    save_raw_on_error: Optional[str] = None,
    scope: Optional[AnalysisScope] = None,
) -> Report:
    """Execute a scan and return a canonical Report."""
    plan = plan_scan(
        text,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        budget=budget,
        privacy=privacy,
        context_token_budget=context_token_budget,
        force_mode=force_mode,
        source_path=source_path,
        scope=scope,
    )
    if enforce_budget:
        assert_within_budget(plan.cost_estimate)

    llm = get_provider(plan.provider_id)
    if not llm.is_configured():
        from tads.llm.base import ProviderNotConfiguredError

        raise ProviderNotConfiguredError(
            f"Provider '{plan.provider_id}' is not configured."
        )

    adapter = get_adapter(corpus)
    corpus_notes = adapter.describe()
    started = datetime.now(timezone.utc)
    _progress(on_progress, f"mode={plan.analysis_mode.value} model={plan.model}")
    for note in plan.scoped.notes:
        _progress(on_progress, note)

    findings: list[Finding] = []
    usage_total = TokenUsage()

    if plan.analysis_mode == AnalysisMode.WHOLE_DOCUMENT:
        bundle = build_whole_document_prompt(
            plan.document,
            corpus_notes=corpus_notes,
            text_override=plan.scoped.text,
        )
        _progress(on_progress, "analyzing whole document (scoped)")
        response = llm.complete(
            [
                ChatMessage(role="system", content=bundle.system),
                ChatMessage(role="user", content=bundle.user),
            ],
            model=plan.model,
            max_output_tokens=max_output_tokens,
        )
        usage_total = _add_usage(usage_total, response.usage)
        payload, usage_total = _parse_or_repair(
            llm,
            response.content,
            model=plan.model,
            max_output_tokens=max_output_tokens,
            usage_total=usage_total,
            on_progress=on_progress,
            save_raw_on_error=save_raw_on_error,
            context_label="whole_document",
        )
        findings = parse_findings_payload(payload)
    else:
        summary = _scoped_summary(plan.document, plan.scoped.sections)
        next_id = 1
        for index, section in enumerate(plan.scoped.sections, start=1):
            _progress(
                on_progress,
                f"analyzing section {index}/{len(plan.scoped.sections)} ({section.id})",
            )
            bundle = build_section_prompt(
                plan.document,
                section,
                corpus_notes=corpus_notes,
                document_summary=summary,
            )
            response = llm.complete(
                [
                    ChatMessage(role="system", content=bundle.system),
                    ChatMessage(role="user", content=bundle.user),
                ],
                model=plan.model,
                max_output_tokens=max_output_tokens,
            )
            usage_total = _add_usage(usage_total, response.usage)
            try:
                payload, usage_total = _parse_or_repair(
                    llm,
                    response.content,
                    model=plan.model,
                    max_output_tokens=max_output_tokens,
                    usage_total=usage_total,
                    on_progress=on_progress,
                    save_raw_on_error=None,
                    context_label=section.id,
                    allow_skip=True,
                )
            except FindingParseError:
                _progress(on_progress, f"warning: unparseable response for {section.id}")
                continue
            if payload is None:
                continue
            batch = parse_findings_payload(
                payload,
                id_start=next_id,
                default_section_id=section.id,
                default_section_title=section.title,
            )
            findings.extend(batch)
            next_id += len(batch)

    findings = [enrich_finding_validation(f) for f in findings]
    actual_cost, _ = estimate_cost_usd(
        provider=plan.provider_id,
        model=plan.model,
        input_tokens=usage_total.input_tokens,
        output_tokens=usage_total.output_tokens,
    )

    completed = datetime.now(timezone.utc)
    report = Report(
        document=DocumentIdentity(
            corpus=plan.document.corpus,
            doc_id=plan.document.doc_id,
            title=plan.document.title,
            source_uri=plan.document.source_uri,
            source_path=plan.document.source_path or source_path,
            content_sha256=plan.document.content_sha256,
            media_type=plan.document.media_type,
        ),
        run=RunMetadata(
            scanner_version=__version__,
            started_at=started,
            completed_at=completed,
            provider=plan.provider_id,
            model=plan.model,
            analysis_mode=plan.analysis_mode,
            privacy_mode=privacy.mode,
            prompt_framework_version=PROMPT_FRAMEWORK_VERSION,
            include_front_matter=plan.scope.include_front_matter,
            include_index_and_acknowledgments=plan.scope.include_index_and_acknowledgments,
            sections_total=plan.scoped.total_sections_before,
            sections_analyzed=len(plan.scoped.sections),
            skipped_front_matter_sections=plan.scoped.skipped_front_matter_sections,
            skipped_index_ack_sections=plan.scoped.skipped_index_ack_sections,
            scope_truncated=plan.scoped.truncated,
            max_sections=plan.scope.max_sections,
            max_chars=plan.scope.max_chars,
            max_input_tokens=plan.scope.max_input_tokens,
            scope_notes=list(plan.scoped.notes),
        ),
        cost_estimate=plan.cost_estimate,
        actual_usage=usage_total,
        actual_cost_usd=actual_cost,
        findings=findings,
    )
    _progress(on_progress, f"done: {len(findings)} findings")
    return report


def _scoped_summary(
    document: ParsedDocument,
    sections: list[Section],
    *,
    max_sections: int = 40,
) -> str:
    titles = [s.title for s in sections[:max_sections]]
    more = ""
    if len(sections) > max_sections:
        more = f"\n... ({len(sections) - max_sections} more sections)"
    return (
        f"Title: {document.title or document.doc_id}\n"
        f"Analyzed sections:\n- " + "\n- ".join(titles) + more
    )


def _add_usage(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
    )


def _progress(callback: Optional[ProgressCallback], message: str) -> None:
    if callback:
        callback(message)


def _parse_or_repair(
    llm: LLMProvider,
    content: str,
    *,
    model: str,
    max_output_tokens: int,
    usage_total: TokenUsage,
    on_progress: Optional[ProgressCallback],
    save_raw_on_error: Optional[str],
    context_label: str,
    allow_skip: bool = False,
) -> tuple[Optional[dict], TokenUsage]:
    try:
        return extract_json_object(content), usage_total
    except FindingParseError as first_error:
        _progress(on_progress, f"repairing JSON for {context_label}")
        repair = build_json_repair_prompt(content)
        try:
            repaired = llm.complete(
                [
                    ChatMessage(role="system", content=repair.system),
                    ChatMessage(role="user", content=repair.user),
                ],
                model=model,
                max_output_tokens=max_output_tokens,
            )
            usage_total = _add_usage(usage_total, repaired.usage)
            return extract_json_object(repaired.content), usage_total
        except FindingParseError as second_error:
            if save_raw_on_error:
                from pathlib import Path

                path = Path(save_raw_on_error)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                _progress(on_progress, f"saved raw model output to {path}")
            if allow_skip:
                return None, usage_total
            hint = (
                "Model output was not valid findings JSON (often truncation or "
                "extra commentary). Try --max-output-tokens 16384 or "
                "--force-sections, or inspect the saved raw output."
            )
            raise FindingParseError(
                f"{second_error}. {hint}",
                raw=getattr(second_error, "raw", "") or content,
            ) from first_error
