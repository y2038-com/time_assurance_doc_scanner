#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0
"""Shared benchmark model configuration, identity, and manifest helpers.

Used by ``run_bench_scans.py`` and ``plan_bench_costs.py``. No provider HTTP.

Runner and planner keep separate historical default matrices; helpers are shared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from tads.jsonutil import JsonLoadError, loads as json_loads_bounded
from tads.llm.openai_models import (
    InvalidReasoningEffortError,
    openai_model_policy,
    recorded_reasoning_effort,
)
from tads.parsing.chunks import DEFAULT_MAX_SECTIONS_PER_REQUEST
from tads.path_safety import path_under, safe_filename
from tads.schemas.report import RequestBatching, Report

MANIFEST_FORMAT_VERSION = 1
DEFAULT_MAX_OUTPUT_TOKENS = 16384
_TAG_DIGEST_LENGTH = 12
_TAG_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONFIG_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

NAMED_CONFIGS: dict[str, "BenchModelConfig"] = {}


@dataclass(frozen=True)
class BenchModelConfig:
    """One reproducible provider/model[/reasoning] configuration."""

    config_id: str
    provider: str
    model: str
    reasoning_effort: Optional[str] = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class BenchControls:
    """Run-level scan/plan controls shared across configurations."""

    force_sections: bool = False
    section_chunking: bool = True
    max_sections_per_request: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_sections: Optional[int] = None
    max_input_tokens: Optional[int] = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "force_sections": self.force_sections,
            "section_chunking": self.section_chunking,
            "max_sections_per_request": self.max_sections_per_request,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_sections": self.max_sections,
            "max_input_tokens": self.max_input_tokens,
        }


def _register_named(
    config_id: str,
    provider: str,
    model: str,
    reasoning_effort: Optional[str] = None,
) -> BenchModelConfig:
    cfg = BenchModelConfig(
        config_id=config_id,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    NAMED_CONFIGS[config_id] = cfg
    return cfg


OPENAI_MINI = _register_named("openai-mini", "openai", "gpt-4.1-mini")
GEMINI_FLASH = _register_named("gemini-flash", "gemini", "gemini-3.6-flash")
OPENAI_LUNA_NONE = _register_named(
    "openai-luna-none", "openai", "gpt-6-luna", "none"
)
OPENAI_LUNA_LOW = _register_named(
    "openai-luna-low", "openai", "gpt-6-luna", "low"
)
OLLAMA_GPT_OSS = _register_named("ollama-gpt-oss-120b", "ollama", "gpt-oss:120b")
ANTHROPIC_SONNET = _register_named(
    "anthropic-claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"
)

# Historical runner default (bake-off pair).
DEFAULT_RUN_CONFIGS: tuple[BenchModelConfig, ...] = (OPENAI_MINI, GEMINI_FLASH)

# Historical planner default (pre-branch four-model matrix and order).
DEFAULT_PLAN_CONFIGS: tuple[BenchModelConfig, ...] = (
    OLLAMA_GPT_OSS,
    OPENAI_MINI,
    ANTHROPIC_SONNET,
    GEMINI_FLASH,
)

# Alias retained for older imports; means the runner historical default.
DEFAULT_BENCH_CONFIGS = DEFAULT_RUN_CONFIGS


class BenchConfigError(ValueError):
    """Malformed or unsupported benchmark configuration."""


class ManifestCompatibilityError(ValueError):
    """Existing same-tag manifest is incompatible with the requested run."""


class ManifestTargetError(ValueError):
    """Manifest path is not a missing path or regular file."""


def sanitize_model_token(model: str) -> str:
    """Filesystem-safe model token (``:`` → ``-``), matching historical runner."""
    return model.replace(":", "-")


def derive_config_id(
    provider: str,
    model: str,
    reasoning_effort: Optional[str] = None,
) -> str:
    """Stable slug for an ad-hoc provider/model[/effort] combination."""
    base = f"{provider}-{sanitize_model_token(model)}"
    if reasoning_effort is not None:
        base = f"{base}-re-{reasoning_effort}"
    return base


def validate_config_id(config_id: str) -> str:
    token = (config_id or "").strip()
    if not token:
        raise BenchConfigError("configuration id must be nonempty")
    if "/" in token or "\\" in token or ".." in token:
        raise BenchConfigError(f"unsafe configuration id: {config_id!r}")
    if not _CONFIG_ID_RE.fullmatch(token):
        raise BenchConfigError(
            f"invalid configuration id {config_id!r}; "
            "use letters, digits, dot, underscore, or hyphen"
        )
    return token


def normalize_reasoning_effort(
    provider: str,
    model: str,
    requested: Optional[str],
) -> Optional[str]:
    """Normalize/validate effort using TADS model policy (no HTTP)."""
    try:
        return recorded_reasoning_effort(
            provider_id=provider,
            model=model,
            requested=requested,
        )
    except InvalidReasoningEffortError as exc:
        raise BenchConfigError(str(exc)) from None


def parse_model_spec(spec: str) -> BenchModelConfig:
    """Parse ``provider/model`` or ``provider/model/effort``.

    Uses ``/`` so Ollama model ids that contain ``:`` remain intact.
    Model identifiers containing ``/`` are unsupported by this grammar.
    """
    raw = (spec or "").strip()
    if not raw:
        raise BenchConfigError("empty --model specification")
    if raw.startswith("/") or raw.endswith("/"):
        raise BenchConfigError(
            f"invalid --model {spec!r}; leading/trailing '/' is not allowed"
        )
    parts = raw.split("/")
    if len(parts) > 3:
        raise BenchConfigError(
            f"invalid --model {spec!r}; model IDs containing '/' are not "
            "supported by --model (use provider/model or provider/model/effort)"
        )
    if len(parts) not in {2, 3}:
        raise BenchConfigError(
            f"invalid --model {spec!r}; expected provider/model "
            "or provider/model/effort"
        )
    provider = parts[0].strip().lower()
    model = parts[1].strip()
    effort_raw = parts[2].strip() if len(parts) == 3 else None
    if not provider or not model:
        raise BenchConfigError(
            f"invalid --model {spec!r}; provider and model must be nonempty"
        )
    if any(sep in provider or sep in model for sep in ("\\",)):
        raise BenchConfigError(f"unsafe --model {spec!r}")
    if ".." in provider or ".." in model:
        raise BenchConfigError(f"unsafe --model {spec!r}")
    if effort_raw is not None and not effort_raw:
        raise BenchConfigError(f"invalid --model {spec!r}; empty effort")
    if effort_raw is not None:
        effort = normalize_reasoning_effort(provider, model, effort_raw)
    else:
        if provider == "openai" and openai_model_policy(model).supports_reasoning_effort:
            raise BenchConfigError(
                f"model {model!r} requires explicit effort in --model "
                "as provider/model/effort (e.g. openai/gpt-6-luna/none)"
            )
        normalize_reasoning_effort(provider, model, None)
        effort = None
    config_id = validate_config_id(derive_config_id(provider, model, effort))
    return BenchModelConfig(
        config_id=config_id,
        provider=provider,
        model=model,
        reasoning_effort=effort,
    )


def resolve_named_config(name: str) -> BenchModelConfig:
    key = (name or "").strip()
    if key not in NAMED_CONFIGS:
        known = ", ".join(sorted(NAMED_CONFIGS))
        raise BenchConfigError(
            f"unknown --config {name!r}; known names: {known}"
        )
    return NAMED_CONFIGS[key]


def resolve_bench_configs(
    *,
    config_names: Sequence[str] | None = None,
    model_specs: Sequence[str] | None = None,
    defaults: Sequence[BenchModelConfig] | None = None,
) -> list[BenchModelConfig]:
    """Resolve named presets and/or exact model specs.

    When both selection lists are empty, return ``defaults`` (caller-specific
    historical matrix).
    """
    names = list(config_names or ())
    specs = list(model_specs or ())
    if not names and not specs:
        return list(defaults if defaults is not None else DEFAULT_RUN_CONFIGS)

    configs: list[BenchModelConfig] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str, Optional[str]]] = set()

    def _add(cfg: BenchModelConfig) -> None:
        key = (cfg.provider, cfg.model, cfg.reasoning_effort)
        if cfg.config_id in seen_ids:
            raise BenchConfigError(
                f"duplicate configuration id {cfg.config_id!r}"
            )
        if key in seen_keys:
            raise BenchConfigError(
                f"duplicate configuration {cfg.provider}/{cfg.model}"
                + (
                    f"/{cfg.reasoning_effort}"
                    if cfg.reasoning_effort is not None
                    else ""
                )
            )
        seen_ids.add(cfg.config_id)
        seen_keys.add(key)
        configs.append(cfg)

    for name in names:
        _add(resolve_named_config(name))
    for spec in specs:
        _add(parse_model_spec(spec))
    return configs


def validate_controls(controls: BenchControls) -> None:
    if controls.max_sections_per_request is not None:
        if controls.max_sections_per_request < 1:
            raise BenchConfigError("max_sections_per_request must be >= 1")
    if controls.max_output_tokens is not None:
        if controls.max_output_tokens < 1:
            raise BenchConfigError("max_output_tokens must be >= 1")
    if controls.max_sections is not None and controls.max_sections < 1:
        raise BenchConfigError("max_sections must be >= 1")
    if controls.max_input_tokens is not None and controls.max_input_tokens < 1:
        raise BenchConfigError("max_input_tokens must be >= 1")
    if controls.max_cost_usd is not None and controls.max_cost_usd < 0:
        raise BenchConfigError("max_cost_usd must be >= 0")


def control_identity_parts(controls: BenchControls) -> list[str]:
    """Slug fragments for nondefault/explicit execution controls."""
    parts: list[str] = []
    if controls.force_sections:
        parts.append("fs")
    if not controls.section_chunking:
        parts.append("nc")
    if controls.max_sections_per_request is not None:
        parts.append(f"mspr-{int(controls.max_sections_per_request)}")
    if controls.max_output_tokens is not None:
        parts.append(f"mot-{int(controls.max_output_tokens)}")
    return parts


def tag_slug(tag: str) -> str:
    """Collision-resistant single-component slug for a benchmark tag.

    Already-safe tags (letters/digits/``.``/``_``/``-``, historical dotted
    release-style names) are preserved unchanged. Lossy normalization appends
    a short SHA-256 digest of the *original* tag so ``foo bar`` and ``foo_bar``
    never collide.
    """
    original = tag if isinstance(tag, str) else str(tag)
    if "\x00" in original or any(ord(ch) < 32 for ch in original):
        raise BenchConfigError(f"unsafe --tag {tag!r}; control characters forbidden")
    if "/" in original or "\\" in original:
        # Still slug safely with digest; do not allow path syntax in the slug.
        pass
    if _TAG_SAFE.fullmatch(original) and original not in {".", ".."}:
        # Already historically safe (e.g. bench2026, 0.6.0rc4-bench).
        if len(original) <= 80:
            return original
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:_TAG_DIGEST_LENGTH]
    # Build a readable stem without claiming the space→underscore identity.
    stem_source = original.replace(" ", "_").replace("/", "_").replace("\\", "_")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem_source).strip("._-")
    if not stem or stem in {".", ".."} or set(stem) <= {"_", ".", "-"}:
        stem = "tag"
    max_stem = 80 - (1 + _TAG_DIGEST_LENGTH)
    if len(stem) > max_stem:
        stem = stem[:max_stem].rstrip("._-") or "tag"
    return f"{stem}_{digest}"


def output_prefix(
    outputs_dir: Path,
    doc_id: str,
    config: BenchModelConfig,
    controls: BenchControls,
    tag: str | None,
) -> Path:
    """Collision-safe ``-o`` prefix under ``outputs_dir``."""
    parts = [
        safe_filename(doc_id),
        safe_filename(config.provider),
        safe_filename(sanitize_model_token(config.model)),
    ]
    if config.reasoning_effort is not None:
        parts.append(safe_filename(f"re-{config.reasoning_effort}"))
    parts.extend(control_identity_parts(controls))
    if tag is not None:
        if tag == "":
            raise BenchConfigError("unsafe or empty --tag ''")
        parts.append(tag_slug(tag))
    name = "__".join(parts)
    if not name or name in {".", ".."}:
        raise BenchConfigError("empty output prefix slug")
    return path_under(outputs_dir, name)


def add_model_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Named configuration (repeatable). Known: "
            + ", ".join(sorted(NAMED_CONFIGS))
            + ". Script-specific historical defaults apply when omitted with --model."
        ),
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="SPEC",
        dest="model_specs",
        help=(
            "Exact provider/model or provider/model/effort (repeatable). "
            "Example: anthropic/claude-sonnet-4-5 or ollama/gpt-oss:120b. "
            "Model IDs may contain ':' but not '/'."
        ),
    )


def add_shared_control_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force-sections",
        action="store_true",
        help="Force section-aware analysis for every document",
    )
    parser.add_argument(
        "--no-section-chunking",
        action="store_true",
        help="One primary request per section (pass through to tads)",
    )
    parser.add_argument(
        "--max-sections-per-request",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Max sections packed per primary request "
            f"(tads default {DEFAULT_MAX_SECTIONS_PER_REQUEST} when omitted)"
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        metavar="N",
        help="Per-completion output cap (tads default when omitted)",
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


def controls_from_args(args: argparse.Namespace) -> BenchControls:
    return BenchControls(
        force_sections=bool(getattr(args, "force_sections", False)),
        section_chunking=not bool(getattr(args, "no_section_chunking", False)),
        max_sections_per_request=getattr(args, "max_sections_per_request", None),
        max_output_tokens=getattr(args, "max_output_tokens", None),
        max_cost_usd=getattr(args, "max_cost_usd", None),
        max_sections=getattr(args, "max_sections", None),
        max_input_tokens=getattr(args, "max_input_tokens", None),
    )


def configs_from_args(
    args: argparse.Namespace,
    *,
    defaults: Sequence[BenchModelConfig],
) -> list[BenchModelConfig]:
    return resolve_bench_configs(
        config_names=getattr(args, "config", None) or [],
        model_specs=getattr(args, "model_specs", None) or [],
        defaults=defaults,
    )


def expected_reasoning_for_match(config: BenchModelConfig) -> Optional[str]:
    """Value that ``run.reasoning_effort`` must equal for skip-existing."""
    if config.reasoning_effort is not None:
        return config.reasoning_effort.strip().casefold()
    return None


def metadata_mismatch(
    *,
    doc_id: str,
    corpus: str,
    config: BenchModelConfig,
    controls: BenchControls,
    report: Report,
) -> str:
    """Return a field name when recorded identity disagrees with the job."""
    if report.document.doc_id != doc_id:
        return "doc_id"
    if report.document.corpus != corpus:
        return "corpus"
    if report.run.provider is not None and report.run.provider != config.provider:
        return "provider"
    if report.run.model is not None and report.run.model != config.model:
        return "model"

    expected_effort = expected_reasoning_for_match(config)
    recorded = report.run.reasoning_effort
    if expected_effort is not None:
        if recorded is None or str(recorded).casefold() != expected_effort:
            return "reasoning_effort"
    else:
        if recorded is not None:
            return "reasoning_effort"

    batching = report.run.request_batching
    if batching is not None:
        if controls.force_sections and batching == RequestBatching.WHOLE_DOCUMENT:
            return "request_batching"
        if not controls.section_chunking and batching != RequestBatching.PER_SECTION:
            return "request_batching"
        if (
            controls.force_sections
            and controls.section_chunking
            and batching
            not in {RequestBatching.SECTION_CHUNKS, RequestBatching.PER_SECTION}
        ):
            return "request_batching"

    # Explicit max_output_tokens: verify only when the report records a value.
    # Legacy null relies on collision-safe prefix identity.
    if controls.max_output_tokens is not None:
        recorded_mot = report.run.max_output_tokens
        if recorded_mot is not None and recorded_mot != controls.max_output_tokens:
            return "max_output_tokens"

    return ""


def git_provenance(cwd: Optional[Path] = None) -> dict[str, Any]:
    """Best-effort git commit + dirty flag; never raises."""
    root = cwd or Path.cwd()
    commit: Optional[str] = None
    dirty: Optional[bool] = None
    try:
        commit_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if commit_proc.returncode == 0:
            commit = commit_proc.stdout.strip() or None
        dirty_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if dirty_proc.returncode == 0:
            dirty = bool(dirty_proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"git_commit": commit, "git_dirty": dirty}


def manifest_path(outputs_dir: Path, tag: str | None) -> Path:
    if tag is not None:
        if tag == "":
            raise BenchConfigError("unsafe or empty --tag ''")
        name = f"bench_manifest__{tag_slug(tag)}.json"
    else:
        name = "bench_manifest.json"
    return path_under(outputs_dir, name)


def ensure_manifest_target_writable(path: Path) -> None:
    """Require missing path or regular file; refuse symlink/dir/fifo/device."""
    try:
        if path.is_symlink():
            raise ManifestTargetError(
                "manifest path is a symlink; refusing to read or replace it"
            )
    except OSError as exc:
        raise ManifestTargetError("manifest path is not usable") from None
    if not path.exists():
        return
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ManifestTargetError("manifest path is not usable") from None
    if stat.S_ISLNK(mode):
        raise ManifestTargetError(
            "manifest path is a symlink; refusing to read or replace it"
        )
    if stat.S_ISDIR(mode):
        raise ManifestTargetError(
            "manifest path is a directory; refusing to read or replace it"
        )
    if stat.S_ISFIFO(mode):
        raise ManifestTargetError(
            "manifest path is a FIFO; refusing to read or replace it"
        )
    if stat.S_ISSOCK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        raise ManifestTargetError(
            "manifest path is not a regular file; refusing to read or replace it"
        )
    if not stat.S_ISREG(mode):
        raise ManifestTargetError(
            "manifest path is not a regular file; refusing to read or replace it"
        )


def report_comparison_fields(report: Report) -> dict[str, Any]:
    """Extract schema 0.5.0 comparison fields; null when unavailable."""
    run = report.run
    usage = report.actual_usage
    est = report.cost_estimate
    usage_payload: dict[str, Any] | None
    if usage is None:
        usage_payload = None
    else:
        usage_payload = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
        }
    return {
        "schema_version": report.schema_version,
        "provider": run.provider,
        "model": run.model,
        "reasoning_effort": run.reasoning_effort,
        "analysis_mode": run.analysis_mode.value if run.analysis_mode else None,
        "request_batching": (
            run.request_batching.value if run.request_batching else None
        ),
        "analysis_request_count": run.analysis_request_count,
        "repair_request_count": run.repair_request_count,
        "chunk_section_counts": (
            list(run.chunk_section_counts)
            if run.chunk_section_counts is not None
            else None
        ),
        "coverage_complete": run.coverage_complete,
        "finding_count": len(report.findings),
        "estimated_cost_usd": (
            est.estimated_cost_usd if est is not None else None
        ),
        "actual_usage": usage_payload,
        "actual_cost_usd": report.actual_cost_usd,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically (temp file + replace). Regular files only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_manifest_target_writable(path)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".bench-manifest-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None  # type: ignore[assignment]
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def document_defs(documents: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "bench_id": d.get("bench_id"),
            "doc_id": d.get("doc_id"),
            "corpus": d.get("corpus"),
            "source": d.get("source"),
        }
        for d in documents
    ]


def run_definition(
    *,
    tag: str | None,
    documents: Sequence[dict[str, str]],
    configs: Sequence[BenchModelConfig],
    controls: BenchControls,
    git_commit: Any,
    git_dirty: Any,
) -> dict[str, Any]:
    """Nonvolatile run definition used for same-tag compatibility."""
    return {
        "benchmark_tag": tag,
        "documents": document_defs(documents),
        "configurations": [c.to_public_dict() for c in configs],
        "controls": controls.to_public_dict(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
    }


def definition_from_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark_tag": payload.get("benchmark_tag"),
        "documents": payload.get("documents"),
        "configurations": payload.get("configurations"),
        "controls": payload.get("controls"),
        "git_commit": payload.get("git_commit"),
        "git_dirty": payload.get("git_dirty"),
    }


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a manifest with bounded JSON; require regular file."""
    ensure_manifest_target_writable(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestTargetError("manifest path is not readable") from None
    try:
        data = json_loads_bounded(text)
    except JsonLoadError as exc:
        raise ManifestCompatibilityError("existing manifest is not valid JSON") from None
    if not isinstance(data, dict):
        raise ManifestCompatibilityError("existing manifest root must be an object")
    if data.get("manifest_format_version") != MANIFEST_FORMAT_VERSION:
        raise ManifestCompatibilityError(
            "existing manifest format version is unsupported or missing"
        )
    for key in (
        "benchmark_tag",
        "documents",
        "configurations",
        "controls",
        "git_commit",
        "git_dirty",
        "jobs",
    ):
        if key not in data:
            raise ManifestCompatibilityError(
                f"existing manifest missing required field {key!r}"
            )
    return data


def assert_manifest_compatible(
    path: Path,
    *,
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    """Return prior payload when compatible; None if missing; raise on mismatch.

    Never modifies the path.
    """
    try:
        ensure_manifest_target_writable(path)
    except ManifestTargetError:
        raise
    if not path.exists():
        return None
    prior = load_manifest(path)
    prior_def = definition_from_manifest(prior)
    if prior_def != expected:
        raise ManifestCompatibilityError(
            "existing benchmark manifest is incompatible with this run "
            "definition; choose a new --tag"
        )
    return prior


def job_identity(entry: dict[str, Any]) -> tuple[Any, Any]:
    return (entry.get("bench_id"), entry.get("config_id"))


def merge_job_entries(
    prior_jobs: Sequence[dict[str, Any]] | None,
    planned: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge planned rows with prior provenance for equivalent resumes."""
    prior_by_id = {}
    if prior_jobs:
        for entry in prior_jobs:
            if isinstance(entry, dict):
                prior_by_id[job_identity(entry)] = dict(entry)
    merged: list[dict[str, Any]] = []
    for plan in planned:
        key = job_identity(plan)
        prev = prior_by_id.get(key)
        if prev is None:
            merged.append(dict(plan))
            continue
        row = dict(prev)
        # Keep planned identity/prefix fields authoritative.
        for field_name in (
            "bench_id",
            "doc_id",
            "corpus",
            "config_id",
            "provider",
            "model",
            "reasoning_effort",
            "output_prefix",
        ):
            if field_name in plan:
                row[field_name] = plan[field_name]
        # Preserve terminal provenance until a later update overwrites.
        if row.get("status") in {None, "pending"}:
            row["status"] = plan.get("status", "pending")
        merged.append(row)
    return merged


def new_manifest(
    *,
    tag: str | None,
    documents: Sequence[dict[str, str]],
    configs: Sequence[BenchModelConfig],
    controls: BenchControls,
    jobs: Sequence[dict[str, Any]],
    cwd: Optional[Path] = None,
    git_commit: Any = ...,
    git_dirty: Any = ...,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    if git_commit is ... or git_dirty is ...:
        prov = git_provenance(cwd)
        if git_commit is ...:
            git_commit = prov["git_commit"]
        if git_dirty is ...:
            git_dirty = prov["git_dirty"]
    return {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "benchmark_tag": tag,
        "created_at_utc": created_at_utc or utc_now_iso(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "documents": document_defs(documents),
        "configurations": [c.to_public_dict() for c in configs],
        "controls": controls.to_public_dict(),
        "jobs": list(jobs),
    }


@dataclass
class MetricStat:
    """Aggregate with explicit completeness."""

    total_jobs: int
    known: int = 0
    value: float = 0.0
    is_int: bool = False

    def add(self, amount: float | int | None) -> None:
        if amount is None:
            return
        self.known += 1
        self.value += float(amount)

    @property
    def complete(self) -> bool:
        return self.known == self.total_jobs and self.total_jobs > 0

    def format(self, *, name: str, currency: bool = False) -> str:
        if self.total_jobs == 0:
            return f"{name}=n/a (0/0 jobs)"
        if self.known == 0:
            return f"{name}=n/a (0/{self.total_jobs} jobs)"
        if currency:
            rendered = f"${self.value:.6f}"
        elif self.is_int:
            rendered = str(int(self.value))
        else:
            rendered = str(self.value)
        if self.complete:
            return f"{name}={rendered}"
        return f"{name}={rendered} (partial: {self.known}/{self.total_jobs} jobs)"


@dataclass
class AggregateMetrics:
    total_jobs: int
    findings: MetricStat = field(init=False)
    analysis_requests: MetricStat = field(init=False)
    repair_requests: MetricStat = field(init=False)
    input_tokens: MetricStat = field(init=False)
    output_tokens: MetricStat = field(init=False)
    reasoning_tokens: MetricStat = field(init=False)
    actual_cost_usd: MetricStat = field(init=False)

    def __post_init__(self) -> None:
        n = self.total_jobs
        self.findings = MetricStat(total_jobs=n, is_int=True)
        self.analysis_requests = MetricStat(total_jobs=n, is_int=True)
        self.repair_requests = MetricStat(total_jobs=n, is_int=True)
        self.input_tokens = MetricStat(total_jobs=n, is_int=True)
        self.output_tokens = MetricStat(total_jobs=n, is_int=True)
        self.reasoning_tokens = MetricStat(total_jobs=n, is_int=True)
        self.actual_cost_usd = MetricStat(total_jobs=n, is_int=False)

    def add_report_fields(
        self,
        fields: dict[str, Any] | None,
        *,
        include: bool,
    ) -> None:
        if not include or fields is None:
            return
        finding_count = fields.get("finding_count")
        if isinstance(finding_count, int) and not isinstance(finding_count, bool):
            self.findings.add(finding_count)
        analysis_n = fields.get("analysis_request_count")
        if isinstance(analysis_n, int) and not isinstance(analysis_n, bool):
            self.analysis_requests.add(analysis_n)
        repair_n = fields.get("repair_request_count")
        if isinstance(repair_n, int) and not isinstance(repair_n, bool):
            self.repair_requests.add(repair_n)
        usage = fields.get("actual_usage")
        if isinstance(usage, dict):
            inp = usage.get("input_tokens")
            out = usage.get("output_tokens")
            rea = usage.get("reasoning_tokens")
            if isinstance(inp, int) and not isinstance(inp, bool):
                self.input_tokens.add(inp)
            if isinstance(out, int) and not isinstance(out, bool):
                self.output_tokens.add(out)
            if isinstance(rea, int) and not isinstance(rea, bool):
                self.reasoning_tokens.add(rea)
        cost = fields.get("actual_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.actual_cost_usd.add(float(cost))

    def format_line(self) -> str:
        parts = [
            self.findings.format(name="findings"),
            self.analysis_requests.format(name="analysis_requests"),
            self.repair_requests.format(name="repair_requests"),
            self.input_tokens.format(name="tokens_in"),
            self.output_tokens.format(name="tokens_out"),
            self.reasoning_tokens.format(name="reasoning_tokens"),
            self.actual_cost_usd.format(name="actual_cost_usd", currency=True),
        ]
        return "Aggregates: " + ", ".join(parts)


def scan_argv_for_config(
    *,
    tads: str,
    source: Path,
    doc_id: str,
    corpus: str,
    config: BenchModelConfig,
    output_prefix_path: Path,
    controls: BenchControls,
    plan_only: bool,
) -> list[str]:
    """Build a ``tads plan|scan`` argv for one job."""
    cmd = [
        tads,
        "plan" if plan_only else "scan",
        str(source),
        "--doc-id",
        doc_id,
        "--corpus",
        corpus,
        "--provider",
        config.provider,
        "--model",
        config.model,
        "-o",
        str(output_prefix_path),
        "--overwrite",
        "-y",
    ]
    if controls.max_cost_usd is not None:
        cmd.extend(["--max-cost-usd", str(controls.max_cost_usd)])
    if controls.max_sections is not None:
        cmd.extend(["--max-sections", str(controls.max_sections)])
    if controls.max_input_tokens is not None:
        cmd.extend(["--max-input-tokens", str(controls.max_input_tokens)])
    if controls.force_sections:
        cmd.append("--force-sections")
    if not controls.section_chunking:
        cmd.append("--no-section-chunking")
    if controls.max_sections_per_request is not None:
        cmd.extend(
            ["--max-sections-per-request", str(controls.max_sections_per_request)]
        )
    # Represent explicit budgets on both plan and scan for consistent display.
    if controls.max_output_tokens is not None:
        cmd.extend(["--max-output-tokens", str(controls.max_output_tokens)])
    if config.reasoning_effort is not None:
        cmd.extend(["--reasoning-effort", config.reasoning_effort])
    return cmd
