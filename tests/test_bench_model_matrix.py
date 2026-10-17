# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Benchmark model-matrix configuration, identity, commands, and manifests."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_common  # noqa: E402
from bench_common import (  # noqa: E402
    AggregateMetrics,
    BenchConfigError,
    BenchControls,
    BenchModelConfig,
    DEFAULT_PLAN_CONFIGS,
    DEFAULT_RUN_CONFIGS,
    ManifestCompatibilityError,
    ManifestTargetError,
    assert_manifest_compatible,
    atomic_write_json,
    derive_config_id,
    ensure_manifest_target_writable,
    expected_report_doc_id,
    load_manifest,
    manifest_path,
    merge_job_entries,
    metadata_mismatch,
    new_manifest,
    output_prefix,
    parse_model_spec,
    report_comparison_fields,
    resolve_bench_configs,
    resolve_named_config,
    run_definition,
    scan_argv_for_config,
    tag_slug,
    validate_controls,
)
from plan_bench_costs import BENCH_DOCS  # noqa: E402
from tads.corpus.registry import get_adapter  # noqa: E402
from tads.path_safety import safe_filename  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_bench_scans",
        _SCRIPTS / "run_bench_scans.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()

SECRET_CANARY = "sk-bench-secret-canary-DO-NOT-LEAK"
ENV_CANARY = "TADS_BENCH_ENV_CANARY_xyz"


# --- configuration parsing -------------------------------------------------


def test_runner_historical_defaults_unchanged():
    configs = resolve_bench_configs(defaults=DEFAULT_RUN_CONFIGS)
    assert [(c.provider, c.model, c.reasoning_effort) for c in configs] == [
        ("openai", "gpt-4.1-mini", None),
        ("gemini", "gemini-3.6-flash", None),
    ]
    assert [c.config_id for c in configs] == ["openai-mini", "gemini-flash"]
    assert configs == list(DEFAULT_RUN_CONFIGS)
    assert all(c.model != "gpt-6-luna" for c in configs)


def test_planner_historical_defaults_unchanged():
    configs = resolve_bench_configs(defaults=DEFAULT_PLAN_CONFIGS)
    assert [(c.provider, c.model, c.reasoning_effort) for c in configs] == [
        ("ollama", "gpt-oss:120b", None),
        ("openai", "gpt-4.1-mini", None),
        ("anthropic", "claude-sonnet-4-5", None),
        ("gemini", "gemini-3.6-flash", None),
    ]
    assert [c.config_id for c in configs] == [
        "ollama-gpt-oss-120b",
        "openai-mini",
        "anthropic-claude-sonnet-4-5",
        "gemini-flash",
    ]
    assert configs == list(DEFAULT_PLAN_CONFIGS)
    assert DEFAULT_RUN_CONFIGS != DEFAULT_PLAN_CONFIGS


def test_named_mini_luna_gemini():
    configs = resolve_bench_configs(
        config_names=[
            "openai-mini",
            "openai-luna-none",
            "openai-luna-low",
            "gemini-flash",
        ]
    )
    assert [c.config_id for c in configs] == [
        "openai-mini",
        "openai-luna-none",
        "openai-luna-low",
        "gemini-flash",
    ]
    assert configs[1].reasoning_effort == "none"
    assert configs[2].reasoning_effort == "low"


def test_duplicate_config_ids_rejected():
    with pytest.raises(BenchConfigError, match="duplicate"):
        resolve_bench_configs(config_names=["openai-mini", "openai-mini"])
    with pytest.raises(BenchConfigError, match="duplicate"):
        resolve_bench_configs(
            config_names=["openai-mini"],
            model_specs=["openai/gpt-4.1-mini"],
        )


def test_malformed_model_specs():
    with pytest.raises(BenchConfigError):
        parse_model_spec("")
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai")
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai/gpt-6-luna")  # effort required
    with pytest.raises(BenchConfigError):
        parse_model_spec("../evil/model")


def test_unsupported_reasoning_effort():
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai/gpt-4.1-mini/none")
    with pytest.raises(BenchConfigError):
        parse_model_spec("gemini/gemini-3.6-flash/low")
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai/gpt-6-luna/not-a-level")


def test_caller_specified_anthropic_and_ollama_without_invented_defaults():
    anthropic = parse_model_spec("anthropic/claude-sonnet-4-5")
    ollama = parse_model_spec("ollama/gpt-oss:120b")
    assert anthropic.provider == "anthropic"
    assert anthropic.model == "claude-sonnet-4-5"
    assert anthropic.reasoning_effort is None
    assert ollama.provider == "ollama"
    assert ollama.model == "gpt-oss:120b"
    assert "claude" in anthropic.config_id
    assert "gpt-oss-120b" in ollama.config_id
    # No silent Anthropic/Ollama defaults in the runner historical matrix.
    assert all(c.provider not in {"anthropic", "ollama"} for c in DEFAULT_RUN_CONFIGS)


def test_luna_effort_casefold_normalized():
    cfg = parse_model_spec("openai/gpt-6-luna/NONE")
    assert cfg.reasoning_effort == "none"


# --- output identity -------------------------------------------------------


def test_luna_none_and_low_distinct_prefixes(tmp_path: Path):
    controls = BenchControls()
    none_cfg = resolve_named_config("openai-luna-none")
    low_cfg = resolve_named_config("openai-luna-low")
    a = output_prefix(tmp_path, "RFC5905", none_cfg, controls, "tag")
    b = output_prefix(tmp_path, "RFC5905", low_cfg, controls, "tag")
    assert a != b
    assert "re-none" in a.name
    assert "re-low" in b.name


def test_chunking_and_output_token_controls_change_prefix(tmp_path: Path):
    mini = resolve_named_config("openai-mini")
    base = output_prefix(tmp_path, "RFC5905", mini, BenchControls(), None)
    fs = output_prefix(
        tmp_path, "RFC5905", mini, BenchControls(force_sections=True), None
    )
    nc = output_prefix(
        tmp_path,
        "RFC5905",
        mini,
        BenchControls(section_chunking=False),
        None,
    )
    mspr = output_prefix(
        tmp_path,
        "RFC5905",
        mini,
        BenchControls(max_sections_per_request=8),
        None,
    )
    mot = output_prefix(
        tmp_path,
        "RFC5905",
        mini,
        BenchControls(max_output_tokens=8192),
        None,
    )
    assert base.name == "RFC5905__openai__gpt-4.1-mini"
    assert "fs" in fs.name and fs != base
    assert "nc" in nc.name and nc != base
    assert "mspr-8" in mspr.name and mspr != base
    assert "mot-8192" in mot.name and mot != base


