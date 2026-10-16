#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Run the 12-doc benchmark across selected provider/model configurations.

Default matrix (historical): OpenAI gpt-4.1-mini and Gemini gemini-3.6-flash.

Writes reports under outputs/ using collision-safe prefixes:
  outputs/<doc_id>__<provider>__<model>[__re-<effort>][__controls…][__tag]

``tads scan -o <prefix>`` is an output prefix. Successful scans write
``<prefix>.json`` and ``<prefix>.md`` by concatenating those suffixes
(not by replacing a pathlib suffix). Prefixes often contain dots
(``gemini-3.6-flash``, ``0.6.0rc3-…``).

Usage:
  scripts/run_bench_scans.py --dry-run
  scripts/run_bench_scans.py --plan-only
  scripts/run_bench_scans.py --max-cost-usd 2.00
  scripts/run_bench_scans.py --skip-existing --tag bench2026
  scripts/run_bench_scans.py --config openai-mini --config openai-luna-none \\
      --config openai-luna-low --config gemini-flash --force-sections \\
      --max-sections-per-request 8 --tag chunked-cmp
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Shared helpers + canonical 12-doc list.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from bench_common import (  # noqa: E402
    AggregateMetrics,
    BenchConfigError,
    BenchControls,
    BenchModelConfig,
    DEFAULT_RUN_CONFIGS,
    ManifestCompatibilityError,
    ManifestTargetError,
    add_model_selection_args,
    add_shared_control_args,
    assert_manifest_compatible,
    atomic_write_json,
    configs_from_args,
    controls_from_args,
    git_provenance,
    manifest_path,
    merge_job_entries,
    metadata_mismatch,
    new_manifest,
    output_prefix,
    report_comparison_fields,
    run_definition,
    scan_argv_for_config,
    validate_controls,
)
from plan_bench_costs import BENCH_DOCS  # noqa: E402

from tads.export import load_report_json, write_report_markdown  # noqa: E402
from tads.jsonutil import JsonLoadError  # noqa: E402
from tads.schemas.report import Report  # noqa: E402

_CHILD_STOP_SECONDS = 5.0
_INTERRUPT_EXIT = 130


@dataclass(frozen=True)
class BenchJob:
    bench_id: str
    doc_id: str
    corpus: str
    source: Path
    config: BenchModelConfig
    controls: BenchControls
    output_prefix: Path

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def reasoning_effort(self) -> Optional[str]:
        return self.config.reasoning_effort

    @property
    def config_id(self) -> str:
        return self.config.config_id


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
    metrics: AggregateMetrics | None = None
    failures: list[str] = field(default_factory=list)


# Back-compat alias for tests that still import BENCH_MODELS.
BENCH_MODELS: list[tuple[str, str]] = [
    (c.provider, c.model) for c in DEFAULT_RUN_CONFIGS
]


def _output_prefix(
    outputs_dir: Path,
    doc_id: str,
    provider: str,
    model: str,
    tag: str | None,
    *,
    config: BenchModelConfig | None = None,
    controls: BenchControls | None = None,
) -> Path:
    """Legacy helper signature used by older tests; prefers full config/controls."""
    cfg = config or BenchModelConfig(
        config_id=f"{provider}-{model}",
        provider=provider,
        model=model,
    )
    ctrl = controls or BenchControls()
    return output_prefix(outputs_dir, doc_id, cfg, ctrl, tag)


def artifact_paths(prefix: Path) -> ArtifactPaths:
    """Map a TADS ``-o`` prefix to the JSON and Markdown files it creates."""
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
    configs: list[BenchModelConfig],
    controls: BenchControls,
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
        for config in configs:
            jobs.append(
                BenchJob(
                    bench_id=doc["bench_id"],
                    doc_id=doc["doc_id"],
                    corpus=doc["corpus"],
                    source=source,
                    config=config,
                    controls=controls,
                    output_prefix=output_prefix(
                        outputs_dir, doc["doc_id"], config, controls, tag
                    ),
                )
            )
    return jobs


