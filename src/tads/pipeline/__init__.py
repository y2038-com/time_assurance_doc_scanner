# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Pipeline orchestration: plan and execute scans."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from tads import __version__
from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import detect_corpus, get_adapter
from tads.cost import assert_within_budget, estimate_cost_usd
from tads.llm.base import DEFAULT_LLM_TEMPERATURE, ChatMessage, LLMProvider
from tads.llm.registry import get_provider
from tads.parsing.clauses import assess_clause_parse_health
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.scope import AnalysisScope, ScopedDocument, apply_analysis_scope
from tads.parsing.sections import choose_analysis_mode
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
    window = context_token_budget or llm.context_window_tokens(model_id)
    mode = force_mode or choose_analysis_mode(
        document,
        context_token_budget=window,
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
    notes = list(scoped.notes) + health_notes + notes
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
    )
    if enforce_budget:
        assert_within_budget(plan.cost_estimate)

    llm = get_provider(plan.provider_id)
    if not llm.is_configured():
        from tads.llm.base import ProviderNotConfiguredError

        raise ProviderNotConfiguredError(
            f"Provider '{plan.provider_id}' is not configured."
        )

    adapter = get_adapter(plan.document.corpus)
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
        payload, usage_total, source_text = _parse_or_repair(
            llm,
            response.content,
            model=plan.model,
            max_output_tokens=max_output_tokens,
            usage_total=usage_total,
            on_progress=on_progress,
            save_raw_on_error=save_raw_on_error,
        )
        findings = _parse_items_or_raw(
            payload,
            source_text,
            save_raw_on_error=save_raw_on_error,
            on_progress=on_progress,
        )
    else:
        summary = _scoped_summary(plan.document, plan.scoped.sections)
        next_id = 1
        for index, section in enumerate(plan.scoped.sections, start=1):
            _progress(
                on_progress,
                f"analyzing section {index}/{len(plan.scoped.sections)}",
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
            payload, usage_total, source_text = _parse_or_repair(
                llm,
                response.content,
                model=plan.model,
                max_output_tokens=max_output_tokens,
                usage_total=usage_total,
                on_progress=on_progress,
                save_raw_on_error=save_raw_on_error,
            )
            batch = _parse_items_or_raw(
                payload,
                source_text,
                save_raw_on_error=save_raw_on_error,
                on_progress=on_progress,
                id_start=next_id,
                default_section_id=section.id,
                default_section_title=section.title,
            )
            findings.extend(batch)
            next_id += len(batch)

    findings = [enrich_finding_validation(f) for f in findings]
    findings = [verify_finding_source(f, plan.scoped.text) for f in findings]
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
            temperature=DEFAULT_LLM_TEMPERATURE,
            max_output_tokens=max_output_tokens,
        ),
        cost_estimate=plan.cost_estimate,
        actual_usage=usage_total,
        actual_cost_usd=actual_cost,
        findings=findings,
    )
    _progress(on_progress, f"done: {len(findings)} candidates")
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
) -> list[Finding]:
    parse_error: FindingParseError | None = None
    try:
        return parse_findings_payload(
            payload,
            id_start=id_start,
            default_section_id=default_section_id,
            default_section_title=default_section_title,
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


def _parse_or_repair(
    llm: LLMProvider,
    content: str,
    *,
    model: str,
    max_output_tokens: int,
    usage_total: TokenUsage,
    on_progress: Optional[ProgressCallback],
    save_raw_on_error: Optional[str],
) -> tuple[dict, TokenUsage, str]:
    first_error: FindingParseError | None = None
    try:
        return extract_json_object(content), usage_total, content
    except FindingParseError as exc:
        first_error = exc
    _progress(on_progress, "repairing JSON")
    repair = build_json_repair_prompt(content)
    provider_error: BaseException | None = None
    repaired = None
    try:
        repaired = llm.complete(
            [
                ChatMessage(role="system", content=repair.system),
                ChatMessage(role="user", content=repair.user),
            ],
            model=model,
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        provider_error = exc
    if provider_error is not None:
        raise provider_error
    assert repaired is not None
    usage_total = _add_usage(usage_total, repaired.usage)
    second_error: FindingParseError | None = None
    try:
        return extract_json_object(repaired.content), usage_total, repaired.content
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
