"""CLI entry point. Phase 0 exposes planning/info commands; scan arrives in Phase 1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from tads import __version__
from tads.corpus import get_adapter, list_corpora
from tads.cost import BudgetExceededError, assert_within_budget
from tads.eval import load_labels, load_manifest, match_findings
from tads.llm import list_providers
from tads.pipeline import plan_scan
from tads.schemas.cost import CostBudget

app = typer.Typer(
    name="tads",
    help="Time Assurance Documentation Scanner",
    no_args_is_help=True,
)


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    rprint(__version__)


@app.command("info")
def info_cmd() -> None:
    """Show corpora, providers, and Phase 0 status."""
    rprint(f"[bold]tads[/bold] {__version__} (Phase 0)")
    rprint(f"corpora: {', '.join(list_corpora())}")
    rprint(f"providers: {', '.join(list_providers())}")
    rprint("scan pipeline: planned for Phase 1")


@app.command("plan")
def plan_cmd(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Local document text"),
    doc_id: str = typer.Option(..., "--doc-id", help="Document id, e.g. RFC5905"),
    corpus: str = typer.Option("ietf", "--corpus"),
    provider: str = typer.Option("openai", "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
    max_cost_usd: Optional[float] = typer.Option(None, "--max-cost-usd"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable plan"),
) -> None:
    """Parse a document and print analysis mode + cost estimate (no LLM calls)."""
    text = file.read_text(encoding="utf-8", errors="replace")
    budget = CostBudget(max_cost_usd=max_cost_usd, max_tokens=max_tokens)
    plan = plan_scan(
        text,
        doc_id=doc_id,
        corpus=corpus,
        provider=provider,
        model=model,
        budget=budget,
    )
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
    else:
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
    try:
        assert_within_budget(plan.cost_estimate)
    except BudgetExceededError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


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


if __name__ == "__main__":
    app()
