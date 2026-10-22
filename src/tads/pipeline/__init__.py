# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Pipeline orchestration: plan and execute scans."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from tads import __version__
from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import detect_corpus, get_adapter
from tads.cost import (
    assert_within_budget,
    estimate_cost_usd_for_requests,
    estimated_request_input_tokens,
)
from tads.errors import ProviderRequestError, PublicScanError, public_error_text
from tads.output import OutputLevel
from tads.llm.base import (
    DEFAULT_LLM_TEMPERATURE,
    ChatMessage,
    LLMProvider,
    ModelOutputTruncatedError,
)
from tads.llm.openai_models import (
    openai_model_policy,
    recorded_reasoning_effort,
    resolve_reasoning_effort,
)
from tads.llm.registry import get_provider
from tads.parsing.chunks import (
    DEFAULT_MAX_SECTIONS_PER_REQUEST,
    SectionChunk,
    SectionChunkingError,
    pack_section_chunks,
)
from tads.parsing.clauses import assess_clause_parse_health
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.scope import AnalysisScope, ScopedDocument, apply_analysis_scope
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS, choose_analysis_mode
from tads.pipeline.parse_findings import (
    EVIDENCE_QUOTE_NOT_IN_CLAIMED_SECTION,
    FindingParseError,
    extract_json_object,
    parse_findings_payload,
)
from tads.pipeline.recovery import (
    DEFAULT_MAX_RECOVERY_REQUESTS,
    LogicalRequestTally,
    RawDiagnosticSink,
    RecoveryBudget,
    RecoveryBudgetExhausted,
    RecoveryLimitedProvider,
    RangeOutcome,
    UnanalyzedLeaf,
    bisect_bindings,
    failed_range,
    is_recoverable_model_output_failure,
    merge_range_outcomes,
    opaque_range_slug,
    unanalyzed_category_for,
    validate_max_recovery_requests,
)
from tads.pipeline.validate import enrich_finding_validation, verify_finding_source
from tads.privacy import DEFAULT_POLICY, PrivacyPolicy
from tads.prompts import (
    PROMPT_FRAMEWORK_VERSION,
    build_json_repair_prompt,
    build_section_chunk_prompt,
    build_semantic_correction_prompt,
    build_whole_document_prompt,
)
from tads.schemas.cost import CostBudget, CostEstimate, TokenUsage
from tads.schemas.findings import Finding
from tads.schemas.report import (
    AnalysisMode,
    AnalysisStatus,
    DocumentIdentity,
    PrivacyMode,
    Report,
    RequestBatching,
    RunMetadata,
    UnanalyzedSection,
)

RAW_ON_ERROR_MAX_BYTES = 256 * 1024
RAW_ON_ERROR_TRUNCATION_MARKER = "\n...[truncated]...\n"
RAW_SAVE_FAILED_NOTICE = "Raw diagnostic output could not be saved."
_RAW_TMP_PREFIX = ".tads-raw-"

ProgressCallback = Callable[[str], None]


class SemanticCorrectionError(PublicScanError, RuntimeError):
    """Correction secondary request failed before usable model text.

    ``raw`` holds the original primary model text for opt-in raw-on-error only.
    Never include ``raw`` or provider details in ``str(exc)`` / progress text.
    """

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class UnreliableSectionizationError(PublicScanError, RuntimeError):
    """
    Planning refused: clause parse health is poor and whole-document analysis
    would not fit the model context budget.
    """


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
    section_chunking: bool = True
    max_sections_per_request: int = DEFAULT_MAX_SECTIONS_PER_REQUEST
    chunks: tuple[SectionChunk, ...] = field(default_factory=tuple)
    request_batching: RequestBatching = RequestBatching.WHOLE_DOCUMENT
    chunk_section_counts: tuple[int, ...] = field(default_factory=tuple)


