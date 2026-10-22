# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Terminal output verbosity policy and plan presentation.

Display-only. Does not change planning, provider payloads, estimates, reports,
or scan semantics. ``CostEstimate.notes`` stay as stored for public JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Optional, Sequence

from tads.cost import estimate_request_cost_usd, lookup_pricing
from tads.errors import PublicScanError
from tads.llm.openai_models import (
    GPT_6_LUNA,
    InvalidReasoningEffortError,
    openai_model_policy,
    recorded_reasoning_effort,
)
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS
from tads.schemas.report import AnalysisMode, RequestBatching


class OutputLevel(IntEnum):
    """Ordered terminal verbosity: quiet < normal < verbose < debug."""

    QUIET = 10
    NORMAL = 20
    VERBOSE = 30
    DEBUG = 40


class InvalidOutputLevelError(PublicScanError, ValueError):
    """Conflicting ``--quiet`` / ``--verbose`` / ``--debug`` flags."""


@dataclass(frozen=True)
class OutputPolicy:
    """Single policy object consulted by plan printing and progress."""

    level: OutputLevel = OutputLevel.NORMAL

    def allows(self, minimum: OutputLevel) -> bool:
        return self.level >= minimum

    def emit_progress(self, message: str) -> bool:
        return should_emit_progress(message, self.level)


@dataclass(frozen=True)
class RequestView:
    """Structured per-request planning facts for display (not a report field)."""

    index: int
    input_tokens: int
    output_tokens: int
    section_count: int
    section_ids: tuple[str, ...] = ()
    opaque_refs: tuple[str, ...] = ()
    start_char: int = 0
    end_char: int = 0


@dataclass(frozen=True)
class PricingTier:
    key: str
    label: str
    known: bool


@dataclass(frozen=True)
class PlanPresentation:
    """Display-layer summary derived from a ``ScanPlan``."""

    identity_lines: tuple[str, ...]
    scope_lines: tuple[str, ...]
    request_lines: tuple[str, ...]
    warning_notes: tuple[str, ...]
    normal_notes: tuple[str, ...]
    verbose_lines: tuple[str, ...]
    debug_lines: tuple[str, ...]


