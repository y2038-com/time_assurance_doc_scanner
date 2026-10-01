#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Run the 12-doc benchmark with OpenAI gpt-4.1-mini and Gemini gemini-3.6-flash.

Writes reports under outputs/ using the bake-off naming convention:
  outputs/<doc_id>__<provider>__<model>[-sanitized].json

``tads scan -o <prefix>`` is an output prefix. Successful scans write
``<prefix>.json`` and ``<prefix>.md`` by concatenating those suffixes
(not by replacing a pathlib suffix). Prefixes often contain dots
(``gemini-3.6-flash``, ``0.6.0rc3-…``).

Usage:
  scripts/run_bench_scans.py --dry-run
  scripts/run_bench_scans.py --plan-only          # cost preflight only (no LLM calls)
  scripts/run_bench_scans.py --max-cost-usd 2.00  # fail closed per scan
  scripts/run_bench_scans.py --skip-existing      # resume from canonical JSON
  scripts/run_bench_scans.py --tag bench2026      # suffix on output prefixes
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the canonical 12-doc list from the planning script.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from plan_bench_costs import BENCH_DOCS  # noqa: E402

from tads.export import load_report_json, write_report_markdown  # noqa: E402
from tads.jsonutil import JsonLoadError  # noqa: E402
from tads.path_safety import path_under, safe_filename  # noqa: E402
from tads.schemas.report import Report  # noqa: E402

# Cost-conscious bake-off pair (see docs/rfc5905_provider_compare.md).
BENCH_MODELS: list[tuple[str, str]] = [
    ("openai", "gpt-4.1-mini"),
    ("gemini", "gemini-3.6-flash"),
]

_CHILD_STOP_SECONDS = 5.0
_INTERRUPT_EXIT = 130

# Verified against a loaded report when the field is actually recorded:
#   document.doc_id, document.corpus, run.provider, run.model.
# Not stored on the report (path/job identity only):
#   bench_id, --tag, output prefix, source path.


@dataclass(frozen=True)
class BenchJob:
    bench_id: str
    doc_id: str
    corpus: str
    source: Path
    provider: str
    model: str
    output_prefix: Path


@dataclass(frozen=True)
class ArtifactPaths:
    json_path: Path
    md_path: Path


@dataclass(frozen=True)
class ExistingDecision:
    """Skip-existing classification. ``run`` means fall through to a paid scan."""

    action: str
    detail: str = ""


@dataclass
class RunTotals:
    executed: int = 0
    skipped: int = 0
    rendered: int = 0
    failed: int = 0
    artifact_errors: int = 0
    interrupted: int = 0
    failures: list[str] = field(default_factory=list)


def _sanitize_model(model: str) -> str:
    return model.replace(":", "-")


def _output_prefix(
    outputs_dir: Path,
    doc_id: str,
    provider: str,
    model: str,
    tag: str | None,
) -> Path:
    name = "__".join(
        (
            safe_filename(doc_id),
            safe_filename(provider),
            safe_filename(_sanitize_model(model)),
        )
    )
    if tag:
        name = f"{name}__{safe_filename(tag)}"
    return path_under(outputs_dir, name)


def artifact_paths(prefix: Path) -> ArtifactPaths:
    """Map a TADS ``-o`` prefix to the JSON and Markdown files it creates.

    Matches ``tads.cli._output_paths`` for prefixes that are not already
    ``.json`` / ``.md``: concatenate the suffix rather than using
    ``Path.with_suffix``, which would replace a dotted model or tag.
    """
    if prefix.suffix.lower() in {".json", ".md"}:
        stem = prefix.with_suffix("")
    else:
        stem = prefix
    return ArtifactPaths(json_path=Path(f"{stem}.json"), md_path=Path(f"{stem}.md"))


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


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _job_label(job: BenchJob) -> str:
    return f"{job.doc_id} ({job.provider}/{job.model})"


def _metadata_mismatch(job: BenchJob, report: Report) -> str:
    """Return a field name when a recorded identity value disagrees with the job."""
    if report.document.doc_id != job.doc_id:
        return "doc_id"
    if report.document.corpus != job.corpus:
        return "corpus"
    if report.run.provider is not None and report.run.provider != job.provider:
        return "provider"
    if report.run.model is not None and report.run.model != job.model:
        return "model"
    return ""


def classify_existing(job: BenchJob) -> ExistingDecision:
    """Decide skip-existing handling from canonical JSON at the job prefix.

    A ``.raw.txt`` file, Markdown-only output, a bare prefix, or a symlink is
    never treated as a completed paid scan.
    """
    paths = artifact_paths(job.output_prefix)
    json_ok = _is_regular_file(paths.json_path)
    md_ok = _is_regular_file(paths.md_path)
    if paths.json_path.is_symlink() or (
        paths.json_path.exists() and not json_ok
    ):
        return ExistingDecision("invalid", "invalid JSON artifact")
    if not json_ok:
        if md_ok or paths.md_path.is_symlink() or paths.md_path.exists():
            return ExistingDecision("incomplete", "missing JSON")
        return ExistingDecision("run")
    try:
        report = load_report_json(paths.json_path)
    except JsonLoadError:
        return ExistingDecision("invalid", "invalid report")
    except Exception:  # noqa: BLE001 - never expose loader internals
        return ExistingDecision("invalid", "invalid report")
    mismatch = _metadata_mismatch(job, report)
    if mismatch:
        return ExistingDecision("mismatch", f"metadata mismatch ({mismatch})")
    if paths.md_path.is_symlink() or (paths.md_path.exists() and not md_ok):
        return ExistingDecision("invalid", "invalid Markdown artifact")
    if not md_ok:
        return ExistingDecision("render")
    return ExistingDecision("skip")


