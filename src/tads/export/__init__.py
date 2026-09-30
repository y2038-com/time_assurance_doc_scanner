# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Report exporters (JSON canonical, Markdown projection)."""

from __future__ import annotations

import json
from pathlib import Path

from tads.export.markdown import (
    escape_list_value,
    escape_paragraphs,
    escape_single_line,
    labeled_line,
    render_inline_code,
    render_list_line,
    render_literal_block,
)
from tads.ingest.url_security import redact_url
from tads.schemas.assurance import (
    AssuranceStatus,
    assurance_status_label,
    assurance_status_sort_key,
    derive_assurance_status,
)
from tads.schemas.findings import ScopeRelevance
from tads.schemas.horizon import HorizonPrecision, exact_comparable_moment
from tads.schemas.report import Report, RunMetadata

_KNOWN_HORIZON_STATUS = {
    "verified": "Verified",
    "contradicted": "Contradicted",
    "insufficient_parameters": "Insufficient parameters",
    "not_applicable": "Not applicable",
    "unsupported": "Unsupported",
    "ambiguous_signedness": "Ambiguous signedness",
    "error": "Error",
}


def report_to_json(report: Report, *, indent: int = 2) -> str:
    source_uri = (
        redact_url(report.document.source_uri) if report.document.source_uri else None
    )
    retrieved_uri = (
        redact_url(report.document.retrieved_uri)
        if report.document.retrieved_uri
        else None
    )
    if retrieved_uri == source_uri:
        retrieved_uri = None
    safe = report.model_copy(
        update={
            "document": report.document.model_copy(
                update={
                    "source_uri": source_uri,
                    "retrieved_uri": retrieved_uri,
                }
            )
        }
    )
    if safe.document.retrieved_uri is None:
        text = safe.model_dump_json(
            indent=indent, exclude={"document": {"retrieved_uri": True}}
        )
    else:
        text = safe.model_dump_json(indent=indent)
    if safe.schema_version in {"0.1.0", "0.2.0"}:
        payload = json.loads(text)
        _downgrade_legacy_claimed_horizons(payload)
        text = json.dumps(payload, indent=indent)
    return text