def plan_scan(
    text: str,
    *,
    doc_id: str,
    corpus: Optional[str] = None,
    provider: str = "ollama",
    model: Optional[str] = None,
    budget: Optional[CostBudget] = None,
    privacy: PrivacyPolicy = DEFAULT_POLICY,
    context_token_budget: Optional[int] = None,
    force_mode: Optional[AnalysisMode] = None,
    source_path: Optional[str] = None,
    source_uri: Optional[str] = None,
    retrieved_uri: Optional[str] = None,
    scope: Optional[AnalysisScope] = None,
    max_output_tokens: int = 16384,
    reasoning_effort: Optional[str] = None,
    section_chunking: bool = True,
    max_sections_per_request: int = DEFAULT_MAX_SECTIONS_PER_REQUEST,
) -> ScanPlan:
    """Parse a document and produce a costed analysis plan (no side effects)."""
    analysis_scope = scope or AnalysisScope()
    resolved_corpus = corpus or detect_corpus(doc_id) or "generic"
    adapter: CorpusAdapter = get_adapter(resolved_corpus)
    ref = adapter.resolve(doc_id)
    # Adapter resolve() URLs are fetch locations, not this scan's provenance.
    ref = CorpusDocumentRef(
        corpus=ref.corpus,
        doc_id=ref.doc_id,
        source_uri=source_uri,
        source_path=source_path,
        retrieved_uri=retrieved_uri,
        media_type=ref.media_type,
        metadata=dict(ref.metadata),
    )
    document = adapter.parse(text, ref)
    scoped = apply_analysis_scope(document, analysis_scope)
    parse_health = assess_clause_parse_health(
        document.sections,
        document_chars=len(document.text),
    )
    health_notes = list(parse_health.notes)

    llm: LLMProvider = get_provider(provider)
    model_id = model or llm.default_model()
    # Validate reasoning effort before any completion HTTP (plan makes none).
    effective_effort = recorded_reasoning_effort(
        provider_id=llm.provider_id,
        model=model_id,
        requested=reasoning_effort,
    )
    window = context_token_budget or llm.context_window_tokens(model_id)
    mode = force_mode or choose_analysis_mode(
        document,
        context_token_budget=window,
        output_reserve_tokens=max_output_tokens,
        text_override=scoped.text,
    )

    # Do not silently trust section-aware coverage on a structurally bad parse.
    # If SECTION_AWARE was selected because the scoped text exceeds the context
    # budget, do not fall back to WHOLE_DOCUMENT (that would send an oversized
    # prompt). Fail closed during planning instead.
    if not parse_health.ok and mode == AnalysisMode.SECTION_AWARE:
        if force_mode is None:
            whole_fits = (
                choose_analysis_mode(
                    document,
                    context_token_budget=window,
                    output_reserve_tokens=max_output_tokens,
                    text_override=scoped.text,
                )
                == AnalysisMode.WHOLE_DOCUMENT
            )
            if whole_fits:
                mode = AnalysisMode.WHOLE_DOCUMENT
                health_notes.append(
                    "Using whole-document analysis because clause sectionization "
                    "looks unreliable — section coverage would be misleading."
                )
            else:
                detail = "; ".join(parse_health.notes) or (
                    "clause sectionization looks unreliable"
                )
                raise UnreliableSectionizationError(
                    "Cannot plan analysis: clause sectionization looks unreliable "
                    "and the scoped document does not fit whole-document mode for "
                    f"this provider/model context budget ({window} tokens). "
                    f"{detail} "
                    "Narrow the scan with --max-sections / --max-input-tokens / "
                    "--max-chars, or re-check after improving the source text. "
                    "Use --force-sections only if you accept untrustworthy "
                    "section coverage."
                )
        else:
            health_notes.append(
                "Section-aware mode forced despite unreliable clause "
                "sectionization; treat section coverage as untrustworthy."
            )

    chunks: tuple[SectionChunk, ...] = ()
    chunk_section_counts: tuple[int, ...] = ()
    if mode == AnalysisMode.SECTION_AWARE and scoped.sections:
        summary = _scoped_summary(document, scoped.sections)
        packed = pack_section_chunks(
            list(scoped.sections),
            context_window=window,
            max_output_tokens=max_output_tokens,
            max_sections_per_request=max_sections_per_request,
            document_id=document.doc_id,
            document_title=document.title or "",
            section_summary=summary,
            section_chunking=section_chunking,
        )
        chunks = tuple(packed)
        chunk_section_counts = tuple(c.section_count for c in chunks)
        request_sizes = [
            (chunk.estimated_input_tokens, chunk.estimated_output_tokens)
            for chunk in chunks
        ]
        input_tokens = sum(size[0] for size in request_sizes)
        output_tokens = sum(size[1] for size in request_sizes)
        if not section_chunking:
            request_batching = RequestBatching.PER_SECTION
        elif any(n > 1 for n in chunk_section_counts):
            request_batching = RequestBatching.SECTION_CHUNKS
        else:
            # Automatic packing that yields only singletons records per_section.
            request_batching = RequestBatching.PER_SECTION
    elif mode == AnalysisMode.SECTION_AWARE:
        # Empty analyzed scope: zero primary requests; honest empty counts.
        chunks = ()
        chunk_section_counts = ()
        request_sizes = []
        input_tokens = 0
        output_tokens = 0
        request_batching = RequestBatching.PER_SECTION
    else:
        request_sizes = [
            (
                estimated_request_input_tokens(llm.estimate_tokens(scoped.text)),
                4096,
            )
        ]
        input_tokens = request_sizes[0][0]
        output_tokens = request_sizes[0][1]
        request_batching = RequestBatching.WHOLE_DOCUMENT
        # One primary request; count records analyzed section cardinality for
        # provenance (not a packing plan). Empty analyzed scope still one request.
        analyzed = len(scoped.sections) if scoped.sections else 0
        chunk_section_counts = (max(1, analyzed),)

    usd, notes = estimate_cost_usd_for_requests(
        provider=llm.provider_id,
        model=model_id,
        requests=request_sizes,
    )
    notes = list(scoped.notes) + health_notes + notes
    if effective_effort is not None:
        notes.append(f"Effective reasoning_effort for this plan: {effective_effort}.")
        if effective_effort != "none":
            notes.append(
                "Non-none reasoning_effort makes the USD estimate a lower bound "
                "until provider-reported completion tokens are known."
            )
    notes.append(
        f"Planning reserves {max_output_tokens} completion tokens plus "
        f"{REQUEST_OVERHEAD_TOKENS}-token shared request overhead "
        "(system prompt, corpus guidance, untrusted framing, native "
        "structured-output schema) inside the model context window."
    )
    if mode == AnalysisMode.SECTION_AWARE:
        notes.append(
            f"Request batching: {request_batching.value}; "
            f"{len(chunk_section_counts)} primary request(s); "
            f"sections per request: {list(chunk_section_counts)}."
        )
        notes.append(
            "Up to one additional repair or section-local evidence-correction "
            "request per logical primary may occur at execution time and is not "
            "included in this estimate."
        )
        notes.append(
            "Adaptive recovery of invalid section-chunk output may add further "
            "unestimated model requests and cost (bounded at scan time by "
            f"--max-recovery-requests, default {DEFAULT_MAX_RECOVERY_REQUESTS}; "
            "0 disables splitting)."
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
                f"Estimated cost ${usd:.4f} exceeds max_cost_usd=${budget.max_cost_usd:.4f} "
                "(estimate-based preflight guard, not a hard billing cap)."
            )
        if budget.max_cost_usd is not None and usd is None:
            notes.append(
                "Cost cap set but USD estimate unavailable; token cap (if any) still applies. "
                "--max-cost-usd is estimate-based, not a hard billing cap."
            )
        elif budget.max_cost_usd is not None:
            notes.append(
                "--max-cost-usd is an estimate-based preflight guard, not a hard billing cap."
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
        section_chunking=section_chunking,
        max_sections_per_request=max_sections_per_request,
        chunks=chunks,
        request_batching=request_batching,
        chunk_section_counts=chunk_section_counts,
    )


