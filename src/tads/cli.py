# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point for the Time Assurance Documentation Scanner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlparse

import typer
from rich import print as rprint
from rich.prompt import Confirm

from tads import __version__
from tads.corpus import detect_corpus, get_adapter, list_corpora, list_corpus_profiles
from tads.cost import BudgetExceededError, assert_within_budget
from tads.eval import load_labels, load_manifest, match_findings
from tads.export import load_report_json, write_report_json, write_report_markdown
from tads.fetch import FetchNotSupportedError, fetch_to_path
from tads.ingest import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    IngestError,
    IngestOptions,
    IngestResult,
    default_max_download_bytes,
    ingest_to_text,
)
from tads.ingest.detect import looks_like_url
from tads.llm import list_providers
from tads.llm.base import ProviderNotConfiguredError
from tads.llm.env import default_provider_id
from tads.pipeline import plan_scan, run_scan
from tads.parsing.scope import AnalysisScope
from tads.privacy import PrivacyPolicy
from tads.schemas.cost import CostBudget
from tads.schemas.report import AnalysisMode, PrivacyMode

app = typer.Typer(
    name="tads",
    help="Time Assurance Documentation Scanner",
    no_args_is_help=True,
)


def _load_dotenv() -> None:
    """Best-effort .env loader without requiring python-dotenv."""
    path = Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _default_provider() -> str:
    """CLI/pipeline default: Ollama Cloud unless overridden."""
    return default_provider_id()


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    rprint(__version__)


@app.command("info")
def info_cmd() -> None:
    """Show corpora, providers, and phase status."""
    rprint(f"[bold]tads[/bold] {__version__} (Phase 2)")
    rprint(f"corpora: {', '.join(list_corpora())}")
    rprint(f"providers: {', '.join(list_providers())}")
    rprint("commands: plan, scan, convert, fetch, render, corpora, corpus-describe")


