"""Report exporters (JSON canonical, Markdown projection)."""

from __future__ import annotations

import json
from pathlib import Path

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

    lines.extend(["", "## Summary", ""])
    lines.append(f"Findings: **{len(report.findings)}**")
    by_disp: dict[str, int] = {}
    for finding in report.findings:
        by_disp[finding.disposition.value] = by_disp.get(finding.disposition.value, 0) + 1
    if by_disp:
        lines.append("")
        lines.append("Dispositions: " + ", ".join(f"{k}={v}" for k, v in sorted(by_disp.items())))

    lines.extend(
        [
            "",
            "## Human review",
            "",
            "Edit dispositions in the JSON report (`accepted`, `rejected`, "
            "`needs_review`, `edited`), then run `tads render <report.json>` "
            "to refresh this Markdown view.",
            "",
            "## Findings",
            "",
        ]
    )

    if not report.findings:
        lines.append("_No findings reported._")
        lines.append("")
        return "\n".join(lines)

    for finding in report.findings:
        loc = finding.location
        loc_txt = ""
        if loc and (loc.section_id or loc.section_title):
            loc_txt = f" — {loc.section_id or ''} {loc.section_title or ''}".rstrip()
        lines.append(f"### {finding.id}: {finding.title}{loc_txt}")
        lines.append("")
        lines.append(f"- **Type:** `{finding.finding_type.value}`")
        lines.append(f"- **Severity:** `{finding.severity.value}`")
        lines.append(f"- **Confidence:** `{finding.confidence.value}`")
        lines.append(f"- **Disposition:** `{finding.disposition.value}`")
        if finding.domains:
            lines.append(
                "- **Domains:** "
                + ", ".join(f"`{d.value}`" for d in finding.domains)
            )
        lines.append(f"- **Validation:** `{finding.validation_status.value}`")
        if finding.validation_detail:
            lines.append(f"- **Validation detail:** {finding.validation_detail}")
        lines.append("")
        lines.append(finding.description)
        lines.append("")
        if finding.machine_interpretation:
            lines.append(f"**Interpretation:** {finding.machine_interpretation}")
            lines.append("")
        if finding.recommendation_level1:
            lines.append(f"**Level-1 recommendation:** {finding.recommendation_level1}")
            lines.append("")
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


def write_report_markdown(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_markdown(report), encoding="utf-8")
