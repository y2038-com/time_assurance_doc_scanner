#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Preflight cost estimates for the 12-doc benchmark across provider/model pairs.

Runs `tads plan` logic locally (no LLM API calls). Writes a summary table plus
optional JSON/CSV under outputs/ by default.

Usage:
  scripts/plan_bench_costs.py
  scripts/plan_bench_costs.py --json-out outputs/bench_plan.json
  scripts/plan_bench_costs.py --inputs-dir inputs --csv-out outputs/bench_plan.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from tads.ingest import IngestOptions, ingest_to_text
from tads.parsing.scope import AnalysisScope
from tads.pipeline import plan_scan
from tads.schemas.report import AnalysisMode

# 12-doc time-assurance benchmark (see docs/backlog.md / conversation history).
BENCH_DOCS: list[dict[str, str]] = [
    {
        "bench_id": "01",
        "doc_id": "RFC5905",
        "corpus": "ietf",
        "source": "RFC5905.txt",
        "label": "NTPv4 / era",
    },
    {
        "bench_id": "02",
        "doc_id": "RFC868",
        "corpus": "ietf",
        "source": "RFC868.txt",
        "label": "Simple epoch",
    },
    {
        "bench_id": "03",
        "doc_id": "EN 300 468",
        "corpus": "etsi",
        "source": "EN_300_468.txt",
        "label": "ETSI 16-bit MJD",
    },
    {
        "bench_id": "04",
        "doc_id": "TS 103 221-2",
        "corpus": "etsi",
        "source": "TS_103_221-2.txt",
        "label": "ETSI awareness-wrong",
    },
    {
        "bench_id": "05",
        "doc_id": "TS 29.061",
        "corpus": "3gpp",
        "source": "TS_29.061.txt",
        "label": "3GPP NTP-style",
    },
    {
        "bench_id": "06",
        "doc_id": "TS 29.274",
        "corpus": "3gpp",
        "source": "TS_29.274.txt",
        "label": "3GPP rollover aware",
    },
    {
        "bench_id": "07",
        "doc_id": "X.680",
        "corpus": "itu-t",
        "source": "X.680.txt",
        "label": "ASN.1 time types",
    },
    {
        "bench_id": "08",
        "doc_id": "ECMA-262",
        "corpus": "ecma",
        "source": "ECMA-262.txt",
        "label": "JS time representation",
    },
    {
        "bench_id": "09",
        "doc_id": "OpenFormula",
        "corpus": "oasis",
        "source": "OpenFormula.txt",
        "label": "Epoch + interop warning",
    },
    {
        "bench_id": "10",
        "doc_id": "hr-time-3",
        "corpus": "w3c",
        "source": "hr-time-3.txt",
        "label": "Monotonic / scope",
    },
    {
        "bench_id": "11",
        "doc_id": "IEEE 1588-2019",
        "corpus": "ieee",
        "source": "IEEE_1588-2019.txt",
        "label": "PTP / precision clocks",
    },
    {
        "bench_id": "12",
        "doc_id": "ECMA-404",
        "corpus": "ecma",
        "source": "ECMA-404.txt",
        "label": "Negative control",
    },
]

# Provider/model pairs smoke-tested for RFC 5905 bake-offs (QUICK_START / rfc5905_provider_compare).
BENCH_MODELS: list[tuple[str, str]] = [
    ("ollama", "gpt-oss:120b"),
    ("openai", "gpt-4.1-mini"),
    ("anthropic", "claude-sonnet-4-5"),
    ("gemini", "gemini-3.6-flash"),
]


@dataclass
class PlanRow:
    bench_id: str
    doc_id: str
    corpus: str
    label: str
    source: str
    provider: str
    model: str
    analysis_mode: str
    sections: int
    analyzed_tokens: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_cost_usd: float | None
    pricing_known: bool
    error: str | None = None


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.4f}"


def _plan_one(
    *,
    text: str,
    source_path: str,
    doc: dict[str, str],
    provider: str,
    model: str,
    scope: AnalysisScope,
    force_sections: bool,
) -> PlanRow:
    try:
        plan = plan_scan(
            text,
            doc_id=doc["doc_id"],
            corpus=doc["corpus"],
            provider=provider,
            model=model,
            source_path=source_path,
            scope=scope,
            force_mode=AnalysisMode.SECTION_AWARE if force_sections else None,
        )
    except Exception as exc:  # noqa: BLE001 - collect per-row failures for the report
        return PlanRow(
            bench_id=doc["bench_id"],
            doc_id=doc["doc_id"],
            corpus=doc["corpus"],
            label=doc["label"],
            source=source_path,
            provider=provider,
            model=model,
            analysis_mode="error",
            sections=0,
            analyzed_tokens=0,
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            estimated_total_tokens=0,
            estimated_cost_usd=None,
            pricing_known=False,
            error=str(exc),
        )

    est = plan.cost_estimate
    return PlanRow(
        bench_id=doc["bench_id"],
        doc_id=doc["doc_id"],
        corpus=doc["corpus"],
        label=doc["label"],
        source=source_path,
        provider=provider,
        model=model,
        analysis_mode=plan.analysis_mode.value,
        sections=plan.section_count,
        analyzed_tokens=plan.scoped.analyzed_tokens,
        estimated_input_tokens=est.estimated_input_tokens,
        estimated_output_tokens=est.estimated_output_tokens,
        estimated_total_tokens=est.estimated_total_tokens,
        estimated_cost_usd=est.estimated_cost_usd,
        pricing_known=est.estimated_cost_usd is not None,
    )