@app.command("corpora")
def corpora_cmd(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List registered corpus adapters and tiers."""
    profiles = list_corpus_profiles()
    if json_out:
        typer.echo(json.dumps(profiles, indent=2))
        return
    for profile in profiles:
        fetch = "fetch" if profile["supports_remote_fetch"] == "true" else "local-file"
        rprint(
            f"[bold]{profile['corpus_id']}[/bold] "
            f"(tier {profile['tier']}, {fetch}) — {profile['display_name']}"
        )


@app.command("fetch")
def fetch_cmd(
    doc_id: str = typer.Argument(..., help="Document id, e.g. RFC5905"),
    corpus: Optional[str] = typer.Option(
        None,
        "--corpus",
        help="Corpus id (default: auto-detect, else ietf)",
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path (default: inputs/<doc_id>.txt)",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing output files without prompting",
    ),
) -> None:
    """Download a document when the corpus supports remote fetch (IETF today)."""
    resolved_corpus = corpus or detect_corpus(doc_id) or "ietf"
    adapter = get_adapter(resolved_corpus)
    normalized = adapter.normalize_id(doc_id)
    out = output or Path("inputs") / f"{normalized.replace(' ', '_')}.txt"
    _confirm_overwrite([out], overwrite=overwrite)
    try:
        path = fetch_to_path(normalized, out, corpus=resolved_corpus)
    except FetchNotSupportedError as exc:
        rprint(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=2) from exc
    rprint(f"wrote {path} [dim](corpus={resolved_corpus})[/dim]")


@app.command("convert")
def convert_cmd(
    source: str = typer.Argument(..., help="Local path or http(s) URL"),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        "--save-text",
        help="Where to write converted plain text",
    ),
    archive_member: Optional[str] = typer.Option(
        None,
        "--archive-member",
        help="Member path/name inside .zip/.tgz (optional)",
    ),
    max_download_mb: float = typer.Option(
        DEFAULT_MAX_DOWNLOAD_BYTES / (1024 * 1024),
        "--max-download-mb",
        help="Max download/local payload size in MiB",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing output files without prompting",
    ),
) -> None:
    """Fetch/convert a document (txt/docx/pdf/zip/tgz) to plain text."""
    out = _resolve_text_output_path(str(output), source=source)
    _confirm_overwrite([out], overwrite=overwrite)
    try:
        result = _ingest(
            source,
            archive_member=archive_member,
            max_download_mb=max_download_mb,
            save_text=str(out),
        )
    except IngestError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rprint(f"[green]Wrote[/green] {result.saved_text_path}")
    rprint(
        f"converter={result.converter} media_type={result.media_type} "
        f"chars={len(result.text):,}"
        + (f" member={result.member_name}" if result.member_name else "")
    )
    for note in result.notes:
        rprint(f"  • {note}")


@app.command("plan")
def plan_cmd(
    source: str = typer.Argument(..., help="Local path or http(s) URL"),
    doc_id: str = typer.Option(..., "--doc-id", help="Document id, e.g. RFC5905"),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus id (default: auto-detect, else ietf)"
    ),
    provider: str = typer.Option(
        None,
        "--provider",
        help="LLM provider (default: TADS_LLM_PROVIDER / TADS_PROVIDER or ollama)",
    ),
    model: Optional[str] = typer.Option(None, "--model"),
    max_cost_usd: Optional[float] = typer.Option(None, "--max-cost-usd"),
    max_tokens: Optional[int] = typer.Option(
        None,
        "--max-tokens",
        help="Max estimated LLM tokens (input+output spend budget)",
    ),
    force_sections: bool = typer.Option(
        False, "--force-sections", help="Force section-aware analysis"
    ),
    include_front_matter: bool = typer.Option(
        False,
        "--include-front-matter",
        help="Include TOC/preamble sections in analysis (skipped by default)",
    ),
    include_index_and_acknowledgments: bool = typer.Option(
        False,
        "--include-index-and-acknowledgments",
        help="Include Index and Acknowledgments sections (skipped by default)",
    ),
    max_sections: Optional[int] = typer.Option(
        None, "--max-sections", help="Analyze at most N body sections"
    ),
    max_chars: Optional[int] = typer.Option(
        None, "--max-chars", help="Cap analyzed document characters"
    ),
    max_input_tokens: Optional[int] = typer.Option(
        None,
        "--max-input-tokens",
        help="Cap estimated input tokens from document text (not LLM spend)",
    ),
    archive_member: Optional[str] = typer.Option(
        None,
        "--archive-member",
        help="Member path/name inside .zip/.tgz (optional)",
    ),
    max_download_mb: float = typer.Option(
        DEFAULT_MAX_DOWNLOAD_BYTES / (1024 * 1024),
        "--max-download-mb",
        help="Max download/local payload size in MiB",
    ),
    save_text: Optional[Path] = typer.Option(
        None,
        "--save-text",
        help="Persist converted plain text to this path (ephemeral by default)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable plan"),
    # Accepted for shared plan/scan scripts; plan does not write report files.
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Ignored on plan (scan writes reports here)",
        hidden=False,
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Ignored on plan (scan skips cost confirmation)",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Ignored on plan (scan overwrites outputs without prompting)",
    ),
) -> None:
    """Parse a document and print analysis mode + cost estimate (no LLM calls)."""
    _ = (output, yes, overwrite)  # accepted for CLI parity with scan
    resolved = _resolve_corpus(doc_id, corpus)
    try:
        ingested = _ingest(
            source,
            archive_member=archive_member,
            max_download_mb=max_download_mb,
            save_text=str(save_text) if save_text else None,
        )
    except IngestError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    plan = _build_plan(
        ingested,
        doc_id=doc_id,
        corpus=resolved,
        provider=provider or _default_provider(),
        model=model,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
        force_sections=force_sections,
        include_front_matter=include_front_matter,
        include_index_and_acknowledgments=include_index_and_acknowledgments,
        max_sections=max_sections,
        max_chars=max_chars,
        max_input_tokens=max_input_tokens,
    )
    _print_plan(plan, json_out=json_out, corpus=resolved, ingested=ingested)
    try:
        assert_within_budget(plan.cost_estimate)
    except BudgetExceededError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


@app.command("scan")
def scan_cmd(
    source: str = typer.Argument(..., help="Local path or http(s) URL"),
    doc_id: str = typer.Option(..., "--doc-id", help="Document id, e.g. RFC5905"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path prefix (writes .json and .md; default: outputs/<doc_id>)",
    ),
    corpus: Optional[str] = typer.Option(
        None, "--corpus", help="Corpus id (default: auto-detect, else ietf)"
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="LLM provider (default: TADS_LLM_PROVIDER / TADS_PROVIDER or ollama)",
    ),
    model: Optional[str] = typer.Option(None, "--model"),
    max_cost_usd: Optional[float] = typer.Option(None, "--max-cost-usd"),
    max_tokens: Optional[int] = typer.Option(
        None,
        "--max-tokens",
        help="Max estimated LLM tokens (input+output spend budget)",
    ),
    force_sections: bool = typer.Option(
        False, "--force-sections", help="Force section-aware analysis"
    ),
    include_front_matter: bool = typer.Option(
        False,
        "--include-front-matter",
        help="Include TOC/preamble sections in analysis (skipped by default)",
    ),
    include_index_and_acknowledgments: bool = typer.Option(
        False,
        "--include-index-and-acknowledgments",
        help="Include Index and Acknowledgments sections (skipped by default)",
    ),
    max_sections: Optional[int] = typer.Option(
        None, "--max-sections", help="Analyze at most N body sections"
    ),
    max_chars: Optional[int] = typer.Option(
        None, "--max-chars", help="Cap analyzed document characters"
    ),
    max_input_tokens: Optional[int] = typer.Option(
        None,
        "--max-input-tokens",
        help="Cap estimated input tokens from document text (not LLM spend)",
    ),
    archive_member: Optional[str] = typer.Option(
        None,
        "--archive-member",
        help="Member path/name inside .zip/.tgz (optional)",
    ),
    max_download_mb: float = typer.Option(
        DEFAULT_MAX_DOWNLOAD_BYTES / (1024 * 1024),
        "--max-download-mb",
        help="Max download/local payload size in MiB",
    ),
    save_text: Optional[Path] = typer.Option(
        None,
        "--save-text",
        help="Persist converted plain text to this path (ephemeral by default)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cost confirmation"),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing output files without prompting",
    ),
    max_output_tokens: int = typer.Option(
        16384,
        "--max-output-tokens",
        help="Max tokens for each model completion (raise if JSON truncates)",
    ),
    save_raw_on_error: bool = typer.Option(
        True,
        "--save-raw-on-error/--no-save-raw-on-error",
        help="Save raw model text next to outputs if JSON parsing fails",
    ),
) -> None:
    """Scan a document and write JSON + Markdown reports for human review."""
    resolved = _resolve_corpus(doc_id, corpus)
    out_prefix = output or Path("outputs") / doc_id.replace(" ", "_")
    json_path, md_path = _output_paths(out_prefix)
    write_paths: list[Path] = [json_path, md_path]
    save_text_resolved: Optional[Path] = None
    if save_text is not None:
        save_text_resolved = _resolve_text_output_path(str(save_text), source=source)
        write_paths.append(save_text_resolved)
    _confirm_overwrite(write_paths, overwrite=overwrite)

    try:
        ingested = _ingest(
            source,
            archive_member=archive_member,
            max_download_mb=max_download_mb,
            save_text=str(save_text_resolved) if save_text_resolved else None,
        )
    except IngestError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    plan = _build_plan(
        ingested,
        doc_id=doc_id,
        corpus=resolved,
        provider=provider or _default_provider(),
        model=model,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
        force_sections=force_sections,
        include_front_matter=include_front_matter,
        include_index_and_acknowledgments=include_index_and_acknowledgments,
        max_sections=max_sections,
        max_chars=max_chars,
        max_input_tokens=max_input_tokens,
    )
    _print_plan(plan, json_out=False, corpus=resolved, ingested=ingested)
    try:
        assert_within_budget(plan.cost_estimate)
    except BudgetExceededError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if not yes:
        if not Confirm.ask("Proceed with LLM scan?", default=False):
            rprint("Aborted.")
            raise typer.Exit(code=1)

    privacy = PrivacyPolicy(mode=PrivacyMode.PERSIST_OUTPUTS)
    raw_error_path = str(json_path.with_suffix(".raw.txt")) if save_raw_on_error else None
    try:
        report = run_scan(
            ingested.text,
            doc_id=doc_id,
            corpus=resolved,
            provider=provider or _default_provider(),
            model=model,
            budget=CostBudget(max_cost_usd=max_cost_usd, max_tokens=max_tokens),
            privacy=privacy,
            force_mode=AnalysisMode.SECTION_AWARE if force_sections else None,
            source_path=ingested.saved_text_path or ingested.source,
            max_output_tokens=max_output_tokens,
            enforce_budget=True,
            on_progress=lambda msg: rprint(f"[dim]{msg}[/dim]"),
            save_raw_on_error=raw_error_path,
            scope=AnalysisScope(
                include_front_matter=include_front_matter,
                include_index_and_acknowledgments=include_index_and_acknowledgments,
                max_sections=max_sections,
                max_chars=max_chars,
                max_input_tokens=max_input_tokens,
            ),
        )
    except ProviderNotConfiguredError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001 - surface provider/runtime errors cleanly
        rprint(f"[red]Scan failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    write_report_json(report, json_path)
    write_report_markdown(report, md_path)
    rprint(f"[green]Wrote[/green] {json_path}")
    rprint(f"[green]Wrote[/green] {md_path}")
    rprint(
        f"Findings: {len(report.findings)}. "
        "Edit dispositions in the JSON, then run:"
    )
    typer.echo(f"tads render {json_path} -o {md_path}")


@app.command("render")
def render_cmd(
    report_json: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Markdown output path (default: alongside JSON with .md)",
    ),
) -> None:
    """Re-render Markdown from a (possibly reviewed) JSON report."""
    report = load_report_json(report_json)
    md_path = output or report_json.with_suffix(".md")
    write_report_markdown(report, md_path)
    rprint(f"wrote {md_path} ({len(report.findings)} findings)")


@app.command("eval-manifest")
def eval_manifest_cmd(
    manifest: Path = typer.Option(
        Path("eval/corpus/seed_manifest.yaml"),
        "--manifest",
        exists=True,
        readable=True,
    ),
) -> None:
    """List bootstrap evaluation documents."""
    data = load_manifest(manifest)
    for doc in data.documents:
        rprint(f"[bold]{doc.doc_id}[/bold] — {doc.title}")
        rprint(f"  {doc.rationale}")
        rprint(f"  {doc.source_uri}")


@app.command("eval-match")
def eval_match_cmd(
    labels: Path = typer.Argument(..., exists=True, readable=True),
    report: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Compare a report JSON findings list against gold labels."""
    expected = load_labels(labels)
    report_data = json.loads(report.read_text(encoding="utf-8"))
    summary = match_findings(expected, report_data.get("findings", []))
    summary.doc_id = report_data.get("document", {}).get("doc_id", labels.stem)
    typer.echo(summary.model_dump_json(indent=2))


@app.command("corpus-describe")
def corpus_describe_cmd(corpus: str = typer.Argument("ietf")) -> None:
    """Print corpus adapter metadata used in prompts."""
    adapter = get_adapter(corpus)
    typer.echo(json.dumps(adapter.describe(), indent=2))


def _resolve_corpus(doc_id: str, corpus: Optional[str]) -> str:
    if corpus:
        return get_adapter(corpus).corpus_id
    detected = detect_corpus(doc_id)
    return detected or "ietf"


def _ingest(
    source: str,
    *,
    archive_member: Optional[str],
    max_download_mb: float,
    save_text: Optional[str],
) -> IngestResult:
    max_bytes = int(max_download_mb * 1024 * 1024)
    if max_bytes <= 0:
        max_bytes = default_max_download_bytes()
    return ingest_to_text(
        source,
        options=IngestOptions(
            max_download_bytes=max_bytes,
            archive_member=archive_member,
            save_text_path=save_text,
        ),
    )


def _build_plan(
    ingested: IngestResult,
    *,
    doc_id: str,
    corpus: str,
    provider: str,
    model: Optional[str],
    max_cost_usd: Optional[float],
    max_tokens: Optional[int],
    force_sections: bool,
    include_front_matter: bool = False,
    include_index_and_acknowledgments: bool = False,
    max_sections: Optional[int] = None,
    max_chars: Optional[int] = None,
    max_input_tokens: Optional[int] = None,
):
    budget = CostBudget(max_cost_usd=max_cost_usd, max_tokens=max_tokens)
    return plan_scan(
        ingested.text,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        budget=budget,
        force_mode=AnalysisMode.SECTION_AWARE if force_sections else None,
        source_path=ingested.saved_text_path or ingested.source,
        scope=AnalysisScope(
            include_front_matter=include_front_matter,
            include_index_and_acknowledgments=include_index_and_acknowledgments,
            max_sections=max_sections,
            max_chars=max_chars,
            max_input_tokens=max_input_tokens,
        ),
    )


def _print_plan(
    plan,
    *,
    json_out: bool,
    corpus: str,
    ingested: Optional[IngestResult] = None,
) -> None:
    scoped = plan.scoped
    coverage = _coverage_payload(scoped)
    payload = {
        "corpus": corpus,
        "doc_id": plan.document.doc_id,
        "title": plan.document.title,
        "ingest": None
        if ingested is None
        else {
            "source": ingested.source,
            "media_type": ingested.media_type,
            "converter": ingested.converter,
            "member_name": ingested.member_name,
            "bytes_fetched": ingested.bytes_fetched,
            "saved_text_path": ingested.saved_text_path,
            "notes": ingested.notes,
        },
        "document": {
            "sections": scoped.total_sections_before,
            "chars": scoped.document_chars,
            "tokens_est": scoped.document_tokens,
        },
        "eligible": {
            "sections": scoped.eligible_sections,
            "chars": scoped.eligible_chars,
            "tokens_est": scoped.eligible_tokens,
            "skipped_front_matter_sections": scoped.skipped_front_matter_sections,
            "skipped_index_ack_sections": scoped.skipped_index_ack_sections,
        },
        "analyzed": {
            "sections": plan.section_count,
            "chars": scoped.analyzed_chars,
            "tokens_est": scoped.analyzed_tokens,
            "truncated": scoped.truncated,
        },
        "coverage": coverage,
        "analysis_mode": plan.analysis_mode.value,
        "provider": plan.provider_id,
        "model": plan.model,
        "privacy_mode": plan.privacy.mode.value,
        "cost_estimate": plan.cost_estimate.model_dump(),
        "corpus_profile": get_adapter(corpus).describe(),
        "scope": {
            "include_front_matter": plan.scope.include_front_matter,
            "include_index_and_acknowledgments": (
                plan.scope.include_index_and_acknowledgments
            ),
            "max_sections": plan.scope.max_sections,
            "max_chars": plan.scope.max_chars,
            "max_input_tokens": plan.scope.max_input_tokens,
            "notes": scoped.notes,
        },
    }
    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        return
    rprint(
        f"[bold]{plan.document.doc_id}[/bold] — {plan.document.title or ''} "
        f"[dim](corpus={corpus})[/dim]"
    )
    if ingested is not None:
        member = f" member={ingested.member_name}" if ingested.member_name else ""
        rprint(
            f"ingest: {ingested.media_type} via {ingested.converter} "
            f"({ingested.bytes_fetched:,} bytes){member}"
        )
        for note in ingested.notes:
            rprint(f"  • {note}")
    rprint(
        "document: "
        f"{scoped.total_sections_before} sections, "
        f"{scoped.document_chars:,} chars, "
        f"~{scoped.document_tokens:,} tokens"
    )
    skip_bits = [
        f"{scoped.skipped_front_matter_sections} front-matter/TOC",
        f"{scoped.skipped_index_ack_sections} Index/Acknowledgments",
    ]
    rprint(
        "eligible: "
        f"{scoped.eligible_sections} sections "
        f"(after skipping {', '.join(skip_bits)}), "
        f"{scoped.eligible_chars:,} chars, "
        f"~{scoped.eligible_tokens:,} tokens"
    )
    rprint(
        "analyzed: "
        f"{plan.section_count} sections, "
        f"{scoped.analyzed_chars:,} chars, "
        f"~{scoped.analyzed_tokens:,} tokens"
        + (" [truncated]" if scoped.truncated else "")
    )
    rprint(
        "coverage: "
        f"sections {coverage['sections_pct']:.1f}% of eligible, "
        f"chars {coverage['chars_pct']:.1f}% of eligible "
        f"({coverage['chars_pct_of_document']:.1f}% of full document)"
    )
    rprint(f"analysis_mode: {plan.analysis_mode.value}")
    rprint(f"provider/model: {plan.provider_id} / {plan.model}")
    est = plan.cost_estimate
    cost = (
        f"${est.estimated_cost_usd:.4f}"
        if est.estimated_cost_usd is not None
        else "unknown"
    )
    rprint(
        f"estimate: {est.estimated_total_tokens} tokens, {cost} USD "
        f"(within_budget={est.within_budget})"
    )
    for note in est.notes:
        rprint(f"  • {note}")


def _coverage_payload(scoped) -> dict[str, float]:
    def _pct(part: int, whole: int) -> float:
        if whole <= 0:
            return 100.0 if part <= 0 else 0.0
        return 100.0 * part / whole

    return {
        "sections_pct": _pct(len(scoped.sections), scoped.eligible_sections),
        "chars_pct": _pct(scoped.analyzed_chars, scoped.eligible_chars),
        "tokens_pct": _pct(scoped.analyzed_tokens, scoped.eligible_tokens),
        "chars_pct_of_document": _pct(scoped.analyzed_chars, scoped.document_chars),
        "tokens_pct_of_document": _pct(scoped.analyzed_tokens, scoped.document_tokens),
    }


def _output_paths(output: Path) -> tuple[Path, Path]:
    if output.suffix.lower() in {".json", ".md"}:
        stem = output.with_suffix("")
    else:
        stem = output
    return Path(f"{stem}.json"), Path(f"{stem}.md")


def _source_stem(source: str) -> str:
    if looks_like_url(source):
        name = Path(urlparse(source).path).name or "document"
    else:
        name = Path(source).expanduser().name or "document"
    return Path(name).stem or "document"


def _resolve_text_output_path(save_text_path: str, *, source: str) -> Path:
    """Resolve convert/--save-text targets the same way as ingest."""
    out = Path(save_text_path).expanduser()
    if save_text_path.endswith(("/", "\\")) or (out.exists() and out.is_dir()):
        return out / f"{_source_stem(source)}.txt"
    return out


def _existing_output_files(paths: Sequence[Path]) -> list[Path]:
    return [p.expanduser() for p in paths if p.expanduser().is_file()]


def _confirm_overwrite(paths: Sequence[Path], *, overwrite: bool) -> None:
    """Warn and optionally abort when outputs already exist."""
    existing = _existing_output_files(paths)
    if not existing or overwrite:
        return
    rprint("[yellow]Output already exists:[/yellow]")
    for path in existing:
        rprint(f"  • {path}")
    if not Confirm.ask("Overwrite?", default=False):
        rprint("Aborted.")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