def resolve_output_level(
    *,
    quiet: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> OutputLevel:
    """Map CLI flags to one level. Conflicting combinations are invalid."""
    selected = [quiet, verbose, debug]
    if sum(1 for flag in selected if flag) > 1:
        raise InvalidOutputLevelError(
            "Conflicting output options: use only one of --quiet, --verbose, "
            "or --debug"
        )
    if debug:
        return OutputLevel.DEBUG
    if verbose:
        return OutputLevel.VERBOSE
    if quiet:
        return OutputLevel.QUIET
    return OutputLevel.NORMAL


def classify_pricing_tier(
    provider: str, model: str, input_tokens: int
) -> PricingTier:
    """Controlled pricing category from provider/model policy, not note text."""
    provider_key = provider.lower()
    model_key = model.lower()
    if provider_key == "openai" and model_key == GPT_6_LUNA:
        policy = openai_model_policy(GPT_6_LUNA)
        threshold = (
            policy.pricing.long_context_input_threshold if policy.pricing else None
        )
        if threshold is not None and input_tokens > threshold:
            return PricingTier(
                key="luna_long",
                label="GPT-6 Luna long-context rates",
                known=True,
            )
        return PricingTier(
            key="luna_short",
            label="GPT-6 Luna short-context rates",
            known=True,
        )
    if lookup_pricing(provider, model) is None:
        return PricingTier(
            key="unknown",
            label=f"no table entry for {provider}:{model}",
            known=False,
        )
    return PricingTier(
        key="table",
        label="indicative table rates",
        known=True,
    )


def request_views_from_plan(plan) -> tuple[RequestView, ...]:
    """Build per-request views from structured chunk data."""
    chunks = tuple(getattr(plan, "chunks", ()) or ())
    if chunks:
        views: list[RequestView] = []
        for chunk in chunks:
            bindings = tuple(chunk.bindings)
            start = bindings[0].section.start_char if bindings else 0
            end = bindings[-1].section.end_char if bindings else 0
            views.append(
                RequestView(
                    index=chunk.chunk_index + 1,
                    input_tokens=int(chunk.estimated_input_tokens),
                    output_tokens=int(chunk.estimated_output_tokens),
                    section_count=chunk.section_count,
                    section_ids=tuple(b.canonical_id for b in bindings),
                    opaque_refs=tuple(b.opaque_ref for b in bindings),
                    start_char=start,
                    end_char=end,
                )
            )
        return tuple(views)
    est = plan.cost_estimate
    sections = tuple(getattr(plan.scoped, "sections", ()) or ())
    start = sections[0].start_char if sections else 0
    end = sections[-1].end_char if sections else 0
    if plan.analysis_mode == AnalysisMode.SECTION_AWARE and not sections:
        return ()
    return (
        RequestView(
            index=1,
            input_tokens=int(est.estimated_input_tokens),
            output_tokens=int(est.estimated_output_tokens),
            section_count=plan.section_count or max(1, len(sections)),
            section_ids=tuple(s.id for s in sections),
            start_char=start,
            end_char=end,
        ),
    )


def format_pricing_line(
    provider: str, model: str, views: Sequence[RequestView]
) -> str:
    """Summarize pricing tiers with counts; unknown pricing stays prominent."""
    if not views:
        tier = classify_pricing_tier(provider, model, 0)
        if not tier.known:
            return f"pricing: USD estimate unavailable ({tier.label})"
        return f"pricing: no primary requests ({tier.label})"
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    unknown: PricingTier | None = None
    for view in views:
        tier = classify_pricing_tier(provider, model, view.input_tokens)
        if not tier.known:
            unknown = tier
            break
        counts[tier.key] = counts.get(tier.key, 0) + 1
        labels[tier.key] = tier.label
    if unknown is not None:
        return f"pricing: USD estimate unavailable ({unknown.label})"
    parts = [
        (
            f"{counts[key]} requests use {labels[key]}"
            if counts[key] != 1
            else f"1 request uses {labels[key]}"
        )
        for key in counts
    ]
    return "pricing: " + "; ".join(parts)


def _comma(n: int) -> str:
    return f"{n:,}"


def _is_luna(provider: str, model: str) -> bool:
    return provider.lower() == "openai" and model.lower() == GPT_6_LUNA


def _effective_reasoning_effort(plan, requested: Optional[str]) -> Optional[str]:
    try:
        return recorded_reasoning_effort(
            provider_id=plan.provider_id,
            model=plan.model,
            requested=requested,
        )
    except InvalidReasoningEffortError:
        return None


def _reasoning_estimate_is_lower_bound(plan, requested: Optional[str]) -> bool:
    """True only when model policy plus effective effort make the USD estimate a lower bound.

    GPT-6 Luna with effective effort ``none`` does not; non-reasoning models
    never do. Decision uses ``openai_model_policy`` / ``recorded_reasoning_effort``,
    not stored note text.
    """
    provider_key = (plan.provider_id or "").strip().lower()
    if provider_key != "openai":
        return False
    policy = openai_model_policy(plan.model)
    if not policy.supports_reasoning_effort:
        return False
    effort = _effective_reasoning_effort(plan, requested)
    return effort is not None and effort != "none"


def _scope_warning_notes(notes: Iterable[str]) -> list[str]:
    warnings: list[str] = []
    for note in notes:
        if _is_scope_warning(note):
            warnings.append(note)
    return warnings


def _is_scope_warning(note: str) -> bool:
    text = note.strip()
    return (
        text.startswith("Capped to max_sections=")
        or text.startswith("Trimmed first section")
        or text.startswith("Stopped at max_chars=")
        or text.startswith("Stopped at max_input_tokens=")
        or text.startswith("Analysis scope is empty")
        or text.startswith("Clause parse health:")
        or text.startswith("Using whole-document analysis because clause")
        or text.startswith("Section-aware mode forced despite unreliable")
    )


def _is_routine_scope_note(note: str) -> bool:
    text = note.strip()
    return (
        text.startswith("Skipped ")
        or text.startswith("No separate front-matter")
        or text.startswith("No Index/Acknowledgments")
    )


def _budget_warning_notes(notes: Sequence[str]) -> list[str]:
    found: list[str] = []
    for note in notes:
        if (
            note.startswith("Estimated tokens ")
            or note.startswith("Estimated cost $")
            or note.startswith("Cost cap set but USD estimate unavailable")
        ):
            found.append(note)
    return found


def _has_max_cost_usd(notes: Sequence[str]) -> bool:
    return any("--max-cost-usd" in note for note in notes)


def build_plan_presentation(
    plan,
    *,
    ingested=None,
    corpus: str,
    requested_reasoning_effort: Optional[str] = None,
) -> PlanPresentation:
    """Derive display lines from structured plan data (not stored notes)."""
    from tads.ingest.url_security import redact_url
    from tads.llm.registry import get_provider

    scoped = plan.scoped
    est = plan.cost_estimate
    views = request_views_from_plan(plan)
    source_uri = redact_url(plan.document.source_uri) if plan.document.source_uri else None
    retrieved = (
        redact_url(plan.document.retrieved_uri) if plan.document.retrieved_uri else None
    )

    identity: list[str] = [
        f"[bold]{plan.document.doc_id}[/bold] — {plan.document.title or ''} "
        f"[dim](corpus={corpus})[/dim]"
    ]
    if source_uri:
        identity.append(f"source_uri: {source_uri}")
    if retrieved and retrieved != source_uri:
        identity.append(f"retrieved_uri: {retrieved}")
    if plan.document.source_path:
        identity.append(f"source_path: {plan.document.source_path}")
    if ingested is not None:
        member = f" member={ingested.member_name}" if ingested.member_name else ""
        identity.append(
            f"ingest: {ingested.media_type} via {ingested.converter} "
            f"({ingested.bytes_fetched:,} bytes){member}"
        )
        for note in ingested.notes:
            identity.append(f"  • {note}")

    skip_bits = [
        f"{scoped.skipped_front_matter_sections} front-matter/TOC",
        f"{scoped.skipped_index_ack_sections} Index/Acknowledgments",
    ]
    coverage_sections = _pct(len(scoped.sections), scoped.eligible_sections)
    coverage_chars = _pct(scoped.analyzed_chars, scoped.eligible_chars)
    coverage_doc = _pct(scoped.analyzed_chars, scoped.document_chars)
    truncated = " [truncated]" if scoped.truncated else ""
    scope_lines = [
        "document: "
        f"{scoped.total_sections_before} sections, "
        f"{scoped.document_chars:,} chars, "
        f"~{scoped.document_tokens:,} tokens",
        "eligible: "
        f"{scoped.eligible_sections} sections "
        f"(after skipping {', '.join(skip_bits)}), "
        f"{scoped.eligible_chars:,} chars, "
        f"~{scoped.eligible_tokens:,} tokens",
        "analyzed: "
        f"{plan.section_count} sections, "
        f"{scoped.analyzed_chars:,} chars, "
        f"~{scoped.analyzed_tokens:,} tokens{truncated}",
        "coverage: "
        f"sections {coverage_sections:.1f}% of eligible, "
        f"chars {coverage_chars:.1f}% of eligible "
        f"({coverage_doc:.1f}% of full document)",
        f"analysis_mode: {plan.analysis_mode.value}",
        f"request_batching: {plan.request_batching.value}",
    ]

    request_lines = [_requests_summary_line(plan, views)]
    request_lines.append(f"provider/model: {plan.provider_id} / {plan.model}")
    effort = _effective_reasoning_effort(plan, requested_reasoning_effort)
    if effort is not None:
        request_lines.append(f"reasoning_effort: {effort}")
    cost = (
        f"${est.estimated_cost_usd:.4f}"
        if est.estimated_cost_usd is not None
        else "unknown"
    )
    request_lines.append(
        f"estimate: {est.estimated_total_tokens} tokens, {cost} USD "
        f"(within_budget={est.within_budget})"
    )
    request_lines.append(format_pricing_line(plan.provider_id, plan.model, views))
    if views:
        inputs = [v.input_tokens for v in views]
        lo, hi = min(inputs), max(inputs)
        if lo == hi:
            request_lines.append(f"estimated input/request: {_comma(lo)} tokens")
        else:
            request_lines.append(
                f"estimated input/request: {_comma(lo)} to {_comma(hi)} tokens"
            )
    request_lines.append(
        "estimate basis: includes "
        f"{_comma(REQUEST_OVERHEAD_TOKENS)} shared input tokens per request"
    )

    warning_notes: list[str] = []
    warning_notes.extend(_scope_warning_notes(scoped.notes))
    warning_notes.extend(_budget_warning_notes(est.notes))
    if not est.within_budget:
        if not any(n.startswith("Estimated ") for n in warning_notes):
            warning_notes.append("Scan estimate exceeds configured budget.")

    normal_notes: list[str] = []
    if _is_luna(plan.provider_id, plan.model):
        normal_notes.append("Cached-input discounts are not modeled.")
    if _reasoning_estimate_is_lower_bound(plan, requested_reasoning_effort):
        lower_bound = (
            "Reasoning tokens are billed as output; this estimate is a lower bound."
        )
        warning_notes.append(lower_bound)
        normal_notes.append(lower_bound)
    if plan.analysis_mode == AnalysisMode.SECTION_AWARE:
        cost_contingency = (
            "Repair, correction, and adaptive-recovery requests may add "
            "unestimated cost."
        )
        warning_notes.append(cost_contingency)
        normal_notes.append(cost_contingency)
    if _has_max_cost_usd(est.notes):
        billing_cap = (
            "--max-cost-usd is a preflight estimate guard, not a hard runtime "
            "billing cap."
        )
        warning_notes.append(billing_cap)
        normal_notes.append(billing_cap)

    verbose_lines: list[str] = []
    verbose_lines.append(
        f"packing: section_chunking={plan.section_chunking}; "
        f"max_sections_per_request={plan.max_sections_per_request}; "
        f"sections per request: {list(plan.chunk_section_counts)}"
    )
    for note in scoped.notes:
        if _is_routine_scope_note(note) or _is_scope_warning(note):
            verbose_lines.append(note)
    if effort is not None:
        verbose_lines.append(f"Effective reasoning_effort for this plan: {effort}.")
    verbose_lines.append(
        f"Planning reserves completion-token headroom plus "
        f"{_comma(REQUEST_OVERHEAD_TOKENS)}-token shared request overhead "
        "inside the model context window."
    )
    for view in views:
        cost_usd, _ignored = estimate_request_cost_usd(
            provider=plan.provider_id,
            model=plan.model,
            input_tokens=view.input_tokens,
            output_tokens=view.output_tokens,
        )
        tier = classify_pricing_tier(
            plan.provider_id, plan.model, view.input_tokens
        )
        cost_s = f"${cost_usd:.4f}" if cost_usd is not None else "unknown"
        ids = ", ".join(view.section_ids) if view.section_ids else "(none)"
        verbose_lines.append(
            f"request {view.index}: {view.section_count} sections, "
            f"~{_comma(view.input_tokens)} input / "
            f"{_comma(view.output_tokens)} output tokens, "
            f"{tier.label}, {cost_s}"
        )
        verbose_lines.append(f"  sections: {ids}")

    debug_lines: list[str] = [
        f"shared_overhead_tokens={REQUEST_OVERHEAD_TOKENS}",
    ]
    try:
        llm = get_provider(plan.provider_id)
        native = bool(llm.supports_native_structured_output(model=plan.model))
    except Exception:  # noqa: BLE001 - display-only; never print the exception
        native = False
    debug_lines.append(
        f"structured_output native_json_schema={'yes' if native else 'no'}"
    )
    for view in views:
        if view.opaque_refs:
            first, last = view.opaque_refs[0], view.opaque_refs[-1]
            opaque = first if first == last else f"{first}–{last}"
            debug_lines.append(
                f"request {view.index} opaque={opaque} "
                f"chars={view.start_char}-{view.end_char}"
            )
        else:
            debug_lines.append(
                f"request {view.index} chars={view.start_char}-{view.end_char}"
            )

    # Deduplicate while preserving order.
    warning_notes = _unique(warning_notes)
    normal_notes = _unique(normal_notes)
    return PlanPresentation(
        identity_lines=tuple(identity),
        scope_lines=tuple(scope_lines),
        request_lines=tuple(request_lines),
        warning_notes=tuple(warning_notes),
        normal_notes=tuple(normal_notes),
        verbose_lines=tuple(verbose_lines),
        debug_lines=tuple(debug_lines),
    )


def format_plan_lines(
    plan,
    *,
    ingested=None,
    corpus: str,
    level: OutputLevel = OutputLevel.NORMAL,
    requested_reasoning_effort: Optional[str] = None,
) -> list[str]:
    """Human plan lines for the selected verbosity (JSON reports unchanged)."""
    pres = build_plan_presentation(
        plan,
        ingested=ingested,
        corpus=corpus,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    lines: list[str] = []
    if level >= OutputLevel.NORMAL:
        lines.extend(pres.identity_lines)
        lines.extend(pres.scope_lines)
        lines.extend(pres.request_lines)
    elif pres.warning_notes or any(
        line.startswith("pricing: USD estimate unavailable")
        for line in pres.request_lines
    ):
        for line in pres.request_lines:
            if (
                line.startswith("pricing:")
                or line.startswith("estimate:")
                or "within_budget=False" in line
            ):
                lines.append(line)
    notes: list[str] = []
    if level >= OutputLevel.NORMAL:
        notes.extend(pres.normal_notes)
        notes.extend(pres.warning_notes)
    else:
        notes.extend(pres.warning_notes)
    notes = _unique(notes)
    if notes:
        lines.append("notes:")
        for note in notes:
            lines.append(f"  - {note}")
    if level >= OutputLevel.VERBOSE:
        lines.append("verbose:")
        lines.extend(f"  {item}" for item in pres.verbose_lines)
    if level >= OutputLevel.DEBUG:
        lines.append("debug:")
        lines.extend(f"  {item}" for item in pres.debug_lines)
    return lines


def _requests_summary_line(plan, views: Sequence[RequestView]) -> str:
    n = len(views)
    if plan.analysis_mode != AnalysisMode.SECTION_AWARE:
        return "requests: 1 whole-document request"
    if plan.request_batching == RequestBatching.PER_SECTION or not plan.section_chunking:
        return f"requests: {n} primary requests, 1 section per request"
    return (
        f"requests: {n} section chunks, up to "
        f"{plan.max_sections_per_request} sections per request"
    )


def _pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 100.0 if part <= 0 else 0.0
    return min(100.0, 100.0 * part / whole)


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def is_plan_time_note(message: str) -> bool:
    """TADS-owned planning notes that the CLI already printed with the plan."""
    return _is_routine_scope_note(message) or _is_scope_warning(message)


def is_warning_progress(message: str) -> bool:
    """True for TADS-owned warning/status text that must never be hidden."""
    lowered = message.strip().casefold()
    if (
        "failed validation" in lowered
        or lowered.startswith("recovery reached")
        or lowered.startswith("scan completed with partial")
        or "raw diagnostic" in lowered
        or lowered.startswith("saved raw")
        or lowered.startswith("raw model output")
        or "within_budget=false" in lowered
        or "usd estimate unavailable" in lowered
        or "no pricing table" in lowered
        or "exceeds max_cost" in lowered
        or "exceeds max_tokens" in lowered
        or lowered.startswith("estimated cost $")
        or lowered.startswith("estimated tokens ")
        or lowered.startswith("cost cap set but usd")
        or lowered.startswith("analysis scope is empty")
    ):
        return True
    return _is_scope_warning(message)


def should_emit_progress(message: str, level: OutputLevel) -> bool:
    """Whether an untyped (normal) progress string should reach the terminal.

    Debug/verbose events are selected by ``ScanProgressReporter.emit_at`` /
    ``min_level``, not by parsing the message text.
    """
    if is_warning_progress(message):
        return True
    if level <= OutputLevel.QUIET:
        return False
    if _is_routine_scope_note(message):
        return False
    return True