def _render_markdown(job: BenchJob) -> None:
    paths = artifact_paths(job.output_prefix)
    if paths.md_path.is_symlink() or (
        paths.md_path.exists() and not _is_regular_file(paths.md_path)
    ):
        raise OSError("invalid Markdown artifact")
    report = load_report_json(paths.json_path)
    write_report_markdown(report, paths.md_path)


def _stop_child(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=_CHILD_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=_CHILD_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _popen(cmd: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd)


def _run_scan_command(cmd: list[str]) -> int:
    proc = _popen(cmd)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        _stop_child(proc)
        raise


def _record_artifact_error(
    totals: RunTotals,
    *,
    index: int,
    total: int,
    job: BenchJob,
    decision: ExistingDecision,
    continue_on_error: bool,
) -> bool:
    """Record a skip-existing artifact failure. Return True to keep going."""
    label = f"[{index}/{total}] {_job_label(job)}"
    msg = f"{label}: skip-existing {decision.detail}"
    totals.artifact_errors += 1
    totals.failures.append(msg)
    print(msg, file=sys.stderr)
    return continue_on_error


def _print_summary(totals: RunTotals, *, interrupted: bool, outputs_dir: Path) -> None:
    parts = [
        f"{totals.executed} executed",
        f"{totals.skipped} skipped",
        f"{totals.rendered} rendered",
        f"{totals.failed} failed",
        f"{totals.artifact_errors} incomplete/invalid",
    ]
    if interrupted or totals.interrupted:
        parts.append("interrupted")
    print("Finished: " + ", ".join(parts) + ".")
    if totals.failures:
        for item in totals.failures:
            print(f"  • {item}", file=sys.stderr)
    if totals.executed or totals.skipped or totals.rendered:
        print(f"Reports under {outputs_dir.resolve()}/")


def run_jobs(
    jobs: list[BenchJob],
    *,
    tads: str,
    skip_existing: bool,
    continue_on_error: bool,
    plan_only: bool,
    dry_run: bool,
    max_cost_usd: float | None,
    max_sections: int | None,
    max_input_tokens: int | None,
    force_sections: bool,
    max_output_tokens: int | None,
    outputs_dir: Path,
) -> int:
    totals = RunTotals()
    interrupted = False
    for index, job in enumerate(jobs, start=1):
        label = f"[{index}/{len(jobs)}] {_job_label(job)}"
        if skip_existing and not plan_only:
            decision = classify_existing(job)
            if decision.action == "skip":
                print(f"{label} skip existing {artifact_paths(job.output_prefix).json_path}")
                totals.skipped += 1
                continue
            if decision.action == "render":
                print(f"{label} regenerate Markdown from existing JSON")
                if dry_run:
                    totals.rendered += 1
                    continue
                try:
                    _render_markdown(job)
                except KeyboardInterrupt:
                    interrupted = True
                    totals.interrupted = 1
                    print("Interrupted.", file=sys.stderr)
                    break
                except Exception:  # noqa: BLE001 - controlled render failure
                    if not _record_artifact_error(
                        totals,
                        index=index,
                        total=len(jobs),
                        job=job,
                        decision=ExistingDecision("invalid", "Markdown render failed"),
                        continue_on_error=continue_on_error,
                    ):
                        break
                    continue
                totals.rendered += 1
                continue
            if decision.action in {
                "incomplete",
                "invalid",
                "mismatch",
            }:
                if not _record_artifact_error(
                    totals,
                    index=index,
                    total=len(jobs),
                    job=job,
                    decision=decision,
                    continue_on_error=continue_on_error,
                ):
                    break
                continue

        cmd = _scan_command(
            tads,
            job,
            plan_only=plan_only,
            max_cost_usd=max_cost_usd,
            max_sections=max_sections,
            max_input_tokens=max_input_tokens,
            force_sections=force_sections,
            max_output_tokens=max_output_tokens,
        )
        print(f"\n{label}")
        print("  " + shlex.join(cmd))
        if dry_run:
            totals.executed += 1
            continue
        try:
            returncode = _run_scan_command(cmd)
        except KeyboardInterrupt:
            interrupted = True
            totals.interrupted = 1
            print("Interrupted.", file=sys.stderr)
            break
        if returncode != 0:
            msg = f"{label} failed (exit {returncode})"
            totals.failed += 1
            totals.failures.append(msg)
            print(msg, file=sys.stderr)
            if not continue_on_error:
                break
            continue
        totals.executed += 1

    print()
    if dry_run and not interrupted:
        print(f"Dry run complete ({len(jobs)} commands).")
        return 0
    _print_summary(totals, interrupted=interrupted, outputs_dir=outputs_dir)
    if interrupted:
        return _INTERRUPT_EXIT
    if totals.failures:
        return 1
    return 0


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
        help=(
            "Resume from a valid canonical JSON report at <prefix>.json. "
            "Skip when matching JSON and Markdown both exist. Regenerate "
            "Markdown from JSON when Markdown is missing (no provider call). "
            "Fail closed on Markdown-only, malformed, loader-rejected, or "
            "mismatched artifacts without overwriting or scanning. Ctrl+C "
            "stops the active child scan and exits 130."
        ),
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
    try:
        return run_jobs(
            jobs,
            tads=tads,
            skip_existing=args.skip_existing,
            continue_on_error=args.continue_on_error,
            plan_only=args.plan_only,
            dry_run=args.dry_run,
            max_cost_usd=args.max_cost_usd,
            max_sections=args.max_sections,
            max_input_tokens=args.max_input_tokens,
            force_sections=args.force_sections,
            max_output_tokens=args.max_output_tokens,
            outputs_dir=args.outputs_dir,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return _INTERRUPT_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