def _scan_command(
    tads: str,
    job: BenchJob,
    *,
    plan_only: bool,
) -> list[str]:
    return scan_argv_for_config(
        tads=tads,
        source=job.source,
        doc_id=job.doc_id,
        corpus=job.corpus,
        config=job.config,
        output_prefix_path=job.output_prefix,
        controls=job.controls,
        plan_only=plan_only,
    )


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _job_label(job: BenchJob) -> str:
    effort = job.reasoning_effort
    extra = f" effort={effort}" if effort is not None else ""
    return f"{job.doc_id} ({job.config_id}: {job.provider}/{job.model}{extra})"


def _metadata_mismatch(job: BenchJob, report: Report) -> str:
    return metadata_mismatch(
        doc_id=job.doc_id,
        corpus=job.corpus,
        config=job.config,
        controls=job.controls,
        report=report,
    )


def classify_existing(job: BenchJob) -> ExistingDecision:
    """Decide skip-existing handling from canonical JSON at the job prefix."""
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
    label = f"[{index}/{total}] {_job_label(job)}"
    msg = f"{label}: skip-existing {decision.detail}"
    totals.artifact_errors += 1
    totals.failures.append(msg)
    print(msg, file=sys.stderr)
    return continue_on_error


def _try_load_report(job: BenchJob) -> Report | None:
    paths = artifact_paths(job.output_prefix)
    if not _is_regular_file(paths.json_path):
        return None
    try:
        return load_report_json(paths.json_path)
    except Exception:  # noqa: BLE001
        return None


def _job_manifest_entry(
    job: BenchJob,
    *,
    status: str,
    elapsed_seconds: float | None = None,
    detail: str = "",
    preserve: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "bench_id": job.bench_id,
        "doc_id": job.doc_id,
        "corpus": job.corpus,
        "config_id": job.config_id,
        "provider": job.provider,
        "model": job.model,
        "reasoning_effort": job.reasoning_effort,
        "output_prefix": str(job.output_prefix),
        "status": status,
        "detail": detail or None,
        "elapsed_seconds": elapsed_seconds,
        "report": None,
    }
    if status == "skipped" and preserve and preserve.get("status") == "executed":
        # Resume skip: keep original executed provenance; terminal still counts skip.
        entry["status"] = "executed"
        if preserve.get("elapsed_seconds") is not None:
            entry["elapsed_seconds"] = preserve.get("elapsed_seconds")
        if preserve.get("report") is not None:
            entry["report"] = preserve.get("report")
            return entry
    if status in {"executed", "skipped", "rendered"}:
        report = _try_load_report(job)
        if report is not None:
            entry["report"] = report_comparison_fields(report)
    return entry


def _write_manifest(
    path: Path,
    *,
    tag: str | None,
    configs: list[BenchModelConfig],
    controls: BenchControls,
    job_entries: list[dict[str, Any]],
    git_commit: Any,
    git_dirty: Any,
    created_at_utc: str | None,
) -> None:
    payload = new_manifest(
        tag=tag,
        documents=BENCH_DOCS,
        configs=configs,
        controls=controls,
        jobs=job_entries,
        git_commit=git_commit,
        git_dirty=git_dirty,
        created_at_utc=created_at_utc,
    )
    atomic_write_json(path, payload)


def _print_summary(
    totals: RunTotals,
    *,
    interrupted: bool,
    outputs_dir: Path,
    manifest: Path | None,
) -> None:
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
    if totals.metrics is not None:
        print(totals.metrics.format_line())
    if totals.failures:
        for item in totals.failures:
            print(f"  • {item}", file=sys.stderr)
    if totals.executed or totals.skipped or totals.rendered:
        print(f"Reports under {outputs_dir.resolve()}/")
    if manifest is not None:
        print(f"Manifest: {manifest}")