def _downgrade_legacy_claimed_horizons(payload: dict) -> None:
    """Keep schema 0.1.0/0.2.0 JSON in the original scalar claimed_horizon form."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        _downgrade_one_claimed_horizon(finding.get("time_representation"))
        horizon = finding.get("horizon_validation")
        _downgrade_one_claimed_horizon(horizon)
        if isinstance(horizon, dict):
            _downgrade_one_claimed_horizon(horizon.get("signed_interpretation"))
            _downgrade_one_claimed_horizon(horizon.get("unsigned_interpretation"))


def _downgrade_one_claimed_horizon(container) -> None:
    if not isinstance(container, dict):
        return
    claim = container.get("claimed_horizon")
    if isinstance(claim, dict) and "value" in claim:
        container["claimed_horizon"] = claim["value"]


def write_report_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_json(report) + "\n", encoding="utf-8")


def load_report_json(path: Path) -> Report:
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def eligible_coverage_percent(run: RunMetadata) -> float | None:
    """
    Percent of eligible text analyzed, clamped to [0, 100].

    Returns None when char totals are unavailable (e.g. older saved reports).
    """
    eligible = run.eligible_chars
    analyzed = run.analyzed_chars
    if eligible is None or analyzed is None:
        return None
    if eligible <= 0:
        return 100.0 if analyzed <= 0 else 0.0
    return min(100.0, 100.0 * analyzed / eligible)


def _analysis_coverage_header_lines(
    run: RunMetadata,
    *,
    coverage_pct: float | None,
) -> list[str]:
    """Prominent partial vs complete analysis notice for Markdown headers."""
    lines: list[str] = []
    if run.scope_truncated:
        lines.append("**Analysis scope:** PARTIAL  ")
    else:
        lines.append("**Analysis scope:** COMPLETE  ")

    if coverage_pct is not None:
        lines.append(
            f"**Coverage:** {coverage_pct:.1f}% of eligible text analyzed  "
        )
    elif run.scope_truncated:
        lines.append(
            "**Coverage:** truncated (eligible/analyzed char totals unavailable)  "
        )
    else:
        # Older reports without char totals: still state completeness from flag.
        lines.append(
            "**Coverage:** eligible text under current scope filters "
            "(char totals unavailable in this report)  "
        )

    detail_bits: list[str] = []
    if (
        run.sections_analyzed is not None
        and run.eligible_sections is not None
        and run.eligible_sections > 0
    ):
        detail_bits.append(
            f"sections {run.sections_analyzed}/{run.eligible_sections} eligible"
        )
    elif run.sections_analyzed is not None and run.sections_total is not None:
        detail_bits.append(
            f"sections analyzed {run.sections_analyzed} "
            f"(parsed total {run.sections_total})"
        )
    if run.analyzed_chars is not None and run.eligible_chars is not None:
        detail_bits.append(
            f"chars {run.analyzed_chars:,}/{run.eligible_chars:,} eligible"
        )
    if detail_bits:
        lines.append(f"**Scope detail:** {'; '.join(detail_bits)}  ")

    lines.append(
        "_Coverage is relative to eligible text after front-matter/Index skips "
        "and any caps — not a claim that every semantic aspect was assessed._  "
    )
    if run.scope_truncated and run.scope_notes:
        # Surface the first truncation-related note for clarity.
        for note in run.scope_notes:
            if any(
                key in note.lower()
                for key in ("capped", "trimmed", "stopped at", "max_")
            ):
                lines.append(
                    labeled_line("Scope note", escape_single_line(note))
                )
                break
    return lines


def report_to_markdown(report: Report) -> str:
    doc = report.document
    run = report.run
    coverage_pct = eligible_coverage_percent(run)
    title_text = (
        escape_single_line(doc.title) if doc.title else "(unknown)"
    )
    provider = render_inline_code(run.provider) if run.provider else "?"
    model = render_inline_code(run.model) if run.model else "?"
    analysis_mode = (
        render_inline_code(run.analysis_mode.value) if run.analysis_mode else "?"
    )

    lines: list[str] = [
        "# Time Assurance Scan Report",
        "",
        labeled_line("Document ID", render_inline_code(doc.doc_id)),
        labeled_line("Title", title_text),
        labeled_line("Corpus", render_inline_code(doc.corpus)),
    ]
    if doc.source_uri:
        lines.append(
            labeled_line(
                "Source URI", render_inline_code(redact_url(doc.source_uri))
            )
        )
    if doc.retrieved_uri:
        lines.append(
            labeled_line(
                "Retrieved URI",
                render_inline_code(redact_url(doc.retrieved_uri)),
            )
        )
    if doc.source_path:
        lines.append(
            labeled_line("Source path", render_inline_code(doc.source_path))
        )
    if doc.content_sha256:
        lines.append(
            labeled_line(
                "Content SHA-256", render_inline_code(doc.content_sha256)
            )
        )
    lines.extend(
        [
            labeled_line("Scanner", render_inline_code(run.scanner_version)),
            labeled_line(
                "Prompt framework",
                render_inline_code(run.prompt_framework_version),
            ),
            labeled_line("Provider/model", f"{provider} / {model}"),
            labeled_line("Analysis mode", analysis_mode),
        ]
    )
    lines.extend(_analysis_coverage_header_lines(run, coverage_pct=coverage_pct))
    lines.extend(
        [
            labeled_line(
                "Privacy mode", render_inline_code(run.privacy_mode.value)
            ),
            labeled_line(
                "Started (UTC)",
                escape_single_line(run.started_at.isoformat()),
            ),
        ]
    )
    if run.completed_at:
        lines.append(
            labeled_line(
                "Completed (UTC)",
                escape_single_line(run.completed_at.isoformat()),
            )
        )
    if report.cost_estimate:
        est = report.cost_estimate
        cost = (
            f"${est.estimated_cost_usd:.4f}"
            if est.estimated_cost_usd is not None
            else "unknown"
        )
        lines.append(
            labeled_line(
                "Preflight estimate",
                escape_single_line(
                    f"{est.estimated_total_tokens} tokens, {cost} USD"
                ),
            )
        )
    if report.actual_usage:
        lines.append(
            labeled_line(
                "Actual usage",
                escape_single_line(
                    f"{report.actual_usage.total_tokens} tokens "
                    f"(in={report.actual_usage.input_tokens}, "
                    f"out={report.actual_usage.output_tokens})"
                ),
            )
        )
    if report.actual_cost_usd is not None:
        lines.append(
            labeled_line(
                "Actual cost (est.)",
                escape_single_line(f"${report.actual_cost_usd:.4f} USD"),
            )
        )

    lines.extend(
        [
            "",
            "## Assurance notice",
            "",
            "TADS reports are decision-support artifacts. Items below are "
            "**machine-generated candidates for review** unless marked otherwise. "
            "Findings are not a certification that a document, protocol, "
            "implementation, or system is time-safe. Absence of findings does "
            "not establish absence of time-assurance risk.",
            "",
            "The phrase **validated finding** is reserved for items with "
            "disposition `accepted` (human-confirmed). A source quote match "
            "produces a **source-verified candidate** and does not validate "
            "interpretation. An automated deterministic check produces a "
            "**deterministically checked candidate** and is not a validated "
            "finding. A human-confirmed validated finding is still not "
            "certification of the document, protocol, implementation, or system.",
            "",
            "## Summary",
            "",
        ]
    )
    lines.append(f"Candidates (JSON): **{len(report.findings)}**")
    by_scope: dict[str, int] = {}
    for finding in report.findings:
        by_scope[finding.scope_relevance.value] = (
            by_scope.get(finding.scope_relevance.value, 0) + 1
        )
    scope_order = [
        ScopeRelevance.CORE.value,
        ScopeRelevance.SUPPORTING.value,
        ScopeRelevance.INCIDENTAL.value,
        ScopeRelevance.OUT_OF_SCOPE.value,
    ]
    if by_scope:
        lines.append(
            "Scope: "
            + ", ".join(
                f"{key}={by_scope.get(key, 0)}"
                for key in scope_order
                if by_scope.get(key, 0)
            )
        )
    by_status: dict[AssuranceStatus, int] = {}
    for finding in report.findings:
        status = derive_assurance_status(finding)
        by_status[status] = by_status.get(status, 0) + 1
    if by_status:
        lines.append("")
        parts = [
            f"{status.value}={count}"
            for status, count in sorted(
                by_status.items(), key=lambda item: assurance_status_sort_key(item[0])
            )
        ]
        lines.append("Assurance status: " + ", ".join(parts))
    by_disp: dict[str, int] = {}
    for finding in report.findings:
        by_disp[finding.disposition.value] = by_disp.get(finding.disposition.value, 0) + 1
    if by_disp:
        lines.append(
            "Dispositions (JSON): "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_disp.items()))
        )

    primary = [
        f
        for f in report.findings
        if f.scope_relevance in (ScopeRelevance.CORE, ScopeRelevance.SUPPORTING)
    ]
    incidental = [
        f for f in report.findings if f.scope_relevance == ScopeRelevance.INCIDENTAL
    ]
    # out_of_scope: kept in JSON only; omitted from Markdown by default

    lines.extend(
        [
            "",
            "## Human review",
            "",
            "Edit dispositions in the JSON report (`accepted` → human-confirmed / "
            "validated finding; `rejected`; `needs_review` → deferred; `edited`), "
            "then run `tads render <report.json>` to refresh this Markdown view. "
            "Scope (`scope_relevance`) is independent of disposition; "
            "`out_of_scope` candidates are omitted from Markdown but retained in JSON.",
            "",
            "## Candidates for review",
            "",
        ]
    )

    if not primary and not incidental:
        if report.findings:
            lines.append(
                "_No in-scope candidates to show "
                f"(JSON retains {len(report.findings)} item(s))._"
            )
        else:
            lines.append("_No candidates reported._")
        lines.append("")
        return "\n".join(lines)

    for finding in primary:
        _append_finding_section(lines, finding, schema_version=report.schema_version)

    if incidental:
        lines.extend(
            [
                "## Incidental observations",
                "",
                "Lower relevance to time assurance; retained for reviewer awareness.",
                "",
            ]
        )
        for finding in incidental:
            _append_finding_section(
                lines, finding, schema_version=report.schema_version
            )

    return "\n".join(lines)


def _append_finding_section(
    lines: list[str], finding, *, schema_version: str
) -> None:
    status = derive_assurance_status(finding)
    label = assurance_status_label(status)
    lines.append(f"### {render_inline_code(finding.id)}")
    lines.append("")
    lines.append(labeled_line("Title", escape_single_line(finding.title)))
    loc = finding.location
    if loc and loc.section_id:
        lines.append(
            labeled_line("Section ID", render_inline_code(loc.section_id))
        )
    if loc and loc.section_title:
        lines.append(
            labeled_line("Section title", escape_single_line(loc.section_title))
        )
    if finding.scope_relevance in (
        ScopeRelevance.SUPPORTING,
        ScopeRelevance.INCIDENTAL,
    ):
        lines.append(
            labeled_line(
                "Scope hint",
                render_inline_code(finding.scope_relevance.value),
            )
        )
    lines.append("")
    lines.append(
        render_list_line(
            "Assurance status",
            f"{escape_single_line(label)} ({render_inline_code(status.value)})",
        )
    )
    lines.append(
        render_list_line(
            "Scope", render_inline_code(finding.scope_relevance.value)
        )
    )
    if finding.scope_rationale:
        lines.append("")
        lines.append("**Scope rationale:**")
        lines.append("")
        lines.append(escape_paragraphs(finding.scope_rationale))
        lines.append("")
    lines.append(
        render_list_line("Type", render_inline_code(finding.finding_type.value))
    )
    lines.append(
        render_list_line("Severity", render_inline_code(finding.severity.value))
    )
    lines.append(
        render_list_line(
            "Confidence", render_inline_code(finding.confidence.value)
        )
    )
    lines.append(
        render_list_line(
            "Disposition", render_inline_code(finding.disposition.value)
        )
    )
    if finding.domains:
        lines.append(
            render_list_line(
                "Domains",
                ", ".join(render_inline_code(d.value) for d in finding.domains),
            )
        )
    lines.append(
        render_list_line(
            "Source verified",
            "yes" if finding.source_verified else "no",
        )
    )
    if finding.source_verification_detail:
        lines.append(
            render_list_line(
                "Source verification detail",
                escape_list_value(finding.source_verification_detail),
            )
        )
    lines.append(
        render_list_line(
            "Deterministic check",
            f"{render_inline_code(finding.validation_status.value)} "
            "(JSON field; `verified` = deterministically checked, not "
            "human-validated)",
        )
    )
    if finding.validation_detail:
        lines.append(
            render_list_line(
                "Deterministic check detail",
                escape_list_value(finding.validation_detail),
            )
        )
    lines.append("")
    if finding.description:
        lines.append(escape_paragraphs(finding.description))
        lines.append("")
    if finding.machine_interpretation:
        lines.append("**Interpretation:**")
        lines.append("")
        lines.append(escape_paragraphs(finding.machine_interpretation))
        lines.append("")
    if finding.recommendation_level1:
        lines.append("**Level-1 recommendation:**")
        lines.append("")
        lines.append(escape_paragraphs(finding.recommendation_level1))
        lines.append("")
    _append_horizon_validation_section(
        lines, finding, schema_version=schema_version
    )
    if finding.evidence:
        for ev in finding.evidence:
            lines.append("**Evidence:**")
            lines.append("")
            lines.append(render_literal_block(ev.quote))
            lines.append("")
            if ev.note:
                lines.append("**Evidence note:**")
                lines.append("")
                lines.append(escape_paragraphs(ev.note))
                lines.append("")
    if finding.reviewer_notes:
        lines.append("**Reviewer notes:**")
        lines.append("")
        lines.append(escape_paragraphs(finding.reviewer_notes))
        lines.append("")
    lines.append("---")
    lines.append("")


def _format_instant(value) -> str:
    if value is None:
        return "n/a"
    text = value.isoformat()
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        # Prefer trailing Z for UTC
        if text.endswith("+00:00"):
            return text[:-6] + "Z"
    return text


def _format_claimed_horizon(claim, *, schema_version: str) -> str:
    """Render a source-stated horizon without inventing extra precision."""
    if claim is None:
        return "n/a"
    if schema_version in {"0.1.0", "0.2.0"}:
        moment = exact_comparable_moment(claim)
        if moment is not None:
            return escape_single_line(_format_instant(moment))
        return escape_single_line(claim.value)
    if claim.precision == HorizonPrecision.INSTANT:
        moment = exact_comparable_moment(claim)
        if moment is not None:
            return escape_single_line(_format_instant(moment))
        return escape_single_line(claim.value)
    # TADS-authored precision suffix; value is a constrained lexical form.
    return f"{escape_single_line(claim.value)} ({claim.precision.value} precision)"


def _append_horizon_validation_section(
    lines: list[str], finding, *, schema_version: str
) -> None:
    hv = finding.horizon_validation
    params = finding.time_representation
    if hv is None and params is None:
        return

    lines.append("#### Deterministic validation")
    lines.append("")
    if hv is None:
        lines.append(
            "Structured `time_representation` is present but horizon validation "
            "was not run."
        )
        lines.append("")
        return

    status_key = hv.status
    known = _KNOWN_HORIZON_STATUS.get(status_key)
    if known is not None:
        status_line = (
            f"Status: **{known}** ({render_inline_code(status_key)})"
        )
    else:
        status_line = (
            f"Status: {escape_single_line(status_key)} "
            f"({render_inline_code(status_key)})"
        )
    lines.append(status_line)
    lines.append("")
    lines.append(
        "Arithmetic verification only — does **not** confirm a standards defect "
        "or change human disposition."
    )
    lines.append("")
    if params is not None:
        if params.width_bits is not None:
            lines.append(
                render_list_line(
                    "Width",
                    escape_single_line(f"{params.width_bits} bits"),
                )
            )
        if params.signed is not None:
            lines.append(
                render_list_line(
                    "Signedness",
                    "Signed" if params.signed else "Unsigned",
                )
            )
        elif hv.status == "ambiguous_signedness":
            lines.append(
                render_list_line(
                    "Signedness",
                    "unresolved (both interpretations below)",
                )
            )
        if params.epoch_kind is not None:
            lines.append(
                render_list_line(
                    "Epoch kind",
                    render_inline_code(params.epoch_kind.value),
                )
            )
        if params.epoch is not None:
            lines.append(
                render_list_line("Epoch", escape_single_line(_format_instant(params.epoch)))
            )
        if params.unit is not None:
            unit_value = render_inline_code(params.unit)
            if params.ticks_per_second is not None:
                unit_value += escape_single_line(
                    f" ({params.ticks_per_second:g} ticks/second)"
                )
            lines.append(render_list_line("Unit", unit_value))
        if params.rollover_behavior:
            lines.append(
                render_list_line(
                    "Rollover behavior (stated)",
                    escape_list_value(params.rollover_behavior),
                )
            )

    if hv.status == "ambiguous_signedness":
        for label, sub in (
            ("Signed interpretation", hv.signed_interpretation),
            ("Unsigned interpretation", hv.unsigned_interpretation),
        ):
            if sub is None:
                continue
            lines.append(f"- **{label}:**")
            if sub.minimum_value is not None and sub.maximum_value is not None:
                lines.append(
                    f"  - Range: {sub.minimum_value:,} … {sub.maximum_value:,}"
                )
            if sub.last_representable is not None:
                lines.append(
                    "  - Last representable: "
                    + escape_single_line(_format_instant(sub.last_representable))
                )
            if sub.first_out_of_range is not None:
                lines.append(
                    "  - First out-of-range: "
                    + escape_single_line(_format_instant(sub.first_out_of_range))
                )
    else:
        if hv.minimum_value is not None:
            lines.append(
                render_list_line("Minimum value", f"{hv.minimum_value:,}")
            )
        if hv.maximum_value is not None:
            lines.append(
                render_list_line("Maximum value", f"{hv.maximum_value:,}")
            )
        if hv.earliest_representable is not None:
            lines.append(
                render_list_line(
                    "Earliest representable instant",
                    escape_single_line(_format_instant(hv.earliest_representable)),
                )
            )
        if hv.last_representable is not None:
            lines.append(
                render_list_line(
                    "Last representable instant",
                    escape_single_line(_format_instant(hv.last_representable)),
                )
            )
        if hv.first_out_of_range is not None:
            lines.append(
                render_list_line(
                    "First wrapped/out-of-range instant",
                    escape_single_line(_format_instant(hv.first_out_of_range)),
                )
            )
    if hv.claimed_horizon is not None:
        horizon_label = (
            "Model-stated horizon"
            if schema_version in {"0.1.0", "0.2.0"}
            else "Source-stated horizon"
        )
        lines.append(
            render_list_line(
                horizon_label,
                _format_claimed_horizon(
                    hv.claimed_horizon, schema_version=schema_version
                ),
            )
        )
    if hv.claim_consistent is True:
        lines.append("- Result: **Consistent** with deterministic calculation")
    elif hv.claim_consistent is False:
        lines.append("- Result: **Inconsistent** with deterministic calculation")
    elif (
        hv.status != "insufficient_parameters"
        and schema_version not in {"0.1.0", "0.2.0"}
        and hv.claimed_horizon is not None
        and hv.claimed_horizon.precision
        in {HorizonPrecision.YEAR, HorizonPrecision.MONTH}
    ):
        lines.append(
            "- Result: not compared as an exact instant (partial source precision)"
        )
    elif hv.status in {
        "insufficient_parameters",
        "unsupported",
        "not_applicable",
        "ambiguous_signedness",
    }:
        reason = hv.notes[0] if hv.notes else "required parameters unavailable"
        if hv.notes:
            reason_txt = escape_single_line(reason)
        else:
            reason_txt = reason
        lines.append(
            render_list_line(
                "Result",
                f"validation not completed ({reason_txt})",
            )
        )
    for note in hv.notes[:3]:
        lines.append(render_list_line("Note", escape_list_value(note)))
    lines.append("")


def write_report_markdown(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_markdown(report), encoding="utf-8")
