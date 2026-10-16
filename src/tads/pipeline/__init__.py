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
    FindingParseError,
    extract_json_object,
    parse_findings_payload,
)
from tads.pipeline.validate import enrich_finding_validation, verify_finding_source
from tads.privacy import DEFAULT_POLICY, PrivacyPolicy
from tads.prompts import (
    PROMPT_FRAMEWORK_VERSION,
    build_json_repair_prompt,
    build_section_chunk_prompt,
    build_whole_document_prompt,
)
from tads.schemas.cost import CostBudget, CostEstimate, TokenUsage
from tads.schemas.findings import Finding
from tads.schemas.report import (
    AnalysisMode,
    DocumentIdentity,
    PrivacyMode,
    Report,
    RequestBatching,
    RunMetadata,
)

RAW_ON_ERROR_MAX_BYTES = 256 * 1024
RAW_ON_ERROR_TRUNCATION_MARKER = "\n...[truncated]...\n"
RAW_SAVE_FAILED_NOTICE = "Raw diagnostic output could not be saved."
_RAW_TMP_PREFIX = ".tads-raw-"

ProgressCallback = Callable[[str], None]


class UnreliableSectionizationError(RuntimeError):
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
        next_id = 1
        total_chunks = len(plan.chunks)
        for chunk in plan.chunks:
            _progress(
                on_progress,
                f"analyzing chunk {chunk.chunk_index + 1}/{total_chunks} "
                f"({chunk.section_count} section(s))",
            )
            opaque_refs = [b.opaque_ref for b in chunk.bindings]
            sections = [b.section for b in chunk.bindings]
            bundle = build_section_chunk_prompt(
                plan.document,
                opaque_refs=opaque_refs,
                sections=sections,
                corpus_notes=corpus_notes,
                document_summary=summary,
            )
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
            batch = _parse_items_or_raw(
                payload,
                source_text,
                save_raw_on_error=save_raw_on_error,
                on_progress=on_progress,
                id_start=next_id,
                section_bindings=chunk.allowlist(),
                document_text=plan.document.text,
            )
            findings.extend(batch)
            next_id += len(batch)

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
            scope_notes=list(plan.scoped.notes),
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
            chunk_section_counts=list(plan.chunk_section_counts),
            coverage_complete=_eligible_coverage_complete(plan.scoped),
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
):
    """Run complete(); persist raw on truncation via the opt-in path only."""
    try:
        return llm.complete(
            messages,
            model=model,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
    except ModelOutputTruncatedError as exc:
        _persist_raw_or_notice(save_raw_on_error, exc.raw, on_progress)
        raise


def _progress(callback: Optional[ProgressCallback], message: str) -> None:
    if callback:
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
        _persist_raw_or_notice(save_raw_on_error, persist, on_progress)
        raise parse_error
    raise FindingParseError("Finding item validation failed")


def _persist_raw_or_notice(
    path: Optional[str],
    content: str,
    on_progress: Optional[ProgressCallback],
) -> None:
    if not path:
        return
    if not _save_raw_on_error(path, content):
        _progress(on_progress, RAW_SAVE_FAILED_NOTICE)
        return
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
            _persist_raw_or_notice(save_raw_on_error, exc.raw or content, on_progress)
            raise
        first_error = exc
    _progress(on_progress, "repairing JSON")
    repair = build_json_repair_prompt(content)
    provider_error: BaseException | None = None
    repaired = None
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
        )
    except ModelOutputTruncatedError:
        raise
    except Exception as exc:  # noqa: BLE001
        provider_error = exc
    if provider_error is not None:
        raise provider_error
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
    parse_error = FindingParseError(
        f"{second_error}. {hint}",
        raw=(getattr(second_error, "raw", "") or content),
    )
    _persist_raw_or_notice(save_raw_on_error, content, on_progress)
    raise parse_error