def run_jobs(
    jobs: list[BenchJob],
    *,
    tads: str,
    skip_existing: bool,
    continue_on_error: bool,
    plan_only: bool,
    dry_run: bool,
    outputs_dir: Path,
    tag: str | None = None,
    configs: list[BenchModelConfig] | None = None,
    controls: BenchControls | None = None,
    write_manifest: bool = True,
    # Legacy keyword args accepted for older tests:
    max_cost_usd: float | None = None,
    max_sections: int | None = None,
    max_input_tokens: int | None = None,
    force_sections: bool = False,
    max_output_tokens: int | None = None,
) -> int:
    if controls is None:
        controls = BenchControls(
            force_sections=force_sections,
            section_chunking=True,
            max_sections_per_request=None,
            max_output_tokens=max_output_tokens,
            max_cost_usd=max_cost_usd,
            max_sections=max_sections,
            max_input_tokens=max_input_tokens,
        )
    if configs is None:
        seen: dict[str, BenchModelConfig] = {}
        for job in jobs:
            seen[job.config_id] = job.config
        configs = list(seen.values())

    totals = RunTotals(metrics=AggregateMetrics(total_jobs=len(jobs)))
    interrupted = False
    manifest_file = manifest_path(outputs_dir, tag) if write_manifest else None

    prov = git_provenance()
    expected_def = run_definition(
        tag=tag,
        documents=BENCH_DOCS,
        configs=configs,
        controls=controls,
        git_commit=prov["git_commit"],
        git_dirty=prov["git_dirty"],
    )
    prior_payload: dict[str, Any] | None = None
    if manifest_file is not None and not dry_run:
        try:
            prior_payload = assert_manifest_compatible(
                manifest_file, expected=expected_def
            )
        except ManifestCompatibilityError as exc:
            print(f"Manifest error: {exc}", file=sys.stderr)
            return 2
        except ManifestTargetError as exc:
            print(f"Manifest error: {exc}", file=sys.stderr)
            return 2

    created_at = (
        prior_payload.get("created_at_utc")
        if isinstance(prior_payload, dict)
        else None
    )
    planned = [_job_manifest_entry(job, status="pending") for job in jobs]
    prior_jobs = (
        prior_payload.get("jobs") if isinstance(prior_payload, dict) else None
    )
    job_entries = merge_job_entries(
        prior_jobs if isinstance(prior_jobs, list) else None,
        planned,
    )

    def _persist() -> None:
        if manifest_file is None or dry_run:
            return
        try:
            _write_manifest(
                manifest_file,
                tag=tag,
                configs=configs or [],
                controls=controls,
                job_entries=job_entries,
                git_commit=prov["git_commit"],
                git_dirty=prov["git_dirty"],
                created_at_utc=created_at if isinstance(created_at, str) else None,
            )
        except (OSError, ManifestTargetError) as exc:
            print(f"Warning: could not write manifest: {exc}", file=sys.stderr)

    _persist()

    def _note_report(report: Report | None, *, countable: bool) -> None:
        if totals.metrics is None:
            return
        fields = report_comparison_fields(report) if report is not None else None
        totals.metrics.add_report_fields(fields, include=countable)

    for index, job in enumerate(jobs, start=1):
        label = f"[{index}/{len(jobs)}] {_job_label(job)}"
        prior_row = job_entries[index - 1]
        if skip_existing and not plan_only:
            decision = classify_existing(job)
            if decision.action == "skip":
                print(
                    f"{label} skip existing "
                    f"{artifact_paths(job.output_prefix).json_path}"
                )
                totals.skipped += 1
                report = _try_load_report(job)
                _note_report(report, countable=True)
                job_entries[index - 1] = _job_manifest_entry(
                    job, status="skipped", preserve=prior_row
                )
                _persist()
                continue
            if decision.action == "render":
                print(f"{label} regenerate Markdown from existing JSON")
                if dry_run:
                    totals.rendered += 1
                    job_entries[index - 1] = _job_manifest_entry(
                        job, status="rendered"
                    )
                    continue
                try:
                    _render_markdown(job)
                except KeyboardInterrupt:
                    interrupted = True
                    totals.interrupted = 1
                    job_entries[index - 1] = _job_manifest_entry(
                        job, status="interrupted", detail="render interrupted"
                    )
                    _persist()
                    print("Interrupted.", file=sys.stderr)
                    break
                except Exception:  # noqa: BLE001 - controlled render failure
                    if not _record_artifact_error(
                        totals,
                        index=index,
                        total=len(jobs),
                        job=job,
                        decision=ExistingDecision(
                            "invalid", "Markdown render failed"
                        ),
                        continue_on_error=continue_on_error,
                    ):
                        job_entries[index - 1] = _job_manifest_entry(
                            job, status="failed", detail="Markdown render failed"
                        )
                        _persist()
                        break
                    job_entries[index - 1] = _job_manifest_entry(
                        job, status="failed", detail="Markdown render failed"
                    )
                    _persist()
                    continue
                totals.rendered += 1
                report = _try_load_report(job)
                _note_report(report, countable=True)
                job_entries[index - 1] = _job_manifest_entry(job, status="rendered")
                _persist()
                continue
            if decision.action in {
                "incomplete",
                "invalid",
                "mismatch",
            }:
                job_entries[index - 1] = _job_manifest_entry(
                    job, status=decision.action, detail=decision.detail
                )
                _persist()
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

        cmd = _scan_command(tads, job, plan_only=plan_only)
        print(f"\n{label}")
        print("  " + shlex.join(cmd))
        if dry_run:
            totals.executed += 1
            job_entries[index - 1] = _job_manifest_entry(job, status="executed")
            continue
        started = time.monotonic()
        try:
            returncode = _run_scan_command(cmd)
        except KeyboardInterrupt:
            interrupted = True
            totals.interrupted = 1
            job_entries[index - 1] = _job_manifest_entry(
                job,
                status="interrupted",
                elapsed_seconds=time.monotonic() - started,
            )
            _persist()
            print("Interrupted.", file=sys.stderr)
            break
        elapsed = time.monotonic() - started
        if returncode != 0:
            msg = f"{label} failed (exit {returncode})"
            totals.failed += 1
            totals.failures.append(msg)
            print(msg, file=sys.stderr)
            # Failed after a provider request: cost completeness remains false.
            job_entries[index - 1] = _job_manifest_entry(
                job,
                status="failed",
                elapsed_seconds=elapsed,
                detail=f"exit {returncode}",
            )
            _persist()
            if not continue_on_error:
                break
            continue
        totals.executed += 1
        report = _try_load_report(job)
        _note_report(report, countable=True)
        job_entries[index - 1] = _job_manifest_entry(
            job, status="executed", elapsed_seconds=elapsed
        )
        _persist()

    print()
    if dry_run and not interrupted:
        print(f"Dry run complete ({len(jobs)} commands).")
        return 0
    _print_summary(
        totals,
        interrupted=interrupted,
        outputs_dir=outputs_dir,
        manifest=manifest_file if not dry_run else None,
    )
    if interrupted:
        return _INTERRUPT_EXIT
    if totals.failures:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the 12-doc benchmark. Default models: gpt-4.1-mini and "
            "gemini-3.6-flash. Use --config / --model for Luna and other providers."
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
    add_model_selection_args(parser)
    add_shared_control_args(parser)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going after a failed job (default: stop on first failure)",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not write the bench_manifest JSON summary",
    )
    args = parser.parse_args()

    try:
        configs = configs_from_args(args, defaults=DEFAULT_RUN_CONFIGS)
        controls = controls_from_args(args)
        validate_controls(controls)
    except BenchConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    tads = _tads_bin()
    try:
        jobs = _build_jobs(
            inputs_dir=args.inputs_dir,
            outputs_dir=args.outputs_dir,
            tag=args.tag,
            configs=configs,
            controls=controls,
        )
    except BenchConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    mode = "plan" if args.plan_only else "scan"
    print(
        f"{mode}: {len(BENCH_DOCS)} documents × {len(configs)} configurations "
        f"= {len(jobs)} jobs"
    )
    for config in configs:
        effort = (
            f" reasoning_effort={config.reasoning_effort}"
            if config.reasoning_effort is not None
            else ""
        )
        print(f"  • {config.config_id}: {config.provider}/{config.model}{effort}")

    args.outputs_dir.mkdir(parents=True, exist_ok=True)
    try:
        return run_jobs(
            jobs,
            tads=tads,
            skip_existing=args.skip_existing,
            continue_on_error=args.continue_on_error,
            plan_only=args.plan_only,
            dry_run=args.dry_run,
            outputs_dir=args.outputs_dir,
            tag=args.tag,
            configs=configs,
            controls=controls,
            write_manifest=not args.no_manifest,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return _INTERRUPT_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