def test_historical_default_prefix_compatible(tmp_path: Path):
    mini = resolve_named_config("openai-mini")
    gemini = resolve_named_config("gemini-flash")
    controls = BenchControls()
    assert (
        output_prefix(tmp_path, "RFC5905", mini, controls, "bench2026").name
        == "RFC5905__openai__gpt-4.1-mini__bench2026"
    )
    assert (
        output_prefix(tmp_path, "RFC868", gemini, controls, None).name
        == "RFC868__gemini__gemini-3.6-flash"
    )


def test_dotted_model_and_tag_prefix(tmp_path: Path):
    gemini = resolve_named_config("gemini-flash")
    prefix = output_prefix(
        tmp_path, "RFC868", gemini, BenchControls(), "0.6.0rc3-post"
    )
    paths = runner.artifact_paths(prefix)
    assert paths.json_path == Path(f"{prefix}.json")
    assert "0.6.0rc3-post.json" in paths.json_path.name


def test_validate_controls_rejects_nonpositive():
    with pytest.raises(BenchConfigError):
        validate_controls(BenchControls(max_sections_per_request=0))
    with pytest.raises(BenchConfigError):
        validate_controls(BenchControls(max_output_tokens=0))


# --- command construction --------------------------------------------------


def test_scan_command_reasoning_and_chunk_flags():
    luna = resolve_named_config("openai-luna-low")
    mini = resolve_named_config("openai-mini")
    controls = BenchControls(
        force_sections=True,
        section_chunking=False,
        max_sections_per_request=8,
        max_output_tokens=4096,
        max_cost_usd=2.5,
    )
    luna_cmd = scan_argv_for_config(
        tads="tads",
        source=Path("inputs/RFC868.txt"),
        doc_id="RFC868",
        corpus="ietf",
        config=luna,
        output_prefix_path=Path("outputs/x"),
        controls=controls,
        plan_only=False,
    )
    assert luna_cmd.count("--reasoning-effort") == 1
    assert "low" in luna_cmd
    assert luna_cmd.count("--force-sections") == 1
    assert luna_cmd.count("--no-section-chunking") == 1
    assert luna_cmd.count("--max-sections-per-request") == 1
    assert luna_cmd.count("--max-output-tokens") == 1

    mini_cmd = scan_argv_for_config(
        tads="tads",
        source=Path("inputs/RFC868.txt"),
        doc_id="RFC868",
        corpus="ietf",
        config=mini,
        output_prefix_path=Path("outputs/x"),
        controls=BenchControls(),
        plan_only=False,
    )
    assert "--reasoning-effort" not in mini_cmd
    assert "--force-sections" not in mini_cmd
    assert "--no-section-chunking" not in mini_cmd
    assert "--max-sections-per-request" not in mini_cmd
    assert "--max-output-tokens" not in mini_cmd

    plan_cmd = scan_argv_for_config(
        tads="tads",
        source=Path("inputs/RFC868.txt"),
        doc_id="RFC868",
        corpus="ietf",
        config=luna,
        output_prefix_path=Path("outputs/x"),
        controls=controls,
        plan_only=True,
    )
    assert plan_cmd[1] == "plan"
    assert plan_cmd.count("--max-output-tokens") == 1
    assert "4096" in plan_cmd
    assert "--reasoning-effort" in plan_cmd


# --- resume identity -------------------------------------------------------


def _luna_job(tmp_path: Path, effort: str, *, force_sections: bool = False):
    outputs = tmp_path / "out"
    outputs.mkdir(parents=True, exist_ok=True)
    config = resolve_named_config(f"openai-luna-{effort}")
    controls = BenchControls(force_sections=force_sections)
    prefix = output_prefix(outputs, "RFC868", config, controls, None)
    return runner.BenchJob(
        bench_id="02",
        doc_id="RFC868",
        corpus="ietf",
        source=tmp_path / "RFC868.txt",
        config=config,
        controls=controls,
        output_prefix=prefix,
    )


def _write_report(job, **run_extra):
    from tads.schemas.report import AnalysisMode, RequestBatching

    run = {
        "scanner_version": "0.6.0rc3",
        "prompt_framework_version": "0.9.1",
        "provider": job.provider,
        "model": job.model,
        "analysis_mode": AnalysisMode.SECTION_AWARE.value,
        "reasoning_effort": job.reasoning_effort,
        "request_batching": RequestBatching.SECTION_CHUNKS.value,
        "analysis_request_count": 2,
        "repair_request_count": 0,
        "chunk_section_counts": [2, 2],
        "sections_analyzed": 4,
        "coverage_complete": True,
        "max_output_tokens": 16384,
    }
    run.update(run_extra)
    payload = {
        "schema_version": "0.5.0",
        "document": {"corpus": job.corpus, "doc_id": job.doc_id},
        "run": run,
        "findings": [],
    }
    path = runner.artifact_paths(job.output_prefix).json_path
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_matching_luna_reasoning_skips(tmp_path: Path, monkeypatch):
    job = _luna_job(tmp_path, "none")
    _write_report(job)
    runner.artifact_paths(job.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no scan")),
    )
    assert (
        runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=True,
            continue_on_error=False,
            plan_only=False,
            dry_run=False,
            outputs_dir=tmp_path / "out",
            write_manifest=False,
        )
        == 0
    )


def test_different_or_missing_reasoning_fails_closed(tmp_path: Path, monkeypatch):
    job = _luna_job(tmp_path, "low")
    _write_report(job, reasoning_effort="none")
    runner.artifact_paths(job.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no scan")),
    )
    assert (
        runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=True,
            continue_on_error=False,
            plan_only=False,
            dry_run=False,
            outputs_dir=tmp_path / "out",
            write_manifest=False,
        )
        == 1
    )
    assert runner.classify_existing(job).action == "mismatch"

    job2 = _luna_job(tmp_path, "none")
    _write_report(job2, reasoning_effort=None)
    runner.artifact_paths(job2.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    assert runner.classify_existing(job2).action == "mismatch"


def test_matching_batching_metadata_skips(tmp_path: Path, monkeypatch):
    from tads.schemas.report import RequestBatching

    job = _luna_job(tmp_path, "none", force_sections=True)
    _write_report(job, request_batching=RequestBatching.SECTION_CHUNKS.value)
    runner.artifact_paths(job.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no scan")),
    )
    assert (
        runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=True,
            continue_on_error=False,
            plan_only=False,
            dry_run=False,
            outputs_dir=tmp_path / "out",
            write_manifest=False,
            configs=[job.config],
            controls=job.controls,
        )
        == 0
    )


