# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Report exporters (JSON canonical, Markdown projection)."""

from __future__ import annotations

from pathlib import Path

from tads.schemas.assurance import (
    AssuranceStatus,
    assurance_status_label,
    assurance_status_sort_key,
    derive_assurance_status,
)
from tads.schemas.report import Report


def report_to_json(report: Report, *, indent: int = 2) -> str:
    return report.model_dump_json(indent=indent)


def write_report_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_json(report) + "\n", encoding="utf-8")


def load_report_json(path: Path) -> Report:
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def report_to_markdown(report: Report) -> str:
    doc = report.document
    run = report.run
    lines: list[str] = [
        f"# Time Assurance Scan Report: {doc.doc_id}",
        "",
        f"**Title:** {doc.title or '(unknown)'}  ",
        f"**Corpus:** {doc.corpus}  ",
        f"**Source:** {doc.source_path or doc.source_uri or '(n/a)'}  ",
        f"**Scanner:** {run.scanner_version}  ",
        f"**Provider/model:** {run.provider or '?'} / {run.model or '?'}  ",
        f"**Analysis mode:** {run.analysis_mode.value if run.analysis_mode else '?'}  ",
        f"**Privacy mode:** {run.privacy_mode.value}  ",
        f"**Started (UTC):** {run.started_at.isoformat()}  ",
    ]
    if run.completed_at:
        lines.append(f"**Completed (UTC):** {run.completed_at.isoformat()}  ")
    if report.cost_estimate:
        est = report.cost_estimate
        cost = (
            f"${est.estimated_cost_usd:.4f}"
            if est.estimated_cost_usd is not None
            else "unknown"
        )
        lines.append(
            f"**Preflight estimate:** {est.estimated_total_tokens} tokens, {cost} USD  "
        )
    if report.actual_usage:
        lines.append(
            f"**Actual usage:** {report.actual_usage.total_tokens} tokens "
            f"(in={report.actual_usage.input_tokens}, out={report.actual_usage.output_tokens})  "
        )
    if report.actual_cost_usd is not None:
        lines.append(f"**Actual cost (est.):** ${report.actual_cost_usd:.4f} USD  ")

    lines.extend(
        [
            "",
            "## Assurance notice",
            "",
            "Items below are **machine-generated candidates for review** unless "
            "marked otherwise. The phrase **validated finding** is reserved for "
            "items with disposition `accepted` (human-confirmed). "
            "Source quote matches produce a **source-verified candidate**; "
            "automated deterministic checks produce a **deterministically checked "
            "candidate** — neither is a validated finding.",
            "",
            "## Summary",
            "",
        ]
    )
    lines.append(f"Candidates: **{len(report.findings)}**")
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

    lines.extend(
        [
            "",
            "## Human review",
            "",
            "Edit dispositions in the JSON report (`accepted` → human-confirmed / "
            "validated finding; `rejected`; `needs_review` → deferred; `edited`), "
            "then run `tads render <report.json>` to refresh this Markdown view.",
            "",
            "## Candidates for review",
            "",
        ]
    )

    if not report.findings:
        lines.append("_No candidates reported._")
        lines.append("")
        return "\n".join(lines)

    for finding in report.findings:
        status = derive_assurance_status(finding)
        label = assurance_status_label(status)
        loc = finding.location
        loc_txt = ""
        if loc and (loc.section_id or loc.section_title):
            loc_txt = f" — {loc.section_id or ''} {loc.section_title or ''}".rstrip()
        lines.append(f"### {finding.id}: {finding.title}{loc_txt}")
        lines.append("")
        lines.append(f"- **Assurance status:** {label} (`{status.value}`)")
        lines.append(f"- **Type:** `{finding.finding_type.value}`")
        lines.append(f"- **Severity:** `{finding.severity.value}`")
        lines.append(f"- **Confidence:** `{finding.confidence.value}`")
        lines.append(f"- **Disposition:** `{finding.disposition.value}`")
        if finding.domains:
            lines.append(
                "- **Domains:** "
                + ", ".join(f"`{d.value}`" for d in finding.domains)
            )
        lines.append(
            f"- **Source verified:** "
            f"{'yes' if finding.source_verified else 'no'}"
        )
        if finding.source_verification_detail:
            lines.append(
                f"- **Source verification detail:** {finding.source_verification_detail}"
            )
        lines.append(
            f"- **Deterministic check:** `{finding.validation_status.value}`"
            " (JSON field; `verified` = deterministically checked, not human-validated)"
        )
        if finding.validation_detail:
            lines.append(f"- **Deterministic check detail:** {finding.validation_detail}")
        lines.append("")
        lines.append(finding.description)
        lines.append("")
        if finding.machine_interpretation:
            lines.append(f"**Interpretation:** {finding.machine_interpretation}")
            lines.append("")
        if finding.recommendation_level1:
            lines.append(f"**Level-1 recommendation:** {finding.recommendation_level1}")
            lines.append("")
        _append_horizon_validation_section(lines, finding)
        if finding.evidence:
            lines.append("**Evidence:**")
            lines.append("")
            for ev in finding.evidence:
                quote = ev.quote.replace("\n", " ").strip()
                lines.append(f"> {quote}")
                if ev.note:
                    lines.append(f">")
                    lines.append(f"> _{ev.note}_")
                lines.append("")
        if finding.reviewer_notes:
            lines.append(f"**Reviewer notes:** {finding.reviewer_notes}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_instant(value) -> str:
    if value is None:
        return "n/a"
    text = value.isoformat()
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        # Prefer trailing Z for UTC
        if text.endswith("+00:00"):
            return text[:-6] + "Z"
    return text


def _append_horizon_validation_section(lines: list[str], finding) -> None:
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

    status_label = hv.status.replace("_", " ").capitalize()
    lines.append(f"Status: **{status_label}** (`{hv.status}`)")
    lines.append("")
    lines.append(
        "Arithmetic verification only — does **not** confirm a standards defect "
        "or change human disposition."
    )
    lines.append("")
    if params is not None:
        if params.width_bits is not None:
            lines.append(f"- Width: {params.width_bits} bits")
        if params.signed is not None:
            lines.append(
                f"- Signedness: {'Signed' if params.signed else 'Unsigned'}"
            )
        if params.epoch is not None:
            lines.append(f"- Epoch: {_format_instant(params.epoch)}")
        if params.unit is not None:
            unit_line = f"- Unit: {params.unit}"
            if params.ticks_per_second is not None:
                unit_line += f" ({params.ticks_per_second:g} ticks/second)"
            lines.append(unit_line)
        if params.rollover_behavior:
            lines.append(f"- Rollover behavior (stated): {params.rollover_behavior}")
    if hv.minimum_value is not None:
        lines.append(f"- Minimum value: {hv.minimum_value:,}")
    if hv.maximum_value is not None:
        lines.append(f"- Maximum value: {hv.maximum_value:,}")
    if hv.earliest_representable is not None:
        lines.append(
            f"- Earliest representable instant: "
            f"{_format_instant(hv.earliest_representable)}"
        )
    if hv.last_representable is not None:
        lines.append(
            f"- Last representable instant: "
            f"{_format_instant(hv.last_representable)}"
        )
    if hv.first_out_of_range is not None:
        lines.append(
            f"- First wrapped/out-of-range instant: "
            f"{_format_instant(hv.first_out_of_range)}"
        )
    if hv.claimed_horizon is not None:
        lines.append(f"- Model-stated horizon: {_format_instant(hv.claimed_horizon)}")
    if hv.claim_consistent is True:
        lines.append("- Result: **Consistent** with deterministic calculation")
    elif hv.claim_consistent is False:
        lines.append("- Result: **Inconsistent** with deterministic calculation")
    elif hv.status in {"insufficient_parameters", "unsupported", "not_applicable"}:
        reason = hv.notes[0] if hv.notes else "required parameters unavailable"
        lines.append(f"- Result: validation not completed ({reason})")
    for note in hv.notes[:3]:
        lines.append(f"- Note: {note}")
    lines.append("")


def write_report_markdown(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_markdown(report), encoding="utf-8")
