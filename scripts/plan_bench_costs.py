#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Preflight cost estimates for the 12-doc benchmark across configurations.

Runs `tads plan` logic locally (no LLM API calls). Writes a summary table plus
optional JSON/CSV under outputs/ by default.

Default configurations match ``run_bench_scans.py`` (OpenAI Mini + Gemini Flash).
Use ``--config`` / ``--model`` for Luna and caller-specified Anthropic/Ollama
models. Planning does not contact providers.

Usage:
  scripts/plan_bench_costs.py
  scripts/plan_bench_costs.py --json-out outputs/bench_plan.json
  scripts/plan_bench_costs.py --config openai-luna-none --force-sections \\
      --max-sections-per-request 8
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from bench_common import (  # noqa: E402
    BenchConfigError,
    BenchControls,
    BenchModelConfig,
    DEFAULT_PLAN_CONFIGS,
    add_model_selection_args,
    add_shared_control_args,
    configs_from_args,
    controls_from_args,
    validate_controls,
)
from tads.ingest import IngestOptions, ingest_to_text
from tads.parsing.chunks import DEFAULT_MAX_SECTIONS_PER_REQUEST
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

# Back-compat alias: historical planner default provider/model pairs.
BENCH_MODELS: list[tuple[str, str]] = [
    (c.provider, c.model) for c in DEFAULT_PLAN_CONFIGS
]


@dataclass
class PlanRow:
    bench_id: str
    doc_id: str
    corpus: str
    label: str
    source: str
    config_id: str
    provider: str
    model: str
    reasoning_effort: str | None
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
    config: BenchModelConfig,
    controls: BenchControls,
    scope: AnalysisScope,
) -> PlanRow:
    try:
        plan = plan_scan(
            text,
            doc_id=doc["doc_id"],
            corpus=doc["corpus"],
            provider=config.provider,
            model=config.model,
            source_path=source_path,
            scope=scope,
            force_mode=AnalysisMode.SECTION_AWARE if controls.force_sections else None,
            max_output_tokens=(
                controls.max_output_tokens
                if controls.max_output_tokens is not None
                else 16384
            ),
            reasoning_effort=config.reasoning_effort,
            section_chunking=controls.section_chunking,
            max_sections_per_request=(
                controls.max_sections_per_request
                if controls.max_sections_per_request is not None
                else DEFAULT_MAX_SECTIONS_PER_REQUEST
            ),
        )
    except Exception as exc:  # noqa: BLE001 - collect per-row failures for the report
        return PlanRow(
            bench_id=doc["bench_id"],
            doc_id=doc["doc_id"],
            corpus=doc["corpus"],
            label=doc["label"],
            source=source_path,
            config_id=config.config_id,
            provider=config.provider,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
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
        config_id=config.config_id,
        provider=config.provider,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        analysis_mode=plan.analysis_mode.value,
        sections=plan.section_count,
        analyzed_tokens=plan.scoped.analyzed_tokens,
        estimated_input_tokens=est.estimated_input_tokens,
        estimated_output_tokens=est.estimated_output_tokens,
        estimated_total_tokens=est.estimated_total_tokens,
        estimated_cost_usd=est.estimated_cost_usd,
        pricing_known=est.estimated_cost_usd is not None,
    )


def _print_summary(rows: list[PlanRow], configs: list[BenchModelConfig]) -> None:
    errors = [r for r in rows if r.error]
    if errors:
        print("\nErrors:", file=sys.stderr)
        for row in errors:
            print(
                f"  {row.doc_id} / {row.config_id} — {row.error}",
                file=sys.stderr,
            )

    print("\nPer-document estimated USD (indicative pricing table)\n")
    header = ["doc_id", "mode", "sections"] + [c.config_id for c in configs]
    print(" | ".join(f"{h:>22}" for h in header))
    print("-" * (25 * len(header)))

    by_key = {(r.doc_id, r.config_id): r for r in rows}
    for doc in BENCH_DOCS:
        cells = [doc["doc_id"][:22]]
        sample = by_key.get((doc["doc_id"], configs[0].config_id)) if configs else None
        cells.append((sample.analysis_mode if sample else "?")[:22])
        cells.append(str(sample.sections if sample else "?"))
        for config in configs:
            row = by_key.get((doc["doc_id"], config.config_id))
            cells.append(_fmt_usd(row.estimated_cost_usd if row else None))
        print(" | ".join(f"{c:>22}" for c in cells))

    print("\nTotals across documents\n")
    for config in configs:
        subset = [
            r
            for r in rows
            if r.config_id == config.config_id and not r.error
        ]
        total_tokens = sum(r.estimated_total_tokens for r in subset)
        priced = [r for r in subset if r.estimated_cost_usd is not None]
        total_usd = sum(r.estimated_cost_usd or 0.0 for r in priced)
        label = f"{config.config_id:28} {config.provider}/{config.model}"
        if config.reasoning_effort is not None:
            label += f" effort={config.reasoning_effort}"
        if len(priced) == len(subset) and subset:
            print(f"  {label}  tokens ~{total_tokens:,}   est. USD {_fmt_usd(total_usd)}")
        else:
            print(
                f"  {label}  tokens ~{total_tokens:,}   est. USD n/a (no pricing row)"
            )
    print(
        "\nNote: estimates exclude contingent JSON repair and semantic-correction "
        "secondary requests; actual cost may be higher."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run tads plan cost estimates for the 12-doc benchmark. "
            "Default: ollama gpt-oss:120b, openai-mini, anthropic claude-sonnet-4-5, "
            "gemini-flash (historical planner matrix)."
        )
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
    add_model_selection_args(parser)
    add_shared_control_args(parser)
    args = parser.parse_args()

    try:
        configs = configs_from_args(args, defaults=DEFAULT_PLAN_CONFIGS)
        controls = controls_from_args(args)
        validate_controls(controls)
    except BenchConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

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
        max_sections=controls.max_sections,
        max_input_tokens=controls.max_input_tokens,
    )

    rows: list[PlanRow] = []
    print(
        f"Planning {len(BENCH_DOCS)} documents × {len(configs)} configurations "
        f"({len(BENCH_DOCS) * len(configs)} estimates, no LLM calls)…"
    )
    for config in configs:
        effort = (
            f" reasoning_effort={config.reasoning_effort}"
            if config.reasoning_effort is not None
            else ""
        )
        print(f"  • {config.config_id}: {config.provider}/{config.model}{effort}")

    for doc in BENCH_DOCS:
        source_path = str(inputs_dir / doc["source"])
        ingested = ingest_to_text(source_path, options=IngestOptions())
        for config in configs:
            rows.append(
                _plan_one(
                    text=ingested.text,
                    source_path=source_path,
                    doc=doc,
                    config=config,
                    controls=controls,
                    scope=scope,
                )
            )

    _print_summary(rows, configs)

    if args.no_write:
        return 0

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "inputs_dir": str(inputs_dir),
        "configurations": [c.to_public_dict() for c in configs],
        "controls": controls.to_public_dict(),
        "documents": BENCH_DOCS,
        "rows": [asdict(r) for r in rows],
        "notes": [
            "Estimates exclude contingent JSON repair and semantic-correction "
            "secondary requests.",
            "Historical planner default remains ollama/openai/anthropic/gemini; "
            "the runner default remains openai-mini + gemini-flash. Explicit "
            "--config/--model selection is shared.",
            "Definitive comparisons should use a clean committed git tree.",
        ],
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