def run_scan(
    text: str,
    *,
    doc_id: str,
    corpus: Optional[str] = None,
    provider: str = "ollama",
    model: Optional[str] = None,
    budget: Optional[CostBudget] = None,
    privacy: PrivacyPolicy = DEFAULT_POLICY,
    context_token_budget: Optional[int] = None,
    force_mode: Optional[AnalysisMode] = None,
    source_path: Optional[str] = None,
    source_uri: Optional[str] = None,
    retrieved_uri: Optional[str] = None,
    max_output_tokens: int = 16384,
    enforce_budget: bool = True,
    on_progress: Optional[ProgressCallback] = None,
    save_raw_on_error: Optional[str] = None,
    scope: Optional[AnalysisScope] = None,
    reasoning_effort: Optional[str] = None,
    section_chunking: bool = True,
    max_sections_per_request: int = DEFAULT_MAX_SECTIONS_PER_REQUEST,
    max_recovery_requests: int = DEFAULT_MAX_RECOVERY_REQUESTS,
) -> Report:
    """Execute a scan and return a canonical Report."""
    max_recovery_requests = validate_max_recovery_requests(max_recovery_requests)
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
        source_uri=source_uri,
        retrieved_uri=retrieved_uri,
        scope=scope,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
        section_chunking=section_chunking,
        max_sections_per_request=max_sections_per_request,
    )
    if enforce_budget:
        assert_within_budget(plan.cost_estimate)

    llm = get_provider(plan.provider_id)
    if not llm.is_configured():
        from tads.llm.base import ProviderNotConfiguredError

        raise ProviderNotConfiguredError(
            f"Provider '{plan.provider_id}' is not configured."
        )

    effective_effort = recorded_reasoning_effort(
        provider_id=plan.provider_id,
        model=plan.model,
        requested=reasoning_effort,
    )
    adapter = get_adapter(plan.document.corpus)
    corpus_notes = adapter.describe()
    started = datetime.now(timezone.utc)
    _progress(on_progress, f"mode={plan.analysis_mode.value} model={plan.model}")
    for note in plan.scoped.notes:
        _progress(on_progress, note)

    findings: list[Finding] = []
    usage_total = TokenUsage()
    request_usages: list[tuple[int, int]] = []
    analysis_request_count = 0
    repair_request_count = 0
    recovery_split_count = 0
    recovery_request_count = 0
    succeeded_section_ids: list[str] = []
    unanalyzed_leaves: list = []
    raw_sink = (
        save_raw_on_error
        if isinstance(save_raw_on_error, RawDiagnosticSink)
        else RawDiagnosticSink(save_raw_on_error)
    )
    save_raw_on_error = raw_sink
    recorded_temperature = _recorded_temperature(
        provider_id=plan.provider_id,
        model=plan.model,
        reasoning_effort=reasoning_effort,
    )

    if plan.analysis_mode == AnalysisMode.WHOLE_DOCUMENT:
        bundle = build_whole_document_prompt(
            plan.document,
            corpus_notes=corpus_notes,
            text_override=plan.scoped.text,
        )
        _progress(on_progress, "analyzing whole document (scoped)")
        response = _complete_checked(
            llm,
            [
                ChatMessage(role="system", content=bundle.system),
                ChatMessage(role="user", content=bundle.user),
            ],
            model=plan.model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            save_raw_on_error=save_raw_on_error,
            on_progress=on_progress,
        )
        analysis_request_count += 1
        usage_total = _add_usage(usage_total, response.usage)
        request_usages.append(
            (response.usage.input_tokens, response.usage.output_tokens)
        )
        payload, usage_total, source_text, request_usages, repairs = (
            _parse_or_repair(
                llm,
                response.content,
                model=plan.model,
                max_output_tokens=max_output_tokens,
                usage_total=usage_total,
                on_progress=on_progress,
                save_raw_on_error=save_raw_on_error,
                reasoning_effort=reasoning_effort,
                request_usages=request_usages,
            )
        )
        repair_request_count += repairs
        findings = _parse_items_or_raw(
            payload,
            source_text,
            save_raw_on_error=save_raw_on_error,
            on_progress=on_progress,
        )
    else:
        summary = _scoped_summary(plan.document, plan.scoped.sections)
        recovery_budget = RecoveryBudget(limit=max_recovery_requests)
        last_recoverable: BaseException | None = None
        total_chunks = len(plan.chunks)

        def analyze_unit(
            active_llm,
            bindings: tuple,
            *,
            progress_unit: str,
        ) -> list[Finding]:
            nonlocal usage_total, request_usages
            opaque_refs = [b.opaque_ref for b in bindings]
            sections = [b.section for b in bindings]
            bundle = build_section_chunk_prompt(
                plan.document,
                opaque_refs=opaque_refs,
                sections=sections,
                corpus_notes=corpus_notes,
                document_summary=summary,
            )
            response = _complete_checked(
                active_llm,
                [
                    ChatMessage(role="system", content=bundle.system),
                    ChatMessage(role="user", content=bundle.user),
                ],
                model=plan.model,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
                save_raw_on_error=save_raw_on_error,
                on_progress=on_progress,
                raw_slug=opaque_range_slug(bindings),
            )
            payload, _ignored_usage, source_text, _ignored_usages, repairs = (
                _parse_or_repair(
                    active_llm,
                    response.content,
                    model=plan.model,
                    max_output_tokens=max_output_tokens,
                    usage_total=usage_total,
                    on_progress=on_progress,
                    save_raw_on_error=save_raw_on_error,
                    reasoning_effort=reasoning_effort,
                    request_usages=request_usages,
                    progress_unit=progress_unit,
                    raw_slug=opaque_range_slug(bindings),
                )
            )
            secondary_used = repairs > 0
            batch, _ignored_usage, _ignored_usages, _corr_repairs = (
                _parse_section_payload_or_correct(
                    active_llm,
                    payload,
                    source_text=source_text,
                    secondary_used=secondary_used,
                    document=plan.document,
                    opaque_refs=opaque_refs,
                    sections=sections,
                    corpus_notes=corpus_notes,
                    document_summary=summary,
                    model=plan.model,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                    save_raw_on_error=save_raw_on_error,
                    on_progress=on_progress,
                    usage_total=usage_total,
                    request_usages=request_usages,
                    id_start=1,
                    section_bindings={b.opaque_ref: b for b in bindings},
                    document_text=plan.document.text,
                    progress_unit=progress_unit,
                    raw_slug=opaque_range_slug(bindings),
                )
            )
            return batch

        def analyze_range(
            active_llm,
            bindings: tuple,
            *,
            origin_index: int,
            origin_total: int,
            is_recovery: bool,
        ) -> RangeOutcome:
            nonlocal last_recoverable
            n = len(bindings)
            if is_recovery:
                progress_unit = f"recovery ({n} sections)"
                _progress(on_progress, f"analyzing recovery ({n} sections)")
            else:
                progress_unit = f"chunk {origin_index}/{origin_total}"
                _progress(
                    on_progress,
                    f"analyzing {progress_unit} ({n} sections)",
                )
            tally = LogicalRequestTally()
            counted = tally.wrap(active_llm)

            def _commit_tally() -> None:
                nonlocal usage_total, request_usages
                for usage in tally.usages:
                    usage_total = _add_usage(usage_total, usage)
                    request_usages.append(
                        (usage.input_tokens, usage.output_tokens)
                    )

            try:
                batch = analyze_unit(
                    counted,
                    bindings,
                    progress_unit=progress_unit,
                )
            except RecoveryBudgetExhausted:
                _commit_tally()
                _progress(
                    on_progress,
                    "recovery budget exhausted",
                    min_level=OutputLevel.DEBUG,
                )
                return failed_range(
                    bindings,
                    category="recovery_limit_exhausted",
                    analysis_requests=tally.primary,
                    repair_requests=tally.repair,
                )
            except Exception as exc:
                if not is_recoverable_model_output_failure(exc):
                    raise
                last_recoverable = exc
                _commit_tally()
                category = unanalyzed_category_for(exc)
                _progress(
                    on_progress,
                    "recovery eligible failure="
                    f"{category} remaining_budget={recovery_budget.remaining}",
                    min_level=OutputLevel.DEBUG,
                )
                if n == 1:
                    _progress(on_progress, "recovery reached one failed section")
                    return failed_range(
                        bindings,
                        category=category,
                        analysis_requests=tally.primary,
                        repair_requests=tally.repair,
                    )
                if recovery_budget.remaining < 1:
                    _progress(
                        on_progress,
                        "recovery budget exhausted",
                        min_level=OutputLevel.DEBUG,
                    )
                    return failed_range(
                        bindings,
                        category="recovery_limit_exhausted",
                        analysis_requests=tally.primary,
                        repair_requests=tally.repair,
                    )
                left, right = bisect_bindings(bindings)
                recovery_budget.record_split()
                if not is_recovery:
                    _progress(
                        on_progress,
                        f"chunk {origin_index}/{origin_total} failed validation",
                    )
                _progress(
                    on_progress,
                    f"retrying as two smaller section ranges ({len(left)}+{len(right)})",
                )
                _progress(
                    on_progress,
                    "recovery split "
                    f"child_sections={len(left)}+{len(right)} "
                    f"remaining_budget={recovery_budget.remaining}",
                    min_level=OutputLevel.DEBUG,
                )
                child_llm = RecoveryLimitedProvider(llm, recovery_budget)
                left_out = analyze_range(
                    child_llm,
                    left,
                    origin_index=origin_index,
                    origin_total=origin_total,
                    is_recovery=True,
                )
                right_out = analyze_range(
                    child_llm,
                    right,
                    origin_index=origin_index,
                    origin_total=origin_total,
                    is_recovery=True,
                )
                merged = merge_range_outcomes(left_out, right_out)
                merged.analysis_requests += tally.primary
                merged.repair_requests += tally.repair
                return merged
            _commit_tally()
            return RangeOutcome(
                findings=list(batch),
                succeeded_section_ids=[b.canonical_id for b in bindings],
                succeeded_chars=sum(b.section.char_count for b in bindings),
                analysis_requests=tally.primary,
                repair_requests=tally.repair,
            )

        for chunk in plan.chunks:
            outcome = analyze_range(
                llm,
                chunk.bindings,
                origin_index=chunk.chunk_index + 1,
                origin_total=total_chunks,
                is_recovery=False,
            )
            findings.extend(outcome.findings)
            succeeded_section_ids.extend(outcome.succeeded_section_ids)
            unanalyzed_leaves.extend(outcome.failures)
            analysis_request_count += outcome.analysis_requests
            repair_request_count += outcome.repair_requests

        recovery_split_count = recovery_budget.split_count
        recovery_request_count = recovery_budget.used
        if not succeeded_section_ids and last_recoverable is not None:
            raise last_recoverable
        findings = _renumber_findings(findings, plan.scoped.sections)

    if plan.analysis_mode == AnalysisMode.WHOLE_DOCUMENT:
        succeeded_section_ids = [s.id for s in plan.scoped.sections]
        unanalyzed_leaves = []

    findings = [enrich_finding_validation(f) for f in findings]
    if plan.chunks:
        section_by_id = {s.id: s for s in plan.scoped.sections}
        verified: list[Finding] = []
        for finding in findings:
            sid = (
                finding.location.section_id
                if finding.location is not None
                else None
            )
            section = section_by_id.get(sid) if sid else None
            haystack = section.text if section is not None else ""
            verified.append(
                verify_finding_source(
                    finding, haystack, update_offsets=False
                )
            )
        findings = verified
    else:
        findings = [
            verify_finding_source(f, plan.scoped.text) for f in findings
        ]
    if not request_usages:
        request_usages = [
            (usage_total.input_tokens, usage_total.output_tokens)
        ]
    actual_cost, _ = estimate_cost_usd_for_requests(
        provider=plan.provider_id,
        model=plan.model,
        requests=request_usages,
    )

    completed = datetime.now(timezone.utc)
    planned_ids = [s.id for s in plan.scoped.sections]
    char_by_id = {s.id: s.char_count for s in plan.scoped.sections}
    seen_success: set[str] = set()
    unique_succeeded: list[str] = []
    for sid in succeeded_section_ids:
        if sid in seen_success:
            continue
        seen_success.add(sid)
        unique_succeeded.append(sid)
    succeeded_section_ids = unique_succeeded
    # Keep document order for public failed-section identifiers.
    failed_ids = [sid for sid in planned_ids if sid not in seen_success]
    id_to_leaf = {leaf.section_id: leaf for leaf in unanalyzed_leaves}
    ordered_leaves = []
    for sid in failed_ids:
        leaf = id_to_leaf.get(sid)
        if leaf is None:
            leaf = UnanalyzedLeaf(
                section_id=sid,
                category="invalid_model_output",
                char_count=char_by_id.get(sid, 0),
            )
        ordered_leaves.append(leaf)
    succeeded_chars = sum(
        s.char_count for s in plan.scoped.sections if s.id in seen_success
    )
    failed_chars = sum(leaf.char_count for leaf in ordered_leaves)
    analysis_status = (
        AnalysisStatus.PARTIAL if ordered_leaves else AnalysisStatus.COMPLETE
    )
    coverage_complete = (
        _eligible_coverage_complete(plan.scoped)
        and analysis_status == AnalysisStatus.COMPLETE
    )
    scope_notes = list(plan.scoped.notes)
    if ordered_leaves:
        scope_notes.append(
            "Partial analysis: "
            f"{len(ordered_leaves)} of {len(planned_ids)} planned sections "
            "were unrecoverable after bounded adaptive recovery. Failed "
            "sections contribute no findings. Human review is required."
        )
        _progress(on_progress, "scan completed with partial coverage")
    elif recovery_split_count:
        scope_notes.append(
            "Adaptive recovery split one or more invalid section chunks; "
            "all planned sections were analyzed successfully."
        )
    unanalyzed_sections = [
        UnanalyzedSection(section_id=leaf.section_id, category=leaf.category)
        for leaf in ordered_leaves
    ]
    report = Report(
        document=DocumentIdentity(
            corpus=plan.document.corpus,
            doc_id=plan.document.doc_id,
            title=plan.document.title,
            source_uri=plan.document.source_uri,
            source_path=plan.document.source_path,
            retrieved_uri=plan.document.retrieved_uri,
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
            scope_notes=scope_notes,
            eligible_sections=plan.scoped.eligible_sections,
            eligible_chars=plan.scoped.eligible_chars,
            analyzed_chars=plan.scoped.analyzed_chars,
            document_chars=plan.scoped.document_chars,
            temperature=recorded_temperature,
            max_output_tokens=max_output_tokens,
            reasoning_effort=effective_effort,
            request_batching=plan.request_batching,
            analysis_request_count=analysis_request_count,
            repair_request_count=repair_request_count,
            total_provider_request_count=(
                analysis_request_count + repair_request_count
            ),
            chunk_section_counts=list(plan.chunk_section_counts),
            coverage_complete=coverage_complete,
            analysis_status=analysis_status,
            sections_succeeded=len(succeeded_section_ids),
            succeeded_chars=succeeded_chars,
            sections_failed=len(ordered_leaves),
            failed_chars=failed_chars,
            failed_section_ids=failed_ids,
            unanalyzed_sections=unanalyzed_sections,
            recovery_split_count=recovery_split_count,
            recovery_request_count=recovery_request_count,
        ),
        cost_estimate=plan.cost_estimate,
        actual_usage=usage_total,
        actual_cost_usd=actual_cost,
        findings=findings,
    )
    _progress(on_progress, f"done: {len(findings)} candidates")
    return report