def test_non_reasoning_expects_null_effort():
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-mini")
    controls = BenchControls()
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model="gpt-4.1-mini",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            reasoning_effort=None,
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=2,
            repair_request_count=0,
            chunk_section_counts=[2, 2],
            sections_analyzed=4,
            coverage_complete=True,
        ),
        findings=[],
    )
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=report,
        )
        == ""
    )
    bad = report.model_copy(
        update={
            "run": report.run.model_copy(update={"reasoning_effort": "none"})
        }
    )
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=bad,
        )
        == "reasoning_effort"
    )


# --- manifest / summary ----------------------------------------------------


def test_manifest_records_configs_controls_git_and_null_cost(tmp_path: Path):
    configs = resolve_bench_configs(
        config_names=["openai-mini", "openai-luna-none", "gemini-flash"]
    )
    controls = BenchControls(
        force_sections=True,
        max_sections_per_request=8,
        max_output_tokens=4096,
    )
    jobs = [
        {
            "doc_id": "RFC868",
            "config_id": "openai-mini",
            "status": "pending",
            "report": None,
        }
    ]
    payload = new_manifest(
        tag="cmp",
        documents=[{"bench_id": "02", "doc_id": "RFC868", "corpus": "ietf", "source": "RFC868.txt"}],
        configs=configs,
        controls=controls,
        jobs=jobs,
        cwd=ROOT,
    )
    assert payload["manifest_format_version"] == 1
    assert payload["benchmark_tag"] == "cmp"
    assert payload["controls"]["force_sections"] is True
    assert payload["controls"]["max_sections_per_request"] == 8
    assert payload["configurations"][1]["reasoning_effort"] == "none"
    assert "git_commit" in payload
    assert "git_dirty" in payload
    path = manifest_path(tmp_path, "cmp")
    atomic_write_json(path, payload)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["jobs"][0]["report"] is None
    blob = path.read_text(encoding="utf-8")
    assert SECRET_CANARY not in blob
    assert ENV_CANARY not in blob


def test_report_comparison_fields_null_for_legacy():
    from tads.schemas.report import DocumentIdentity, Report, RunMetadata

    report = Report(
        schema_version="0.3.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model="gpt-4.1-mini",
        ),
        findings=[],
        actual_cost_usd=None,
    )
    fields = report_comparison_fields(report)
    assert fields["actual_cost_usd"] is None
    assert fields["analysis_request_count"] is None
    assert fields["coverage_complete"] is None
    assert fields["finding_count"] == 0


def test_atomic_rewrite_leaves_valid_json(tmp_path: Path):
    path = tmp_path / "bench_manifest.json"
    atomic_write_json(path, {"manifest_format_version": 1, "jobs": []})
    atomic_write_json(
        path,
        {"manifest_format_version": 1, "jobs": [{"status": "executed"}]},
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["jobs"][0]["status"] == "executed"


def test_interrupted_run_retains_completed_manifest(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "out"
    outputs.mkdir()
    first = _luna_job(tmp_path, "none")
    second = _luna_job(tmp_path, "low")
    # Distinct prefixes already from effort.
    assert first.output_prefix != second.output_prefix

    class _FakeProc:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self._code = None

        def poll(self):
            return self._code

        def wait(self, timeout=None):
            _ = timeout
            if self._code is None:
                raise KeyboardInterrupt
            return self._code

        def terminate(self):
            self.terminated = True
            self._code = -2

        def kill(self):
            self.killed = True
            self._code = -9

    monkeypatch.setattr(runner, "_popen", lambda cmd: _FakeProc())
    code = runner.run_jobs(
        [first, second],
        tads="tads",
        skip_existing=False,
        continue_on_error=False,
        plan_only=False,
        dry_run=False,
        outputs_dir=outputs,
        tag="intr",
        configs=[first.config, second.config],
        controls=BenchControls(),
        write_manifest=True,
    )
    assert code == 130
    man = manifest_path(outputs, "intr")
    assert man.is_file()
    data = json.loads(man.read_text(encoding="utf-8"))
    statuses = [j["status"] for j in data["jobs"]]
    assert "interrupted" in statuses
    assert statuses.count("pending") == 1


def test_manifest_excludes_env_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET_CANARY)
    monkeypatch.setenv("TADS_BENCH_ENV_CANARY", ENV_CANARY)
    payload = new_manifest(
        tag=None,
        documents=[],
        configs=list(DEFAULT_RUN_CONFIGS),
        controls=BenchControls(),
        jobs=[],
        cwd=ROOT,
    )
    path = tmp_path / "bench_manifest.json"
    atomic_write_json(path, payload)
    text = path.read_text(encoding="utf-8")
    assert SECRET_CANARY not in text
    assert ENV_CANARY not in text
    assert "OPENAI_API_KEY" not in text


def test_derive_config_id_includes_effort():
    assert derive_config_id("openai", "gpt-6-luna", "none") != derive_config_id(
        "openai", "gpt-6-luna", "low"
    )


def test_runner_dry_run_plan_only_no_popen(tmp_path: Path, monkeypatch):
    job = _luna_job(tmp_path, "none")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no subprocess")),
    )
    # dry-run should not popen
    assert (
        runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=False,
            continue_on_error=False,
            plan_only=True,
            dry_run=True,
            outputs_dir=tmp_path / "out",
            write_manifest=False,
        )
        == 0
    )


# --- exact-model slash grammar ---------------------------------------------


def test_ollama_model_with_colon():
    cfg = parse_model_spec("ollama/gpt-oss:120b")
    assert cfg.model == "gpt-oss:120b"
    assert cfg.reasoning_effort is None


def test_two_and_three_component_exact_specs():
    two = parse_model_spec("openai/gpt-4.1-mini")
    assert two.provider == "openai" and two.model == "gpt-4.1-mini"
    three = parse_model_spec("openai/gpt-6-luna/low")
    assert three.reasoning_effort == "low"


def test_too_many_slash_components_not_misread_as_effort():
    with pytest.raises(BenchConfigError, match="containing '/'"):
        parse_model_spec("openai/org/gpt-6-luna/none")
    with pytest.raises(BenchConfigError, match="containing '/'"):
        parse_model_spec("provider/a/b/c")


