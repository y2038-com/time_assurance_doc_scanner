#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Run the 12-doc benchmark with OpenAI gpt-4.1-mini and Gemini gemini-3.6-flash.

Writes reports under outputs/ using the bake-off naming convention:
  outputs/<doc_id>__<provider>__<model>[-sanitized].json

Usage:
  scripts/run_bench_scans.py --dry-run
  scripts/run_bench_scans.py --plan-only          # cost preflight only (no LLM calls)
  scripts/run_bench_scans.py --max-cost-usd 2.00  # fail closed per scan
  scripts/run_bench_scans.py --skip-existing      # resume partial runs
  scripts/run_bench_scans.py --tag bench2026      # suffix on output prefixes
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Reuse the canonical 12-doc list from the planning script.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from plan_bench_costs import BENCH_DOCS  # noqa: E402

# Cost-conscious bake-off pair (see docs/rfc5905_provider_compare.md).
BENCH_MODELS: list[tuple[str, str]] = [
    ("openai", "gpt-4.1-mini"),
    ("gemini", "gemini-3.6-flash"),
]


@dataclass(frozen=True)
class BenchJob:
    bench_id: str
    doc_id: str
    corpus: str
    source: Path
    provider: str
    model: str
    output_prefix: Path


def _sanitize_model(model: str) -> str:
    return model.replace(":", "-")


def _output_prefix(
    outputs_dir: Path,
    doc_id: str,
    provider: str,
    model: str,
    tag: str | None,
) -> Path:
    slug = doc_id.replace(" ", "_")
    name = f"{slug}__{provider}__{_sanitize_model(model)}"
    if tag:
        name += f"__{tag}"
    return outputs_dir / name


def _tads_bin() -> str:
    found = shutil.which("tads")
    if found:
        return found
    candidate = Path(sys.executable).resolve().parent / "tads"
    if candidate.is_file():
        return str(candidate)
    raise SystemExit(
        "Could not find `tads` on PATH. Activate the project venv "
        "(source .venv/bin/activate) or run: pip install -e ."
    )


def _build_jobs(
    *,
    inputs_dir: Path,
    outputs_dir: Path,
    tag: str | None,
    providers: list[tuple[str, str]] | None = None,
) -> list[BenchJob]:
    missing: list[str] = []
    for doc in BENCH_DOCS:
        if not (inputs_dir / doc["source"]).is_file():
            missing.append(str(inputs_dir / doc["source"]))
    if missing:
        print("Missing benchmark inputs:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        raise SystemExit(1)

    jobs: list[BenchJob] = []
    for doc in BENCH_DOCS:
        source = inputs_dir / doc["source"]
        for provider, model in providers or BENCH_MODELS:
            jobs.append(
                BenchJob(
                    bench_id=doc["bench_id"],
                    doc_id=doc["doc_id"],
                    corpus=doc["corpus"],
                    source=source,
                    provider=provider,
                    model=model,
                    output_prefix=_output_prefix(
                        outputs_dir, doc["doc_id"], provider, model, tag
                    ),
                )
            )
    return jobs


def _scan_command(
    tads: str,
    job: BenchJob,
    *,
    plan_only: bool,
    max_cost_usd: float | None,
    max_sections: int | None,
    max_input_tokens: int | None,
    force_sections: bool,
    max_output_tokens: int | None,
) -> list[str]:
    cmd = [
        tads,
        "plan" if plan_only else "scan",
        str(job.source),
        "--doc-id",
        job.doc_id,
        "--corpus",
        job.corpus,
        "--provider",
        job.provider,
        "--model",
        job.model,
        "-o",
        str(job.output_prefix),
        "--overwrite",
        "-y",
    ]
    if max_cost_usd is not None:
        cmd.extend(["--max-cost-usd", str(max_cost_usd)])
    if max_sections is not None:
        cmd.extend(["--max-sections", str(max_sections)])
    if max_input_tokens is not None:
        cmd.extend(["--max-input-tokens", str(max_input_tokens)])
    if force_sections:
        cmd.append("--force-sections")
    if not plan_only and max_output_tokens is not None:
        cmd.extend(["--max-output-tokens", str(max_output_tokens)])
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the 12-doc benchmark with gpt-4.1-mini and gemini-3.6-flash."
        )
    )
    parser.add_argument(
        "--inputs-dir",
        type=Path,
        default=Path("inputs"),
        help="Directory with benchmark .txt files (default: inputs)",
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path("outputs"),
        help="Report output directory (default: outputs)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Optional suffix on output prefixes (e.g. bench2026)",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Run tads plan only (no LLM API calls)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip scan jobs when <output>.json already exists",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="Per-job cost cap passed to tads (recommended: 2.00 for large specs)",
    )
    parser.add_argument(
        "--max-sections",
        type=int,
        default=None,
        help="Optional body-section cap (passed to plan/scan)",
    )
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        help="Optional analyzed-input token cap",
    )
    parser.add_argument(
        "--force-sections",
        action="store_true",
        help="Force section-aware analysis for every document",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Per-completion output cap for scan (default: tads CLI default)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going after a failed job (default: stop on first failure)",
    )
    args = parser.parse_args()

    tads = _tads_bin()
    jobs = _build_jobs(
        inputs_dir=args.inputs_dir,
        outputs_dir=args.outputs_dir,
        tag=args.tag,
    )
    mode = "plan" if args.plan_only else "scan"
    print(
        f"{mode}: {len(BENCH_DOCS)} documents × {len(BENCH_MODELS)} models "
        f"= {len(jobs)} jobs"
    )
    for provider, model in BENCH_MODELS:
        print(f"  • {provider} / {model}")

    args.outputs_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    skipped = 0
    for index, job in enumerate(jobs, start=1):
        json_path = job.output_prefix.with_suffix(".json")
        if args.skip_existing and json_path.is_file() and not args.plan_only:
            print(f"[{index}/{len(jobs)}] skip existing {json_path}")
            skipped += 1
            continue

        cmd = _scan_command(
            tads,
            job,
            plan_only=args.plan_only,
            max_cost_usd=args.max_cost_usd,
            max_sections=args.max_sections,
            max_input_tokens=args.max_input_tokens,
            force_sections=args.force_sections,
            max_output_tokens=args.max_output_tokens,
        )
        label = f"[{index}/{len(jobs)}] {job.doc_id} ({job.provider}/{job.model})"
        print(f"\n{label}")
        print("  " + shlex.join(cmd))

        if args.dry_run:
            continue

        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            msg = f"{label} failed (exit {result.returncode})"
            failures.append(msg)
            print(msg, file=sys.stderr)
            if not args.continue_on_error:
                break

    print()
    if args.dry_run:
        print(f"Dry run complete ({len(jobs)} commands).")
        return 0

    if failures:
        print(f"Finished with {len(failures)} failure(s), {skipped} skipped.")
        for item in failures:
            print(f"  • {item}", file=sys.stderr)
        return 1

    done = len(jobs) - skipped
    print(f"Finished OK: {done} job(s) run, {skipped} skipped.")
    if not args.plan_only:
        print(f"Reports under {args.outputs_dir.resolve()}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