def _print_summary(rows: list[PlanRow]) -> None:
    errors = [r for r in rows if r.error]
    if errors:
        print("\nErrors:", file=sys.stderr)
        for row in errors:
            print(f"  {row.doc_id} / {row.provider}:{row.model} — {row.error}", file=sys.stderr)

    models = BENCH_MODELS
    docs = BENCH_DOCS

    print("\nPer-document estimated USD (indicative pricing table; ollama = n/a)\n")
    header = ["doc_id", "mode", "sections"] + [f"{p}:{m}" for p, m in models]
    print(" | ".join(f"{h:>22}" for h in header))
    print("-" * (25 * len(header)))

    by_key = {(r.doc_id, r.provider, r.model): r for r in rows}
    for doc in docs:
        cells = [doc["doc_id"][:22]]
        sample = by_key.get((doc["doc_id"], models[0][0], models[0][1]))
        cells.append((sample.analysis_mode if sample else "?")[:22])
        cells.append(str(sample.sections if sample else "?"))
        for provider, model in models:
            row = by_key.get((doc["doc_id"], provider, model))
            cells.append(_fmt_usd(row.estimated_cost_usd if row else None))
        print(" | ".join(f"{c:>22}" for c in cells))

    print("\nTotals across 12 documents\n")
    for provider, model in models:
        subset = [r for r in rows if r.provider == provider and r.model == model and not r.error]
        total_tokens = sum(r.estimated_total_tokens for r in subset)
        priced = [r for r in subset if r.estimated_cost_usd is not None]
        total_usd = sum(r.estimated_cost_usd or 0.0 for r in priced)
        if len(priced) == len(subset):
            print(
                f"  {provider:10} {model:22}  "
                f"tokens ~{total_tokens:,}   est. USD {_fmt_usd(total_usd)}"
            )
        else:
            print(
                f"  {provider:10} {model:22}  "
                f"tokens ~{total_tokens:,}   est. USD n/a (no pricing row)"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run tads plan cost estimates for the 12-doc benchmark."
    )
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        default=Path("inputs"),
        help="Directory containing benchmark source files (default: inputs)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("outputs/bench_plan_estimates.json"),
        help="Write machine-readable results here (default: outputs/bench_plan_estimates.json)",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=Path("outputs/bench_plan_estimates.csv"),
        help="Write CSV results here (default: outputs/bench_plan_estimates.csv)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print summary only; do not write JSON/CSV",
    )
    parser.add_argument(
        "--force-sections",
        action="store_true",
        help="Force section-aware analysis for all documents",
    )
    parser.add_argument(
        "--max-sections",
        type=int,
        default=None,
        help="Cap analyzed body sections (passed through to plan scope)",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help="Cap analyzed document input tokens (passed through to plan scope)",
    )
    args = parser.parse_args()

    inputs_dir = args.inputs_dir
    missing: list[str] = []
    for doc in BENCH_DOCS:
        if not (inputs_dir / doc["source"]).is_file():
            missing.append(str(inputs_dir / doc["source"]))
    if missing:
        print("Missing benchmark inputs:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    scope = AnalysisScope(
        max_sections=args.max_sections,
        max_input_tokens=args.max_input_tokens,
    )

    rows: list[PlanRow] = []
    print(f"Planning {len(BENCH_DOCS)} documents × {len(BENCH_MODELS)} models "
          f"({len(BENCH_DOCS) * len(BENCH_MODELS)} estimates, no LLM calls)…")

    for doc in BENCH_DOCS:
        source_path = str(inputs_dir / doc["source"])
        ingested = ingest_to_text(source_path, options=IngestOptions())
        for provider, model in BENCH_MODELS:
            rows.append(
                _plan_one(
                    text=ingested.text,
                    source_path=source_path,
                    doc=doc,
                    provider=provider,
                    model=model,
                    scope=scope,
                    force_sections=args.force_sections,
                )
            )

    _print_summary(rows)

    if args.no_write:
        return 0

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "inputs_dir": str(inputs_dir),
        "models": [{"provider": p, "model": m} for p, m in BENCH_MODELS],
        "documents": BENCH_DOCS,
        "rows": [asdict(r) for r in rows],
    }
    args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out}")

    with args.csv_out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    print(f"Wrote {args.csv_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