def test_empty_and_slash_edge_model_specs():
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai/")
    with pytest.raises(BenchConfigError):
        parse_model_spec("/openai/gpt-4.1-mini")
    with pytest.raises(BenchConfigError):
        parse_model_spec("openai/gpt-6-luna/")
    with pytest.raises(BenchConfigError):
        parse_model_spec(" /gpt-4.1-mini")


# --- tag slug collision resistance -----------------------------------------


def test_tag_slug_preserves_safe_historical_tags():
    assert tag_slug("bench2026") == "bench2026"
    assert tag_slug("0.6.0rc4-bench") == "0.6.0rc4-bench"
    assert tag_slug("cmp") == "cmp"


def test_tag_slug_collision_pairs():
    assert tag_slug("foo bar") != tag_slug("foo_bar")
    assert tag_slug("foo/bar") != tag_slug("foo_bar")
    assert tag_slug(".") != tag_slug("..")
    assert tag_slug("") != tag_slug("!!!")
    assert tag_slug("café!!") != tag_slug("cafe__")
    assert "_" in tag_slug("foo bar")  # digest suffix
    assert len(tag_slug("foo bar").split("_")[-1]) == 12


def test_tag_slug_empty_and_dot_safe_nonempty():
    for tag in ("", ".", "..", "!!!", "..."):
        slug = tag_slug(tag)
        assert slug
        assert slug not in {".", ".."}
        assert "/" not in slug and "\\" not in slug


def test_manifest_and_prefix_share_tag_slug(tmp_path: Path):
    tag = "foo bar"
    prefix = output_prefix(
        tmp_path, "RFC868", resolve_named_config("openai-mini"), BenchControls(), tag
    )
    man = manifest_path(tmp_path, tag)
    assert tag_slug(tag) in prefix.name
    assert man.name == f"bench_manifest__{tag_slug(tag)}.json"


# --- manifest target refusal -----------------------------------------------


def test_regular_manifest_target_accepted(tmp_path: Path):
    path = tmp_path / "bench_manifest.json"
    path.write_text("{}", encoding="utf-8")
    ensure_manifest_target_writable(path)
    missing = tmp_path / "missing.json"
    ensure_manifest_target_writable(missing)


def test_symlink_manifest_refused(tmp_path: Path):
    target = tmp_path / "real.json"
    target.write_text('{"keep": true}\n', encoding="utf-8")
    link = tmp_path / "bench_manifest.json"
    link.symlink_to(target)
    before, mtime = target.read_bytes(), target.stat().st_mtime_ns
    with pytest.raises(ManifestTargetError, match="symlink"):
        ensure_manifest_target_writable(link)
    with pytest.raises(ManifestTargetError):
        load_manifest(link)
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == mtime
    assert link.is_symlink()


def test_broken_symlink_manifest_refused(tmp_path: Path):
    link = tmp_path / "bench_manifest.json"
    link.symlink_to(tmp_path / "does-not-exist.json")
    with pytest.raises(ManifestTargetError, match="symlink"):
        ensure_manifest_target_writable(link)


def test_directory_manifest_refused(tmp_path: Path):
    path = tmp_path / "bench_manifest.json"
    path.mkdir()
    marker = path / "marker.txt"
    marker.write_text("keep\n", encoding="utf-8")
    with pytest.raises(ManifestTargetError, match="directory"):
        ensure_manifest_target_writable(path)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_fifo_manifest_refused(tmp_path: Path):
    path = tmp_path / "bench_manifest.json"
    try:
        os.mkfifo(path)
    except (AttributeError, OSError, NotImplementedError):
        pytest.skip("FIFO not supported on this platform")
    with pytest.raises(ManifestTargetError, match="FIFO"):
        ensure_manifest_target_writable(path)
    assert path.exists()


def test_atomic_write_cleanup_on_failure(tmp_path: Path, monkeypatch):
    path = tmp_path / "bench_manifest.json"
    path.write_text('{"manifest_format_version": 1, "jobs": []}\n', encoding="utf-8")
    before = path.read_bytes()

    def _boom(*_a, **_k):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_json(path, {"manifest_format_version": 1, "jobs": [{"x": 1}]})
    assert path.read_bytes() == before
    leftovers = list(tmp_path.glob(".bench-manifest-*.tmp"))
    assert leftovers == []
    json.loads(path.read_text(encoding="utf-8"))


# --- same-tag compatibility ------------------------------------------------


def _fixed_git(monkeypatch, commit="abc123", dirty=False):
    monkeypatch.setattr(
        runner,
        "git_provenance",
        lambda cwd=None: {"git_commit": commit, "git_dirty": dirty},
    )
    monkeypatch.setattr(
        bench_common,
        "git_provenance",
        lambda cwd=None: {"git_commit": commit, "git_dirty": dirty},
    )


def _compat_manifest(
    *,
    tag: str,
    configs,
    controls: BenchControls,
    jobs,
    git_commit="abc123",
    git_dirty=False,
    documents=None,
    **extra,
):
    payload = new_manifest(
        tag=tag,
        documents=documents if documents is not None else BENCH_DOCS,
        configs=configs,
        controls=controls,
        jobs=jobs,
        git_commit=git_commit,
        git_dirty=git_dirty,
    )
    payload.update(extra)
    return payload