def _eligible_coverage_complete(scoped: ScopedDocument) -> bool:
    """True only when every eligible section was analyzed (no intentional caps)."""
    if scoped.truncated:
        return False
    return scoped.eligible_sections == len(scoped.sections)


def _renumber_findings(findings: list[Finding], sections: list[Section]) -> list[Finding]:
    """Assign F-NNN ids in original document order, not recursion completion order."""
    order = {section.id: index for index, section in enumerate(sections)}
    indexed = list(enumerate(findings))

    def _sort_key(pair: tuple[int, Finding]) -> tuple[int, int]:
        position, finding = pair
        sid = finding.location.section_id if finding.location is not None else None
        section_rank = order.get(sid, len(order) + 1) if sid else len(order) + 1
        return (section_rank, position)

    indexed.sort(key=_sort_key)
    return [
        finding.model_copy(update={"id": f"F-{index:03d}"})
        for index, (_, finding) in enumerate(indexed, start=1)
    ]


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
    reasoning: Optional[int] = None
    if a.reasoning_tokens is not None or b.reasoning_tokens is not None:
        reasoning = (a.reasoning_tokens or 0) + (b.reasoning_tokens or 0)
    return TokenUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        reasoning_tokens=reasoning,
    )


def _complete_checked(
    llm: LLMProvider,
    messages: list[ChatMessage],
    *,
    model: str,
    max_output_tokens: int,
    reasoning_effort: Optional[str],
    save_raw_on_error: Optional[str],
    on_progress: Optional[ProgressCallback],
    raw_slug: Optional[str] = None,
):
    """Run complete(); persist raw on truncation via the opt-in path only."""
    try:
        return llm.complete(
            messages,
            model=model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
    except RecoveryBudgetExhausted:
        raise
    except ModelOutputTruncatedError as exc:
        raw = getattr(exc, "raw", "") or ""
        _persist_raw_or_notice(
            save_raw_on_error, raw, on_progress, slug=raw_slug or "truncated"
        )
        raise


def _progress(
    callback: Optional[ProgressCallback],
    message: str,
    *,
    min_level: OutputLevel = OutputLevel.NORMAL,
) -> None:
    """Emit a TADS-owned progress string.

    ``min_level`` is chosen by this caller, never by parsing ``message``.
    Alternate callbacks without ``emit_at`` receive only normal-level events
    and never a ``debug:`` / ``verbose:`` prefix.
    """
    if not callback:
        return
    emit_at = getattr(callback, "emit_at", None)
    if callable(emit_at):
        emit_at(message, min_level)
        return
    if min_level > OutputLevel.NORMAL:
        return
    callback(message)


def cap_raw_output(
    text: str,
    *,
    max_bytes: int = RAW_ON_ERROR_MAX_BYTES,
) -> str:
    """Bound raw-on-error text to ``max_bytes`` UTF-8 bytes, including the marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker = RAW_ON_ERROR_TRUNCATION_MARKER.encode("utf-8")
    budget = max(0, max_bytes - len(marker))
    cut = _utf8_safe_prefix(encoded, budget)
    return cut.decode("utf-8") + RAW_ON_ERROR_TRUNCATION_MARKER


def _utf8_safe_prefix(data: bytes, max_bytes: int) -> bytes:
    """Return ``data[:max_bytes]`` without splitting a UTF-8 sequence."""
    if max_bytes <= 0:
        return b""
    if len(data) <= max_bytes:
        return data
    cut = data[:max_bytes]
    index = len(cut)
    while index > 0 and (cut[index - 1] & 0xC0) == 0x80:
        index -= 1
    if index > 0 and (cut[index - 1] & 0x80):
        index -= 1
    return cut[:index]


def _parse_items_or_raw(
    payload: dict,
    source_text: str,
    *,
    save_raw_on_error: Optional[str],
    on_progress: Optional[ProgressCallback],
    raw_slug: Optional[str] = None,
    id_start: int = 1,
    default_section_id: Optional[str] = None,
    default_section_title: Optional[str] = None,
    section_bindings: Optional[dict] = None,
    document_text: Optional[str] = None,
) -> list[Finding]:
    parse_error: FindingParseError | None = None
    try:
        return parse_findings_payload(
            payload,
            id_start=id_start,
            default_section_id=default_section_id,
            default_section_title=default_section_title,
            section_bindings=section_bindings,
            document_text=document_text,
        )
    except FindingParseError as exc:
        parse_error = exc
    if parse_error is not None:
        persist = parse_error.raw or source_text
        _persist_raw_or_notice(
            save_raw_on_error, persist, on_progress, slug=raw_slug
        )
        raise parse_error from None
    raise FindingParseError("Finding item validation failed")


def _is_evidence_grounding_miss(exc: FindingParseError) -> bool:
    return getattr(exc, "code", None) == EVIDENCE_QUOTE_NOT_IN_CLAIMED_SECTION


def _parse_section_payload_or_correct(
    llm: LLMProvider,
    payload: dict,
    *,
    source_text: str,
    secondary_used: bool,
    document: ParsedDocument,
    opaque_refs: list[str],
    sections: list[Section],
    corpus_notes: Optional[dict],
    document_summary: str,
    model: str,
    max_output_tokens: int,
    reasoning_effort: Optional[str],
    save_raw_on_error: Optional[str],
    on_progress: Optional[ProgressCallback],
    usage_total: TokenUsage,
    request_usages: list[tuple[int, int]],
    id_start: int,
    section_bindings: dict,
    document_text: str,
    progress_unit: Optional[str] = None,
    raw_slug: Optional[str] = None,
) -> tuple[list[Finding], TokenUsage, list[tuple[int, int]], int]:
    """
    Validate a section-aware payload; optionally one semantic correction.

    Returns ``(findings, usage_total, request_usages, correction_repair_count)``
    where ``correction_repair_count`` is 0 or 1. JSON envelope repair and
    semantic correction share one secondary slot per primary.
    """
    try:
        findings = parse_findings_payload(
            payload,
            id_start=id_start,
            section_bindings=section_bindings,
            document_text=document_text,
        )
        return findings, usage_total, request_usages, 0
    except FindingParseError as exc:
        if secondary_used or not _is_evidence_grounding_miss(exc):
            persist = exc.raw or source_text
            _persist_raw_or_notice(
                save_raw_on_error, persist, on_progress, slug=raw_slug
            )
            raise exc from None
        primary_failure = exc

    if progress_unit:
        _progress(
            on_progress, f"correcting evidence grounding for {progress_unit}"
        )
    else:
        _progress(on_progress, "correcting evidence grounding")
    correction = build_semantic_correction_prompt(
        document,
        opaque_refs=opaque_refs,
        sections=sections,
        corpus_notes=corpus_notes,
        document_summary=document_summary,
    )
    # Capture only controlled scalars inside except; raise fresh errors outside.
    correction_truncation_raw: str | None = None
    correction_call_failed = False
    corrected = None
    try:
        corrected = _complete_checked(
            llm,
            [
                ChatMessage(role="system", content=correction.system),
                ChatMessage(role="user", content=correction.user),
            ],
            model=model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            save_raw_on_error=save_raw_on_error,
            on_progress=on_progress,
            raw_slug=raw_slug,
        )
    except RecoveryBudgetExhausted:
        raise
    except ModelOutputTruncatedError as exc:
        # _complete_checked already persisted correction raw when enabled.
        correction_truncation_raw = str(exc.raw) if exc.raw else ""
    except Exception:  # noqa: BLE001 - drop provider objects/text at this boundary
        correction_call_failed = True
    if correction_truncation_raw is not None:
        raise ModelOutputTruncatedError(
            "Model output truncated: completion token limit reached",
            raw=correction_truncation_raw,
        )
    if correction_call_failed:
        # No correction model text: keep the primary semantic-failure payload.
        _persist_raw_or_notice(
            save_raw_on_error, source_text, on_progress, slug=raw_slug
        )
        raise SemanticCorrectionError(
            "Semantic evidence correction failed",
            raw=source_text,
        )
    assert corrected is not None

    usage_total = _add_usage(usage_total, corrected.usage)
    usages = list(request_usages)
    usages.append((corrected.usage.input_tokens, corrected.usage.output_tokens))

    # No nested JSON envelope repair for correction responses.
    extract_error: FindingParseError | None = None
    try:
        corr_payload = extract_json_object(corrected.content)
    except FindingParseError as exc:
        extract_error = exc
    if extract_error is not None:
        persist = extract_error.raw or corrected.content
        _persist_raw_or_notice(
            save_raw_on_error, persist, on_progress, slug=raw_slug
        )
        raise extract_error from None

    parse_error: FindingParseError | None = None
    try:
        findings = parse_findings_payload(
            corr_payload,
            id_start=id_start,
            section_bindings=section_bindings,
            document_text=document_text,
        )
    except FindingParseError as exc:
        parse_error = exc
    if parse_error is not None:
        persist = parse_error.raw or corrected.content
        _persist_raw_or_notice(
            save_raw_on_error, persist, on_progress, slug=raw_slug
        )
        raise parse_error from None

    _ = primary_failure  # eligibility already established; discarded on success
    return findings, usage_total, usages, 1


def _persist_raw_or_notice(
    path: Optional[str] | RawDiagnosticSink,
    content: str,
    on_progress: Optional[ProgressCallback],
    *,
    slug: Optional[str] = None,
) -> None:
    sink = path if isinstance(path, RawDiagnosticSink) else RawDiagnosticSink(path)
    dest = sink.destination(slug)
    if not dest:
        return
    if not _save_raw_on_error(dest, content):
        _progress(on_progress, RAW_SAVE_FAILED_NOTICE)
        return
    sink.remember(dest)
    _progress(on_progress, "saved raw model output")


def _save_raw_on_error(path: str, content: str) -> bool:
    """Write capped raw text atomically. Return False on any save failure.

    Never raises. Does not follow a symlink destination. POSIX mode of the
    installed file is 0600. Temporary files in the destination directory are
    removed on failure.
    """
    dest = Path(path)
    tmp_path: Path | None = None
    fd: int | None = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            return False
        text = cap_raw_output(content)
        fd, tmp_name = tempfile.mkstemp(
            prefix=_RAW_TMP_PREFIX,
            suffix=".tmp",
            dir=str(dest.parent),
        )
        tmp_path = Path(tmp_name)
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
        tmp_path = None
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        return dest.is_file() and not dest.is_symlink()
    except OSError:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _recorded_temperature(
    *,
    provider_id: str,
    model: str,
    reasoning_effort: Optional[str],
) -> Optional[float]:
    """Temperature actually sent on the wire, if any (not reasoning provenance)."""
    if provider_id != "openai":
        return DEFAULT_LLM_TEMPERATURE
    policy = openai_model_policy(model)
    effort = resolve_reasoning_effort(policy, reasoning_effort)
    if effort is not None and effort != "none":
        return None
    if policy.send_temperature_when_reasoning_none:
        return DEFAULT_LLM_TEMPERATURE
    return None


def _parse_or_repair(
    llm: LLMProvider,
    content: str,
    *,
    model: str,
    max_output_tokens: int,
    usage_total: TokenUsage,
    on_progress: Optional[ProgressCallback],
    save_raw_on_error: Optional[str],
    reasoning_effort: Optional[str] = None,
    request_usages: Optional[list[tuple[int, int]]] = None,
    progress_unit: Optional[str] = None,
    raw_slug: Optional[str] = None,
) -> tuple[dict, TokenUsage, str, list[tuple[int, int]], int]:
    """Extract findings JSON; optionally one repair completion.

    Returns ``(..., repair_count)`` where ``repair_count`` is 0 or 1.
    """
    usages = list(request_usages or [])
    first_error: FindingParseError | None = None
    try:
        return extract_json_object(content), usage_total, content, usages, 0
    except FindingParseError as exc:
        if not exc.repairable:
            _persist_raw_or_notice(
                save_raw_on_error,
                exc.raw or content,
                on_progress,
                slug=raw_slug,
            )
            raise
        first_error = exc
    if progress_unit:
        _progress(on_progress, f"repairing JSON for {progress_unit}")
    else:
        _progress(on_progress, "repairing JSON")
    repair = build_json_repair_prompt(content)
    repaired = None
    repair_call_failed = False
    try:
        repaired = _complete_checked(
            llm,
            [
                ChatMessage(role="system", content=repair.system),
                ChatMessage(role="user", content=repair.user),
            ],
            model=model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            save_raw_on_error=save_raw_on_error,
            on_progress=on_progress,
            raw_slug=raw_slug,
        )
    except RecoveryBudgetExhausted:
        raise
    except ModelOutputTruncatedError:
        raise
    except Exception:  # noqa: BLE001 - drop provider objects/text at this boundary
        repair_call_failed = True
    if repair_call_failed:
        # Do not re-raise contaminated provider exceptions into the CLI.
        raise ProviderRequestError("JSON repair request failed") from None
    assert repaired is not None
    usage_total = _add_usage(usage_total, repaired.usage)
    usages.append((repaired.usage.input_tokens, repaired.usage.output_tokens))
    second_error: FindingParseError | None = None
    try:
        return (
            extract_json_object(repaired.content),
            usage_total,
            repaired.content,
            usages,
            1,
        )
    except FindingParseError as exc:
        second_error = exc
    hint = (
        "Model output was not valid findings JSON (often truncation or "
        "extra commentary). Try --max-output-tokens 16384."
    )
    base = (
        public_error_text(second_error)
        or "Could not parse JSON object from model response"
    )
    parse_error = FindingParseError(
        f"{base}. {hint}",
        raw=(second_error.raw or content),
    )
    _persist_raw_or_notice(
        save_raw_on_error, content, on_progress, slug=raw_slug
    )
    raise parse_error
