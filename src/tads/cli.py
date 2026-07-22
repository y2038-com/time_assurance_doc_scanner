"""CLI entry point for the Time Assurance Documentation Scanner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.prompt import Confirm

from tads import __version__
from tads.corpus import get_adapter, list_corpora
from tads.cost import BudgetExceededError, assert_within_budget
from tads.eval import load_labels, load_manifest, match_findings
from tads.export import load_report_json, write_report_json, write_report_markdown
from tads.fetch import fetch_to_path
from tads.llm import list_providers
from tads.llm.base import ProviderNotConfiguredError
from tads.pipeline import plan_scan, run_scan
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


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    rprint(__version__)


@app.command("info")
def info_cmd() -> None:
    """Show corpora, providers, and phase status."""
    rprint(f"[bold]tads[/bold] {__version__} (Phase 1)")
    rprint(f"corpora: {', '.join(list_corpora())}")
    rprint(f"providers: {', '.join(list_providers())}")
    rprint("commands: plan, scan, fetch, render, eval-manifest, eval-match")


@app.command("fetch")
def fetch_cmd(
    doc_id: str = typer.Argument(..., help="RFC id, e.g. RFC5905 or 5905"),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path (default: .tads/inputs/<doc_id>.txt)",
    ),
) -> None:
    """Download IETF plain-text RFC / Internet-Draft."""
    adapter = get_adapter("ietf")
    normalized = adapter.normalize_id(doc_id)
    out = output or Path(".tads/inputs") / f"{normalized}.txt"
    path = fetch_to_path(normalized, out)
    rprint(f"wrote {path}")


@app.command("plan")
def plan_cmd(
    file: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    doc_id: str = typer.Option(..., "--doc-id", help="Document id, e.g. RFC5905"),
    corpus: str = typer.Option("ietf", "--corpus"),
    provider: str = typer.Option("openai", "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
    max_cost_usd: Optional[float] = typer.Option(None, "--max-cost-usd"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    force_sections: bool = typer.Option(
        False, "--force-sections", help="Force section-aware analysis"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable plan"),
) -> None:
    """Parse a document and print analysis mode + cost estimate (no LLM calls)."""
    plan = _build_plan(
        file,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
        force_sections=force_sections,
    )
    _print_plan(plan, json_out=json_out)
    try:
        assert_within_budget(plan.cost_estimate)
    except BudgetExceededError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


@app.command("scan")
def scan_cmd(
    file: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    doc_id: str = typer.Option(..., "--doc-id", help="Document id, e.g. RFC5905"),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output path prefix (writes .json and .md)",
    ),
    corpus: str = typer.Option("ietf", "--corpus"),
    provider: str = typer.Option("openai", "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
    max_cost_usd: Optional[float] = typer.Option(None, "--max-cost-usd"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    force_sections: bool = typer.Option(
        False, "--force-sections", help="Force section-aware analysis"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cost confirmation"),
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
    plan = _build_plan(
        file,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        max_cost_usd=max_cost_usd,
        max_tokens=max_tokens,
        force_sections=force_sections,
    )
    _print_plan(plan, json_out=False)
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
    json_path, md_path = _output_paths(output)
    raw_error_path = str(json_path.with_suffix(".raw.txt")) if save_raw_on_error else None
    try:
        report = run_scan(
            file.read_text(encoding="utf-8", errors="replace"),
            doc_id=doc_id,
            corpus=corpus,
            provider=provider,
            model=model,
            budget=CostBudget(max_cost_usd=max_cost_usd, max_tokens=max_tokens),
            privacy=privacy,
            force_mode=AnalysisMode.SECTION_AWARE if force_sections else None,
            source_path=str(file),
            max_output_tokens=max_output_tokens,
            enforce_budget=True,
            on_progress=lambda msg: rprint(f"[dim]{msg}[/dim]"),
            save_raw_on_error=raw_error_path,
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
        "Edit dispositions in the JSON, then run: "
        f"[bold]tads render {json_path} -o {md_path}[/bold]"
    )


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
    """Print corpus adapter metadata."""
    adapter = get_adapter(corpus)
    typer.echo(json.dumps(adapter.describe(), indent=2))


def _build_plan(
    file: Path,
    *,
    doc_id: str,
    corpus: str,
    provider: str,
    model: Optional[str],
    max_cost_usd: Optional[float],
    max_tokens: Optional[int],
    force_sections: bool,
):
    text = file.read_text(encoding="utf-8", errors="replace")
    budget = CostBudget(max_cost_usd=max_cost_usd, max_tokens=max_tokens)
    return plan_scan(
        text,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        budget=budget,
        force_mode=AnalysisMode.SECTION_AWARE if force_sections else None,
        source_path=str(file),
    )


def _print_plan(plan, *, json_out: bool) -> None:
    payload = {
        "doc_id": plan.document.doc_id,
        "title": plan.document.title,
        "sections": plan.section_count,
        "analysis_mode": plan.analysis_mode.value,
        "provider": plan.provider_id,
        "model": plan.model,
        "privacy_mode": plan.privacy.mode.value,
        "cost_estimate": plan.cost_estimate.model_dump(),
    }
    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        return
    rprint(f"[bold]{plan.document.doc_id}[/bold] — {plan.document.title or ''}")
    rprint(f"sections: {plan.section_count}")
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


def _output_paths(output: Path) -> tuple[Path, Path]:
    if output.suffix.lower() in {".json", ".md"}:
        stem = output.with_suffix("")
    else:
        stem = output
    return Path(f"{stem}.json"), Path(f"{stem}.md")


if __name__ == "__main__":
    app()