def test_equivalent_same_tag_resume(tmp_path: Path, monkeypatch):
    _fixed_git(monkeypatch)
    outputs = tmp_path / "out"
    outputs.mkdir()
    configs = list(DEFAULT_RUN_CONFIGS)
    controls = BenchControls()
    jobs = [
        {
            "bench_id": "02",
            "doc_id": "RFC868",
            "config_id": "openai-mini",
            "status": "executed",
            "elapsed_seconds": 1.5,
            "report": {"finding_count": 0, "actual_cost_usd": 0.01},
        }
    ]
    path = manifest_path(outputs, "same")
    atomic_write_json(
        path,
        _compat_manifest(tag="same", configs=configs, controls=controls, jobs=jobs),
    )
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    expected = run_definition(
        tag="same",
        documents=BENCH_DOCS,
        configs=configs,
        controls=controls,
        git_commit="abc123",
        git_dirty=False,
    )
    prior = assert_manifest_compatible(path, expected=expected)
    assert prior is not None
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(
            lambda p: p.__setitem__(
                "configurations",
                [resolve_named_config("openai-luna-none").to_public_dict()],
            ),
            id="model-set",
        ),
        pytest.param(
            lambda p: p.__setitem__(
                "configurations",
                [
                    resolve_named_config("openai-luna-none").to_public_dict(),
                ],
            ),
            id="luna-none-vs-requested-low-setup",
        ),
        pytest.param(
            lambda p: p.__setitem__("documents", []),
            id="documents",
        ),
        pytest.param(
            lambda p: p["controls"].__setitem__("force_sections", True),
            id="force-sections",
        ),
        pytest.param(
            lambda p: p["controls"].__setitem__("section_chunking", False),
            id="chunking",
        ),
        pytest.param(
            lambda p: p["controls"].__setitem__("max_sections_per_request", 3),
            id="max-sections",
        ),
        pytest.param(
            lambda p: p["controls"].__setitem__("max_output_tokens", 999),
            id="max-output",
        ),
        pytest.param(
            lambda p: p["controls"].__setitem__("max_cost_usd", 1.25),
            id="max-cost",
        ),
        pytest.param(
            lambda p: p.__setitem__("git_commit", "other"),
            id="git-commit",
        ),
        pytest.param(
            lambda p: p.__setitem__("git_dirty", True),
            id="git-dirty",
        ),
    ),
)
def test_incompatible_same_tag_fails_closed(tmp_path: Path, monkeypatch, mutate):
    _fixed_git(monkeypatch)
    outputs = tmp_path / "out"
    outputs.mkdir()
    configs = [resolve_named_config("openai-luna-low")]
    controls = BenchControls()
    path = manifest_path(outputs, "cmp")
    payload = _compat_manifest(
        tag="cmp",
        configs=configs,
        controls=controls,
        jobs=[{"bench_id": "02", "config_id": "openai-luna-low", "status": "pending"}],
    )
    mutate(payload)
    atomic_write_json(path, payload)
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    expected = run_definition(
        tag="cmp",
        documents=BENCH_DOCS,
        configs=configs,
        controls=controls,
        git_commit="abc123",
        git_dirty=False,
    )
    with pytest.raises(ManifestCompatibilityError, match="choose a new --tag"):
        assert_manifest_compatible(path, expected=expected)
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime

    job = _luna_job(tmp_path, "low")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no scan on mismatch")),
    )
    for skip in (False, True):
        code = runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=skip,
            continue_on_error=False,
            plan_only=False,
            dry_run=False,
            outputs_dir=outputs,
            tag="cmp",
            configs=configs,
            controls=controls,
            write_manifest=True,
        )
        assert code == 2
        assert path.read_bytes() == before
        assert path.stat().st_mtime_ns == mtime


def test_interrupted_equivalent_manifest_resumable(tmp_path: Path, monkeypatch):
    _fixed_git(monkeypatch)
    outputs = tmp_path / "out"
    outputs.mkdir()
    configs = [resolve_named_config("openai-luna-none")]
    controls = BenchControls()
    jobs = [
        {
            "bench_id": "02",
            "doc_id": "RFC868",
            "config_id": "openai-luna-none",
            "status": "interrupted",
            "elapsed_seconds": 0.2,
            "report": None,
        }
    ]
    path = manifest_path(outputs, "intr2")
    atomic_write_json(
        path,
        _compat_manifest(tag="intr2", configs=configs, controls=controls, jobs=jobs),
    )
    expected = run_definition(
        tag="intr2",
        documents=BENCH_DOCS,
        configs=configs,
        controls=controls,
        git_commit="abc123",
        git_dirty=False,
    )
    prior = assert_manifest_compatible(path, expected=expected)
    assert prior["jobs"][0]["status"] == "interrupted"


def test_merge_preserves_executed_on_equivalent_resume():
    prior = [
        {
            "bench_id": "02",
            "config_id": "openai-mini",
            "status": "executed",
            "elapsed_seconds": 3.0,
            "report": {"finding_count": 2},
            "output_prefix": "old",
        }
    ]
    planned = [
        {
            "bench_id": "02",
            "config_id": "openai-mini",
            "status": "pending",
            "output_prefix": "new",
            "doc_id": "RFC868",
        }
    ]
    merged = merge_job_entries(prior, planned)
    assert len(merged) == 1
    assert merged[0]["status"] == "executed"
    assert merged[0]["elapsed_seconds"] == 3.0
    assert merged[0]["report"]["finding_count"] == 2
    assert merged[0]["output_prefix"] == "new"


def test_skip_preserves_executed_provenance(tmp_path: Path, monkeypatch):
    _fixed_git(monkeypatch)
    outputs = tmp_path / "out"
    outputs.mkdir()
    job = _luna_job(tmp_path, "none")
    # Redirect job into outputs dir with tag for manifest path.
    job = runner.BenchJob(
        bench_id=job.bench_id,
        doc_id=job.doc_id,
        corpus=job.corpus,
        source=job.source,
        config=job.config,
        controls=job.controls,
        output_prefix=output_prefix(
            outputs, job.doc_id, job.config, job.controls, "prov"
        ),
    )
    _write_report(job)
    runner.artifact_paths(job.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    path = manifest_path(outputs, "prov")
    atomic_write_json(
        path,
        _compat_manifest(
            tag="prov",
            configs=[job.config],
            controls=job.controls,
            jobs=[
                {
                    "bench_id": job.bench_id,
                    "doc_id": job.doc_id,
                    "config_id": job.config_id,
                    "status": "executed",
                    "elapsed_seconds": 9.9,
                    "report": {"finding_count": 0, "actual_cost_usd": 0.12},
                }
            ],
        ),
    )
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no scan")),
    )
    code = runner.run_jobs(
        [job],
        tads="tads",
        skip_existing=True,
        continue_on_error=False,
        plan_only=False,
        dry_run=False,
        outputs_dir=outputs,
        tag="prov",
        configs=[job.config],
        controls=job.controls,
        write_manifest=True,
    )
    assert code == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["jobs"][0]["status"] == "executed"
    assert data["jobs"][0]["elapsed_seconds"] == 9.9
    assert data["jobs"][0]["report"]["actual_cost_usd"] == 0.12


# --- aggregate completeness ------------------------------------------------


def test_aggregate_all_known():
    m = AggregateMetrics(total_jobs=2)
    m.add_report_fields(
        {
            "finding_count": 1,
            "analysis_request_count": 2,
            "repair_request_count": 0,
            "actual_usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 1,
            },
            "actual_cost_usd": 0.05,
        },
        include=True,
    )
    m.add_report_fields(
        {
            "finding_count": 2,
            "analysis_request_count": 1,
            "repair_request_count": 1,
            "actual_usage": {
                "input_tokens": 20,
                "output_tokens": 7,
                "reasoning_tokens": 0,
            },
            "actual_cost_usd": 0.07,
        },
        include=True,
    )
    line = m.format_line()
    assert "actual_cost_usd=$0.120000" in line
    assert "partial" not in line
    assert "findings=3" in line


def test_aggregate_partial_and_none():
    partial = AggregateMetrics(total_jobs=2)
    partial.add_report_fields(
        {
            "finding_count": 0,
            "analysis_request_count": 1,
            "repair_request_count": 0,
            "actual_usage": {
                "input_tokens": 3,
                "output_tokens": 1,
                "reasoning_tokens": 0,
            },
            "actual_cost_usd": 0.12,
        },
        include=True,
    )
    line = partial.format_line()
    assert "actual_cost_usd=$0.120000 (partial: 1/2 jobs)" in line
    assert "findings=0 (partial: 1/2 jobs)" in line

    none = AggregateMetrics(total_jobs=2)
    assert "actual_cost_usd=n/a (0/2 jobs)" in none.format_line()


def test_aggregate_empty_report_zero_findings():
    m = AggregateMetrics(total_jobs=1)
    m.add_report_fields({"finding_count": 0}, include=True)
    assert "findings=0" in m.format_line()
    assert "partial" not in m.findings.format(name="findings")


def test_aggregate_legacy_missing_batching_metrics():
    m = AggregateMetrics(total_jobs=1)
    m.add_report_fields(
        {
            "finding_count": 1,
            "analysis_request_count": None,
            "repair_request_count": None,
            "actual_usage": None,
            "actual_cost_usd": None,
        },
        include=True,
    )
    line = m.format_line()
    assert "findings=1" in line
    assert "analysis_requests=n/a (0/1 jobs)" in line
    assert "actual_cost_usd=n/a (0/1 jobs)" in line


def test_aggregate_failed_and_pending_not_zero():
    m = AggregateMetrics(total_jobs=2)
    # Only one successful report; failed/pending contribute nothing.
    m.add_report_fields({"finding_count": 4, "actual_cost_usd": 0.5}, include=True)
    line = m.format_line()
    assert "partial: 1/2" in line
    assert "actual_cost_usd=$0.500000 (partial: 1/2 jobs)" in line


def test_aggregate_mixed_jobs_terminal(tmp_path: Path, monkeypatch, capsys):
    _fixed_git(monkeypatch)
    outputs = tmp_path / "out"
    outputs.mkdir()
    ok = _luna_job(tmp_path, "none")
    bad = _luna_job(tmp_path, "low")
    ok = runner.BenchJob(
        bench_id="02",
        doc_id=ok.doc_id,
        corpus=ok.corpus,
        source=ok.source,
        config=ok.config,
        controls=ok.controls,
        output_prefix=output_prefix(outputs, ok.doc_id, ok.config, ok.controls, "mix"),
    )
    bad = runner.BenchJob(
        bench_id="02",
        doc_id=bad.doc_id,
        corpus=bad.corpus,
        source=bad.source,
        config=bad.config,
        controls=bad.controls,
        output_prefix=output_prefix(outputs, bad.doc_id, bad.config, bad.controls, "mix"),
    )
    _write_report(ok)
    path = runner.artifact_paths(ok.output_prefix).json_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["actual_cost_usd"] = 0.11
    payload["actual_usage"] = {
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 0,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    runner.artifact_paths(ok.output_prefix).md_path.write_text("md\n", encoding="utf-8")

    class _Fail:
        def poll(self):
            return 1

        def wait(self, timeout=None):
            _ = timeout
            return 1

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(runner, "_popen", lambda cmd: _Fail())
    code = runner.run_jobs(
        [ok, bad],
        tads="tads",
        skip_existing=True,
        continue_on_error=True,
        plan_only=False,
        dry_run=False,
        outputs_dir=outputs,
        tag="mix",
        configs=[ok.config, bad.config],
        controls=BenchControls(),
        write_manifest=True,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "actual_cost_usd=$0.110000 (partial: 1/2 jobs)" in out


# --- resume batching singleton ---------------------------------------------


def test_chunking_enabled_singleton_per_section_matches():
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-mini")
    controls = BenchControls(force_sections=True, section_chunking=True)
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model="gpt-4.1-mini",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            reasoning_effort=None,
            request_batching=RequestBatching.PER_SECTION,
            analysis_request_count=3,
            repair_request_count=0,
            chunk_section_counts=[1, 1, 1],
            sections_analyzed=3,
            coverage_complete=True,
        ),
        findings=[],
    )
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=report,
        )
        == ""
    )
    whole = report.model_copy(
        update={
            "run": report.run.model_copy(
                update={"request_batching": RequestBatching.WHOLE_DOCUMENT}
            )
        }
    )
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=whole,
        )
        == "request_batching"
    )


def test_no_section_chunking_requires_per_section():
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-mini")
    controls = BenchControls(force_sections=True, section_chunking=False)
    chunks = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model="gpt-4.1-mini",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=1,
            repair_request_count=0,
            chunk_section_counts=[2],
            sections_analyzed=2,
            coverage_complete=True,
        ),
        findings=[],
    )
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=chunks,
        )
        == "request_batching"
    )


def test_legacy_null_max_output_tokens_relies_on_prefix():
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-mini")
    controls = BenchControls(max_output_tokens=4096)
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider="openai",
            model="gpt-4.1-mini",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            request_batching=RequestBatching.SECTION_CHUNKS,
            max_output_tokens=None,
            analysis_request_count=1,
            repair_request_count=0,
            chunk_section_counts=[2],
            sections_analyzed=2,
            coverage_complete=True,
        ),
        findings=[],
    )
    # Null recorded value: no report-level mismatch (prefix identity only).
    assert (
        metadata_mismatch(
            doc_id="RFC868",
            corpus="ietf",
            config=config,
            controls=controls,
            report=report,
        )
        == ""
    )


# --- canonical report document identity (resume) ---------------------------


def _bench_job(tmp_path: Path, doc: dict[str, str], *, tag: str | None = None):
    outputs = tmp_path / "out"
    outputs.mkdir(parents=True, exist_ok=True)
    config = resolve_named_config("openai-mini")
    controls = BenchControls()
    return runner.BenchJob(
        bench_id=doc["bench_id"],
        doc_id=doc["doc_id"],
        corpus=doc["corpus"],
        source=tmp_path / doc["source"],
        config=config,
        controls=controls,
        output_prefix=output_prefix(
            outputs, doc["doc_id"], config, controls, tag
        ),
    )


def _write_canonical_report(job, *, doc_id_override: str | None = None, **run_extra):
    from tads.schemas.report import AnalysisMode, RequestBatching

    run = {
        "scanner_version": "0.6.0rc4",
        "prompt_framework_version": "0.9.1",
        "provider": job.provider,
        "model": job.model,
        "analysis_mode": AnalysisMode.WHOLE_DOCUMENT.value,
        "reasoning_effort": job.reasoning_effort,
        "request_batching": RequestBatching.WHOLE_DOCUMENT.value,
        "analysis_request_count": 1,
        "repair_request_count": 0,
        "chunk_section_counts": [1],
        "coverage_complete": True,
        "max_output_tokens": 16384,
    }
    run.update(run_extra)
    published_id = doc_id_override or expected_report_doc_id(job.doc_id, job.corpus)
    payload = {
        "schema_version": "0.5.0",
        "document": {"corpus": job.corpus, "doc_id": published_id},
        "run": run,
        "findings": [],
    }
    path = runner.artifact_paths(job.output_prefix).json_path
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_expected_report_doc_id_matches_adapter_resolve_for_all_bench_docs():
    """Regression: every BENCH_DOCS entry uses adapter-canonical publish ids."""
    assert len(BENCH_DOCS) == 12
    for doc in BENCH_DOCS:
        adapter = get_adapter(doc["corpus"])
        expected = expected_report_doc_id(doc["doc_id"], doc["corpus"])
        # Same path as plan_scan / run_scan (resolve → document.doc_id).
        assert expected == adapter.resolve(doc["doc_id"]).doc_id


def test_bench_doc_requested_versus_expected_report_ids():
    mapping = {
        (d["doc_id"], d["corpus"]): expected_report_doc_id(d["doc_id"], d["corpus"])
        for d in BENCH_DOCS
    }
    assert mapping[("RFC5905", "ietf")] == "RFC5905"
    assert mapping[("RFC868", "ietf")] == "RFC868"
    assert mapping[("EN 300 468", "etsi")] == "ETSI EN 300 468"
    assert mapping[("TS 103 221-2", "etsi")] == "ETSI TS 103 221-2"
    assert mapping[("TS 29.061", "3gpp")] == "3GPP TS 29.061"
    assert mapping[("TS 29.274", "3gpp")] == "3GPP TS 29.274"
    assert mapping[("X.680", "itu-t")] == "X.680"
    assert mapping[("ECMA-262", "ecma")] == "ECMA-262"
    assert mapping[("OpenFormula", "oasis")] == "OpenFormula"
    assert mapping[("hr-time-3", "w3c")] == "hr-time-3"
    assert mapping[("IEEE 1588-2019", "ieee")] == "IEEE 1588-2019"
    assert mapping[("ECMA-404", "ecma")] == "ECMA-404"


@pytest.mark.parametrize("doc", BENCH_DOCS, ids=[d["bench_id"] for d in BENCH_DOCS])
def test_every_bench_doc_canonical_report_skips_without_subprocess(
    tmp_path: Path, monkeypatch, doc
):
    job = _bench_job(tmp_path, doc)
    _write_canonical_report(job)
    runner.artifact_paths(job.output_prefix).md_path.write_text("md\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("no provider subprocess")),
    )
    assert runner.classify_existing(job).action == "skip"
    assert (
        metadata_mismatch(
            doc_id=job.doc_id,
            corpus=job.corpus,
            config=job.config,
            controls=job.controls,
            report=runner._try_load_report(job),
        )
        == ""
    )
    assert (
        runner.run_jobs(
            [job],
            tads="tads",
            skip_existing=True,
            continue_on_error=False,
            plan_only=False,
            dry_run=False,
            outputs_dir=tmp_path / "out",
            write_manifest=False,
            configs=[job.config],
            controls=job.controls,
        )
        == 0
    )


def test_etsi_canonical_prefix_accepted_only_for_matching_etsi_job(tmp_path: Path):
    en = next(d for d in BENCH_DOCS if d["doc_id"] == "EN 300 468")
    ts = next(d for d in BENCH_DOCS if d["doc_id"] == "TS 103 221-2")
    en_job = _bench_job(tmp_path, en)
    ts_job = _bench_job(tmp_path, ts)
    _write_canonical_report(en_job)  # ETSI EN 300 468
    runner.artifact_paths(en_job.output_prefix).md_path.write_text(
        "md\n", encoding="utf-8"
    )
    # Copy EN report bytes onto the TS job prefix → wrong canonical id for that job.
    ts_paths = runner.artifact_paths(ts_job.output_prefix)
    ts_paths.json_path.write_bytes(
        runner.artifact_paths(en_job.output_prefix).json_path.read_bytes()
    )
    ts_paths.md_path.write_text("md\n", encoding="utf-8")
    assert runner.classify_existing(en_job).action == "skip"
    assert runner.classify_existing(ts_job).action == "mismatch"
    assert "ETSI EN" in expected_report_doc_id(en["doc_id"], en["corpus"])
    assert "ETSI TS" in expected_report_doc_id(ts["doc_id"], ts["corpus"])


def test_threegpp_canonical_prefix_accepted_only_for_matching_3gpp_job(
    tmp_path: Path,
):
    a = next(d for d in BENCH_DOCS if d["doc_id"] == "TS 29.061")
    b = next(d for d in BENCH_DOCS if d["doc_id"] == "TS 29.274")
    job_a = _bench_job(tmp_path, a)
    job_b = _bench_job(tmp_path, b)
    _write_canonical_report(job_a)
    runner.artifact_paths(job_a.output_prefix).md_path.write_text(
        "md\n", encoding="utf-8"
    )
    b_paths = runner.artifact_paths(job_b.output_prefix)
    b_paths.json_path.write_bytes(
        runner.artifact_paths(job_a.output_prefix).json_path.read_bytes()
    )
    b_paths.md_path.write_text("md\n", encoding="utf-8")
    assert runner.classify_existing(job_a).action == "skip"
    assert runner.classify_existing(job_b).action == "mismatch"


def test_genuinely_different_doc_id_fails_closed_without_overwrite(
    tmp_path: Path, monkeypatch
):
    doc = next(d for d in BENCH_DOCS if d["doc_id"] == "EN 300 468")
    job = _bench_job(tmp_path, doc)
    _write_canonical_report(job, doc_id_override="ETSI EN 999 999")
    md = runner.artifact_paths(job.output_prefix).md_path
    md.write_text("keep\n", encoding="utf-8")
    json_path = runner.artifact_paths(job.output_prefix).json_path
    before_j, mtime_j = json_path.read_bytes(), json_path.stat().st_mtime_ns
    before_m, mtime_m = md.read_bytes(), md.stat().st_mtime_ns
    monkeypatch.setattr(
        runner,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    assert runner.classify_existing(job).action == "mismatch"
    code = runner.run_jobs(
        [job],
        tads="tads",
        skip_existing=True,
        continue_on_error=False,
        plan_only=False,
        dry_run=False,
        outputs_dir=tmp_path / "out",
        write_manifest=False,
        configs=[job.config],
        controls=job.controls,
    )
    assert code == 1
    assert json_path.read_bytes() == before_j
    assert json_path.stat().st_mtime_ns == mtime_j
    assert md.read_bytes() == before_m
    assert md.stat().st_mtime_ns == mtime_m


def test_raw_cli_doc_id_in_report_still_mismatches_when_adapter_canonicalizes():
    """TADS publishes canonical ids; raw CLI strings must not pass resume."""
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-mini")
    controls = BenchControls()
    report = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="etsi", doc_id="EN 300 468"),
        run=RunMetadata(
            scanner_version="0.6.0rc4",
            provider="openai",
            model="gpt-4.1-mini",
            analysis_mode=AnalysisMode.WHOLE_DOCUMENT,
            request_batching=RequestBatching.WHOLE_DOCUMENT,
            analysis_request_count=1,
            repair_request_count=0,
            chunk_section_counts=[1],
            coverage_complete=True,
        ),
        findings=[],
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=report,
        )
        == "doc_id"
    )
    good = report.model_copy(
        update={
            "document": DocumentIdentity(corpus="etsi", doc_id="ETSI EN 300 468")
        }
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=good,
        )
        == ""
    )


def test_other_identity_mismatches_unchanged_with_canonical_doc_id():
    from tads.schemas.report import (
        AnalysisMode,
        DocumentIdentity,
        Report,
        RequestBatching,
        RunMetadata,
    )

    config = resolve_named_config("openai-luna-low")
    controls = BenchControls(force_sections=True, max_output_tokens=4096)
    base = Report(
        schema_version="0.5.0",
        document=DocumentIdentity(corpus="etsi", doc_id="ETSI EN 300 468"),
        run=RunMetadata(
            scanner_version="0.6.0rc4",
            provider="openai",
            model="gpt-6-luna",
            analysis_mode=AnalysisMode.SECTION_AWARE,
            reasoning_effort="low",
            request_batching=RequestBatching.SECTION_CHUNKS,
            analysis_request_count=2,
            repair_request_count=0,
            chunk_section_counts=[2, 2],
            sections_analyzed=4,
            coverage_complete=True,
            max_output_tokens=4096,
        ),
        findings=[],
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=base,
        )
        == ""
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="3gpp",
            config=config,
            controls=controls,
            report=base,
        )
        == "doc_id"
    )
    wrong_corpus = base.model_copy(
        update={"document": DocumentIdentity(corpus="ietf", doc_id="ETSI EN 300 468")}
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_corpus,
        )
        == "corpus"
    )
    wrong_provider = base.model_copy(
        update={"run": base.run.model_copy(update={"provider": "gemini"})}
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_provider,
        )
        == "provider"
    )
    wrong_model = base.model_copy(
        update={"run": base.run.model_copy(update={"model": "gpt-4.1-mini"})}
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_model,
        )
        == "model"
    )
    wrong_effort = base.model_copy(
        update={"run": base.run.model_copy(update={"reasoning_effort": "none"})}
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_effort,
        )
        == "reasoning_effort"
    )
    wrong_batch = base.model_copy(
        update={
            "run": base.run.model_copy(
                update={"request_batching": RequestBatching.WHOLE_DOCUMENT}
            )
        }
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_batch,
        )
        == "request_batching"
    )
    wrong_mot = base.model_copy(
        update={"run": base.run.model_copy(update={"max_output_tokens": 2048})}
    )
    assert (
        metadata_mismatch(
            doc_id="EN 300 468",
            corpus="etsi",
            config=config,
            controls=controls,
            report=wrong_mot,
        )
        == "max_output_tokens"
    )


def test_output_prefix_still_uses_raw_cli_doc_id(tmp_path: Path):
    mini = resolve_named_config("openai-mini")
    controls = BenchControls()
    for doc in BENCH_DOCS:
        prefix = output_prefix(tmp_path, doc["doc_id"], mini, controls, "tag")
        assert prefix.name.startswith(f"{safe_filename(doc['doc_id'])}__")
        # Canonical report ids must not rewrite the prefix stem.
        if doc["doc_id"] == "EN 300 468":
            assert prefix.name.startswith("EN_300_468__")
            assert "ETSI" not in prefix.name.split("__")[0]


def test_scan_argv_still_passes_raw_cli_doc_id():
    doc = next(d for d in BENCH_DOCS if d["doc_id"] == "TS 29.061")
    cmd = scan_argv_for_config(
        tads="tads",
        source=Path("inputs") / doc["source"],
        doc_id=doc["doc_id"],
        corpus=doc["corpus"],
        config=resolve_named_config("openai-mini"),
        output_prefix_path=Path("outputs/x"),
        controls=BenchControls(),
        plan_only=False,
    )
    assert cmd[cmd.index("--doc-id") + 1] == "TS 29.061"


def test_manifest_documents_keep_cli_doc_ids_for_compatibility():
    """Manifest run definition stores CLI/bench ids; resume derives canonical."""
    from bench_common import document_defs

    defs = document_defs(BENCH_DOCS)
    by_id = {d["doc_id"]: d for d in defs}
    assert by_id["EN 300 468"]["corpus"] == "etsi"
    assert by_id["TS 29.061"]["corpus"] == "3gpp"
    # Compatibility still keys on CLI identifiers in the definition.
    expected = run_definition(
        tag="cmp",
        documents=BENCH_DOCS,
        configs=list(DEFAULT_RUN_CONFIGS),
        controls=BenchControls(),
        git_commit="abc",
        git_dirty=False,
    )
    assert expected["documents"][2]["doc_id"] == "EN 300 468"
    assert expected_report_doc_id("EN 300 468", "etsi") == "ETSI EN 300 468"
