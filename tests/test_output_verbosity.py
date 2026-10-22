# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Terminal verbosity levels and planning-note aggregation (issue #29)."""

from __future__ import annotations

import inspect
import io
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

try:
    from click.utils import strip_ansi
except ImportError:  # Typer 0.27+ vendors Click; no top-level `click` package
    from typer._click.utils import strip_ansi

from tads.cli import app, plan_cmd, scan_cmd
from tads.llm.base import ChatMessage, LLMResponse
from tads.llm.openai_models import LUNA_LONG_CONTEXT_INPUT_THRESHOLD
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.output import (
    InvalidOutputLevelError,
    OutputLevel,
    RequestView,
    format_plan_lines,
    format_pricing_line,
    resolve_output_level,
    should_emit_progress,
)
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS
from tads.parsing.scope import AnalysisScope
from tads.pipeline import _progress, plan_scan, run_scan
from tads.progress import ScanProgressReporter
from tads.schemas.cost import CostBudget
from tads.schemas.report import AnalysisMode

CANARY = "VERBOSITY_SECRET_CANARY_xyz"
BODY_CANARY = "VERBOSITY_DOC_BODY_CANARY_xyz"
PROMPT_CANARY = "VERBOSITY_PROMPT_CANARY_xyz"
URL_CANARY = "https://evil.example/steal?token=abc#frag"
KEY_CANARY = "sk-test-verbosity-key-should-never-print"


def _sections_doc(n: int, *, extra: str = "") -> str:
    parts = [
        f"{i}. Section {i}\n\n"
        f"Unique quote {i:02d} timestamps are 32-bit seconds for tests.\n"
        for i in range(1, n + 1)
    ]
    text = "\n".join(parts)
    if extra:
        text += "\n" + extra
    return text


def _write_doc(path: Path, n: int = 2, extra: str = "") -> Path:
    path.write_text(_sections_doc(n, extra=extra), encoding="utf-8")
    return path


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


def test_resolve_output_level_default_and_conflicts():
    assert resolve_output_level() is OutputLevel.NORMAL
    assert resolve_output_level(quiet=True) is OutputLevel.QUIET
    assert resolve_output_level(verbose=True) is OutputLevel.VERBOSE
    assert resolve_output_level(debug=True) is OutputLevel.DEBUG
    with pytest.raises(InvalidOutputLevelError, match="Conflicting output options"):
        resolve_output_level(quiet=True, verbose=True)
    with pytest.raises(InvalidOutputLevelError, match="Conflicting output options"):
        resolve_output_level(quiet=True, debug=True)
    with pytest.raises(InvalidOutputLevelError, match="Conflicting output options"):
        resolve_output_level(verbose=True, debug=True)


def _assert_verbosity_options_declared(fn) -> None:
    params = inspect.signature(fn).parameters
    for name in ("quiet", "verbose", "debug"):
        option = params[name].default
        decls = " ".join(getattr(option, "param_decls", ()) or ())
        assert f"--{name}" in decls
        help_text = (option.help or "").lower()
        assert "mutually exclusive" in help_text
    assert "save-raw-on-error" not in (params["quiet"].default.help or "").lower()


def _assert_verbosity_options_rendered(help_out: str) -> None:
    help_out = strip_ansi(help_out).lower()
    assert "--quiet" in help_out
    assert "--verbose" in help_out
    assert "--debug" in help_out


def test_help_documents_verbosity_options():
    for command, fn in (("plan", plan_cmd), ("scan", scan_cmd)):
        _assert_verbosity_options_declared(fn)
        result = CliRunner().invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        help_out = strip_ansi(result.output).lower()
        _assert_verbosity_options_rendered(help_out)


def test_help_documents_verbosity_options_with_forced_color(monkeypatch):
    """GitHub Actions sets GITHUB_ACTIONS, which makes Typer force a Rich
    terminal. OptionHighlighter then inserts CSI between the two hyphens of
    ``--quiet`` (``-`` + ANSI + ``-quiet``), so raw substring checks fail.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("_TYPER_FORCE_DISABLE_TERMINAL", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    # FORCE_TERMINAL is computed at typer.rich_utils import time.
    monkeypatch.setattr("typer.rich_utils.FORCE_TERMINAL", True)

    for command, fn in (("plan", plan_cmd), ("scan", scan_cmd)):
        _assert_verbosity_options_declared(fn)
        result = CliRunner().invoke(app, [command, "--help"], color=True)
        assert result.exit_code == 0, result.output
        assert "\x1b[" in result.output
        help_out = strip_ansi(result.output).lower()
        _assert_verbosity_options_rendered(help_out)


def test_conflicting_verbosity_rejected_before_provider(tmp_path: Path, restore_mock_provider):
    src = _write_doc(tmp_path / "doc.txt")
    seen = {"complete": 0}

    class _Guard(MockProvider):
        def complete(self, *args, **kwargs):
            seen["complete"] += 1
            raise AssertionError("provider must not be called on a flag conflict")

    register_provider(_Guard())
    for flags in (
        ["--quiet", "--verbose"],
        ["--quiet", "--debug"],
        ["--verbose", "--debug"],
    ):
        result = CliRunner().invoke(
            app,
            [
                "scan",
                str(src),
                "--doc-id",
                "RFC9999",
                "--provider",
                "mock",
                "-y",
                "--overwrite",
                "-o",
                str(tmp_path / "conflict"),
                *flags,
            ],
        )
        assert result.exit_code == 2, result.output
        assert "Conflicting output options" in result.output
        assert "Traceback" not in result.output
        assert seen["complete"] == 0
        assert not (tmp_path / "conflict.json").exists()

    plan_result = CliRunner().invoke(
        app,
        ["plan", str(src), "--doc-id", "RFC9999", "--provider", "mock", "--quiet", "--debug"],
    )
    assert plan_result.exit_code == 2
    assert "Conflicting output options" in plan_result.output
    assert "Traceback" not in plan_result.output


def test_normal_plan_summarizes_repeated_luna_notes():
    plan = plan_scan(
        _sections_doc(20),
        doc_id="RFC9999",
        corpus="ietf",
        provider="openai",
        model="gpt-6-luna",
        force_mode=AnalysisMode.SECTION_AWARE,
        max_sections_per_request=2,
    )
    stored = "\n".join(plan.cost_estimate.notes)
    assert stored.count("request ") >= 2
    assert "untrusted framing" in stored
    lines = format_plan_lines(plan, corpus="ietf", level=OutputLevel.NORMAL)
    text = "\n".join(lines)
    assert "RFC9999" in text
    assert "requests:" in text
    assert "section chunks" in text
    assert "up to 2 sections per request" in text
    assert "GPT-6 Luna short-context rates" in text
    assert text.count("GPT-6 Luna short-context rates") == 1
    assert text.count("Cached-input discounts are not modeled.") == 1
    assert "this estimate is a lower bound" not in text
    assert "reasoning_effort: none" in text
    assert text.count("6000 shared input tokens") == 1 or text.count("6,000 shared") == 1
    assert text.count("Repair, correction, and adaptive-recovery") == 1
    assert "untrusted framing" not in text
    assert "native structured-output" not in text
    assert "request 1:" not in text
    verbose = "\n".join(
        format_plan_lines(plan, corpus="ietf", level=OutputLevel.VERBOSE)
    )
    assert "request 1:" in verbose
    assert "input /" in verbose
    assert "sections:" in verbose


def test_mixed_short_and_long_luna_pricing_summary():
    views = (
        RequestView(index=1, input_tokens=10_000, output_tokens=1_000, section_count=8),
        RequestView(index=2, input_tokens=10_000, output_tokens=1_000, section_count=8),
        RequestView(
            index=3,
            input_tokens=LUNA_LONG_CONTEXT_INPUT_THRESHOLD + 1,
            output_tokens=1_000,
            section_count=8,
        ),
    )
    line = format_pricing_line("openai", "gpt-6-luna", views)
    assert "2 requests use GPT-6 Luna short-context rates" in line
    assert "1 request uses GPT-6 Luna long-context rates" in line


def test_unknown_pricing_stays_prominent():
    plan = plan_scan(
        _sections_doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    normal = "\n".join(format_plan_lines(plan, corpus="ietf", level=OutputLevel.NORMAL))
    quiet = "\n".join(format_plan_lines(plan, corpus="ietf", level=OutputLevel.QUIET))
    assert "USD estimate unavailable" in normal
    assert "mock:mock-model" in normal
    assert "USD estimate unavailable" in quiet
    assert "Analyzing" not in quiet


def _human_and_json_notes(plan, *, requested=None):
    humans = {
        level: "\n".join(
            format_plan_lines(
                plan,
                corpus="ietf",
                level=level,
                requested_reasoning_effort=requested,
            )
        )
        for level in (
            OutputLevel.QUIET,
            OutputLevel.NORMAL,
            OutputLevel.VERBOSE,
            OutputLevel.DEBUG,
        )
    }
    return humans, list(plan.cost_estimate.notes)


def test_luna_none_does_not_claim_reasoning_lower_bound():
    plan = plan_scan(
        _sections_doc(2),
        doc_id="RFC9999",
        provider="openai",
        model="gpt-6-luna",
        force_mode=AnalysisMode.SECTION_AWARE,
        reasoning_effort="none",
    )
    humans, stored = _human_and_json_notes(plan, requested="none")
    assert plan.cost_estimate.notes  # JSON notes stay stored
    assert any("Effective reasoning_effort for this plan: none" in n for n in stored)
    assert not any(
        "Non-none reasoning_effort makes the USD estimate a lower bound" in n
        for n in stored
    )
    for level, text in humans.items():
        assert "this estimate is a lower bound" not in text, level
        if level >= OutputLevel.NORMAL:
            assert "reasoning_effort: none" in text


def test_luna_low_shows_reasoning_lower_bound_at_every_human_level():
    plan = plan_scan(
        _sections_doc(2),
        doc_id="RFC9999",
        provider="openai",
        model="gpt-6-luna",
        force_mode=AnalysisMode.SECTION_AWARE,
        reasoning_effort="low",
    )
    humans, stored = _human_and_json_notes(plan, requested="low")
    assert any(
        "Non-none reasoning_effort makes the USD estimate a lower bound" in n
        for n in stored
    )
    for level, text in humans.items():
        assert "this estimate is a lower bound" in text, level
        if level >= OutputLevel.NORMAL:
            assert "reasoning_effort: low" in text


def test_non_reasoning_model_omits_reasoning_lower_bound():
    plan = plan_scan(
        _sections_doc(2),
        doc_id="RFC9999",
        provider="openai",
        model="gpt-4.1-mini",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    humans, stored = _human_and_json_notes(plan)
    assert not any("Effective reasoning_effort" in n for n in stored)
    for text in humans.values():
        assert "this estimate is a lower bound" not in text
        assert "reasoning_effort:" not in text


def test_unknown_pricing_model_omits_reasoning_lower_bound():
    plan = plan_scan(
        _sections_doc(2),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    humans, stored = _human_and_json_notes(plan)
    assert not any("Effective reasoning_effort" in n for n in stored)
    for text in humans.values():
        assert "this estimate is a lower bound" not in text
        assert "USD estimate unavailable" in text


def test_debug_plan_has_sanitized_diagnostics_not_bodies():
    plan = plan_scan(
        _sections_doc(4, extra=BODY_CANARY),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        max_sections_per_request=2,
    )
    text = "\n".join(format_plan_lines(plan, corpus="ietf", level=OutputLevel.DEBUG))
    assert f"shared_overhead_tokens={REQUEST_OVERHEAD_TOKENS}" in text
    assert "structured_output native_json_schema=" in text
    assert "opaque=" in text
    assert BODY_CANARY not in text
    assert CANARY not in text
    assert "untrusted framing" not in text.split("verbose:")[0]


def test_quiet_plan_keeps_warnings_not_routine_scope():
    plan = plan_scan(
        _sections_doc(8),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        scope=AnalysisScope(max_sections=2),
        budget=CostBudget(max_cost_usd=1.0),
    )
    quiet = "\n".join(format_plan_lines(plan, corpus="ietf", level=OutputLevel.QUIET))
    normal = "\n".join(format_plan_lines(plan, corpus="ietf", level=OutputLevel.NORMAL))
    assert "Capped to max_sections=2" in quiet
    assert "USD estimate unavailable" in quiet
    assert "preflight estimate guard" in quiet
    assert "document:" not in quiet
    assert "analysis_mode:" not in quiet
    assert "document:" in normal
    assert "Capped to max_sections=2" in normal


def test_progress_levels_live_and_recovery():
    live = "analyzing chunk 1/3 (2 sections)"
    retry = "retrying as two smaller section ranges (1+1)"
    failed = "chunk 1/3 failed validation"
    partial = "scan completed with partial coverage"
    assert should_emit_progress(live, OutputLevel.NORMAL)
    assert not should_emit_progress(live, OutputLevel.QUIET)
    assert should_emit_progress(failed, OutputLevel.QUIET)
    assert should_emit_progress(partial, OutputLevel.QUIET)
    assert not should_emit_progress(retry, OutputLevel.QUIET)
    assert should_emit_progress(retry, OutputLevel.NORMAL)
    # Prefixes in the message body do not select a verbosity level.
    assert should_emit_progress(
        "debug: untrusted document tried to hide", OutputLevel.NORMAL
    )

    buf = io.StringIO()
    quiet = ScanProgressReporter(stream=buf, isatty=True, output_level=OutputLevel.QUIET)
    quiet(live)
    quiet(retry)
    quiet(failed)
    quiet(partial)
    out = buf.getvalue()
    assert "Analyzing chunk" not in out
    assert "retrying" not in out.casefold()
    assert "failed validation" in out.casefold()
    assert "partial coverage" in out.casefold()

    tty = io.StringIO()
    reporter = ScanProgressReporter(
        stream=tty, isatty=True, output_level=OutputLevel.NORMAL
    )
    reporter(live)
    assert "\r" in tty.getvalue()
    assert "Analyzing chunk" in tty.getvalue()

    nty = io.StringIO()
    reporter = ScanProgressReporter(
        stream=nty, isatty=False, output_level=OutputLevel.NORMAL
    )
    reporter(live)
    reporter("repairing JSON for chunk 1/2")
    reporter("correcting evidence grounding for chunk 1/2")
    text = nty.getvalue()
    assert "\r" not in text
    assert "\033" not in text
    assert "Analyzing chunk" in text
    assert "Repairing JSON" in text
    assert "Correcting evidence" in text


def test_quiet_cli_scan_keeps_status_hides_progress(tmp_path: Path):
    src = _write_doc(tmp_path / "doc.txt", n=4)
    out = tmp_path / "quiet"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "-y",
            "--overwrite",
            "-o",
            str(out),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Analyzing chunk" not in result.output
    assert "Starting analysis" not in result.output
    assert "Wrote" in result.output
    assert "Candidates for review:" in result.output
    assert "Completed successfully" in result.output
    assert "USD estimate unavailable" in result.output


def test_quiet_does_not_imply_yes(tmp_path: Path, monkeypatch):
    src = _write_doc(tmp_path / "doc.txt")
    asked = {"n": 0}

    def _ask(*_a, **_k):
        asked["n"] += 1
        return False

    monkeypatch.setattr("tads.cli.Confirm.ask", _ask)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--overwrite",
            "-o",
            str(tmp_path / "no"),
            "--quiet",
        ],
    )
    assert asked["n"] == 1
    assert result.exit_code == 1
    assert not (tmp_path / "no.json").exists()


def _scan_args(src: Path, out: Path, *extra: str) -> list[str]:
    return [
        "scan",
        str(src),
        "--doc-id",
        "RFC9999",
        "--provider",
        "mock",
        "--force-sections",
        "--max-sections-per-request",
        "2",
        "-y",
        "--overwrite",
        "-o",
        str(out),
        *extra,
    ]


def test_reports_and_payloads_invariant_across_verbosity(
    tmp_path: Path, restore_mock_provider
):
    src = _write_doc(tmp_path / "doc.txt", n=4)
    captured: list[list[tuple[str, str]]] = []

    class _Rec(MockProvider):
        def complete(self, messages, **kwargs):
            captured.append([(m.role, m.content) for m in messages])
            return super().complete(messages, **kwargs)

    register_provider(_Rec())
    dumps = []
    mds = []
    for i, flag in enumerate(([], ["--quiet"], ["--verbose"], ["--debug"])):
        captured.clear()
        out = tmp_path / f"v{i}"
        result = CliRunner().invoke(app, _scan_args(src, out, *flag))
        assert result.exit_code == 0, result.output
        dumps.append(json.loads((tmp_path / f"v{i}.json").read_text(encoding="utf-8")))
        mds.append((tmp_path / f"v{i}.md").read_text(encoding="utf-8"))
        if i == 0:
            baseline = list(captured)
        else:
            assert captured == baseline
        if flag == ["--debug"]:
            assert "shared_overhead_tokens=" in result.output
            assert not (tmp_path / f"v{i}.raw.txt").exists()
        if flag == ["--verbose"]:
            assert "request 1:" in result.output
            assert "packing:" in result.output
        if flag == ["--quiet"]:
            assert "Analyzing chunk" not in result.output

    def _strip(report: dict) -> dict:
        run = dict(report["run"])
        run.pop("started_at", None)
        run.pop("completed_at", None)
        run.pop("duration_ms", None)
        return {
            "schema_version": report["schema_version"],
            "findings": report["findings"],
            "cost_estimate": report["cost_estimate"],
            "run": {
                k: run[k]
                for k in (
                    "analysis_mode",
                    "request_batching",
                    "analysis_request_count",
                    "repair_request_count",
                    "chunk_section_counts",
                    "prompt_framework_version",
                    "analysis_status",
                    "coverage_complete",
                )
                if k in run
            },
        }

    for other in dumps[1:]:
        assert _strip(other) == _strip(dumps[0])
    ts_re = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+")
    for other in mds[1:]:
        assert ts_re.sub("TS", other) == ts_re.sub("TS", mds[0])
    assert dumps[0]["schema_version"] == "0.6.0"


def test_debug_does_not_save_raw_or_print_secrets(
    tmp_path: Path, restore_mock_provider
):
    src = _write_doc(tmp_path / "doc.txt", n=2, extra=BODY_CANARY + "\n" + KEY_CANARY)
    out = tmp_path / "dbg"
    result = CliRunner().invoke(
        app,
        _scan_args(src, out, "--debug"),
    )
    assert result.exit_code == 0, result.output
    blob = result.output
    assert BODY_CANARY not in blob
    assert KEY_CANARY not in blob
    assert CANARY not in blob
    assert URL_CANARY not in blob
    assert "sk-" not in blob
    assert not list(tmp_path.glob("*.raw*.txt"))
    report = json.loads((tmp_path / "dbg.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "0.6.0"


class _Scripted(MockProvider):
    def __init__(self, payloads: list) -> None:
        super().__init__()
        self._payloads = list(payloads)
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages, **kwargs):
        from tads.llm.base import reject_unsupported_reasoning_effort
        from tads.schemas.cost import TokenUsage

        reject_unsupported_reasoning_effort(kwargs.get("reasoning_effort"))
        self.calls.append(list(messages))
        item = self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item()
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(
            content=content,
            model=kwargs.get("model") or self.default_model(),
            usage=TokenUsage(
                input_tokens=11 + len(self.calls),
                output_tokens=7 + len(self.calls),
            ),
        )


def _invalid():
    return {"findings": [{"finding_type": "time_assurance_gap", "title": "bad"}]}


def test_adaptive_recovery_verbosity_and_partial(
    tmp_path: Path, restore_mock_provider
):
    empty = {"findings": []}
    payloads = [empty, _invalid(), empty, _invalid()]
    register_provider(_Scripted(payloads))
    src = _write_doc(tmp_path / "doc.txt", n=4)
    results = {}
    for flag in ("", "--quiet", "--verbose", "--debug"):
        extra = [flag] if flag else []
        out = tmp_path / f"rec{flag or 'normal'}"
        result = CliRunner().invoke(app, _scan_args(src, out, *extra))
        results[flag or "normal"] = result
        assert "Traceback" not in result.output
        register_provider(_Scripted(payloads))

    normal = results["normal"].output
    assert results["normal"].exit_code == 3
    assert "failed validation" in normal.casefold()
    assert "retrying as two smaller" in normal.casefold()
    assert "partial" in normal.casefold()
    quiet = results["--quiet"].output
    assert results["--quiet"].exit_code == 3
    assert "failed validation" in quiet.casefold() or "partial" in quiet.casefold()
    assert "Analyzing chunk" not in quiet
    assert "retrying as two smaller" not in quiet.casefold()
    debug = results["--debug"].output
    assert "recovery eligible failure=" in debug
    assert CANARY not in debug
    assert results["--quiet"].output.count("partial") >= 1


def test_budget_failure_and_unavailable_pricing_cli(tmp_path: Path):
    src = _write_doc(tmp_path / "doc.txt", n=4)
    unknown = CliRunner().invoke(
        app,
        [
            "plan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-cost-usd",
            "1",
        ],
    )
    assert unknown.exit_code == 0, unknown.output
    assert "USD estimate unavailable" in unknown.output
    assert "preflight estimate guard" in unknown.output

    over = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "openai",
            "--model",
            "gpt-4.1-mini",
            "--force-sections",
            "--max-cost-usd",
            "0.0000001",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "over"),
        ],
    )
    assert over.exit_code == 2, over.output
    assert "Traceback" not in over.output
    assert "exceeds" in over.output.lower() or "budget" in over.output.lower()
    assert not (tmp_path / "over.json").exists()


def test_interruption_clean_at_quiet(tmp_path: Path, restore_mock_provider):
    src = _write_doc(tmp_path / "doc.txt")
    register_provider(_Scripted([KeyboardInterrupt()]))
    result = CliRunner().invoke(
        app,
        _scan_args(src, tmp_path / "int", "--quiet"),
    )
    assert result.exit_code == 130
    assert "Interrupted after" in result.output
    assert "Traceback" not in result.output


def test_json_plan_notes_unchanged_by_verbosity(tmp_path: Path):
    src = _write_doc(tmp_path / "doc.txt", n=16)
    args = [
        "plan",
        str(src),
        "--doc-id",
        "RFC9999",
        "--provider",
        "openai",
        "--model",
        "gpt-6-luna",
        "--force-sections",
        "--max-sections-per-request",
        "2",
        "--json",
    ]
    payloads = {}
    for extra in ([], ["--quiet"], ["--verbose"], ["--debug"]):
        result = CliRunner().invoke(app, args + extra)
        assert result.exit_code == 0, result.output
        stdout = result.stdout or ""
        json.loads(stdout)
        assert not stdout.lstrip().startswith("RFC9999")
        assert "\nverbose:" not in stdout
        assert "\ndebug:" not in stdout
        assert "this estimate is a lower bound" not in stdout
        payloads[tuple(extra)] = json.loads(stdout)
    baseline = payloads[()]
    for extra, data in payloads.items():
        assert data == baseline, extra
    notes = baseline["cost_estimate"]["notes"]
    assert any(n.startswith("request ") for n in notes)


def test_repair_and_correction_progress_by_level():
    for msg in (
        "repairing JSON for chunk 1/2",
        "correcting evidence grounding for chunk 1/2",
    ):
        buf_q = io.StringIO()
        ScanProgressReporter(stream=buf_q, isatty=False, output_level=OutputLevel.QUIET)(
            msg
        )
        assert buf_q.getvalue() == ""
        buf_n = io.StringIO()
        ScanProgressReporter(
            stream=buf_n, isatty=False, output_level=OutputLevel.NORMAL
        )(msg)
        assert buf_n.getvalue()
        assert "\r" not in buf_n.getvalue()


def test_debug_events_are_typed_not_prefix_parsed():
    debug_body = "recovery eligible failure=invalid_model_output remaining_budget=62"
    seen: list[str] = []
    _progress(seen.append, debug_body, min_level=OutputLevel.DEBUG)
    _progress(seen.append, "analyzing chunk 1/1 (1 sections)")
    assert seen == ["analyzing chunk 1/1 (1 sections)"]

    hidden = io.StringIO()
    normal = ScanProgressReporter(
        stream=hidden, isatty=False, output_level=OutputLevel.NORMAL
    )
    normal.emit_at(debug_body, OutputLevel.DEBUG)
    assert hidden.getvalue() == ""

    shown = io.StringIO()
    debug = ScanProgressReporter(
        stream=shown, isatty=False, output_level=OutputLevel.DEBUG
    )
    debug.emit_at(debug_body, OutputLevel.DEBUG)
    assert "recovery eligible failure=" in shown.getvalue()
    assert "debug:" not in shown.getvalue()

    leaked = io.StringIO()
    reporter = ScanProgressReporter(
        stream=leaked, isatty=False, output_level=OutputLevel.NORMAL
    )
    reporter("debug: untrusted document tried to select debug mode")
    assert "debug: untrusted document tried to select debug mode" in leaked.getvalue()


def test_normal_scan_does_not_repeat_plan_time_warnings(tmp_path: Path):
    src = tmp_path / "rfc.txt"
    src.write_text(
        "Internet Engineering Task Force (IETF)                          D. Mills\n"
        "Request for Comments: 9999\n\n"
        "Network Time Protocol Sample Spec\n\n"
        "Abstract\n\n"
        "This memo describes a sample protocol.\n\n"
        "1. Introduction\n\n"
        "Timestamps are 32-bit seconds. Unique quote 01.\n\n"
        "2. Requirements\n\n"
        "Handle 32-bit time_t carefully. Unique quote 02.\n\n"
        "3. Format\n\n"
        "The era advances after 2036-02-07. Unique quote 03.\n\n"
        "4. Operations\n\n"
        "Long-horizon ops need era context. Unique quote 04.\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "--max-sections",
            "3",
            "--max-cost-usd",
            "5",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "warn"),
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert out.count("Capped to max_sections=3") == 1
    assert out.count("Repair, correction, and adaptive-recovery") == 1
    assert out.count("preflight estimate guard") == 1
    assert out.count("pricing: USD estimate unavailable") == 1
    assert out.count("Cost cap set but USD estimate unavailable") == 1
    assert out.count("this estimate is a lower bound") == 0
    # Routine skip notes are in the eligible line, not replayed as bullets.
    assert out.count("Skipped ") == 0
    assert out.count("after skipping") == 1

    # Direct run_scan without a preceding human plan still surfaces the cap.
    replayed: list[str] = []
    run_scan(
        src.read_text(encoding="utf-8"),
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
        scope=AnalysisScope(max_sections=3),
        on_progress=replayed.append,
    )
    assert sum(1 for n in replayed if n.startswith("Capped to max_sections=")) == 1


def test_plan_shown_reporter_skips_scope_echo_keeps_execution_warnings():
    buf = io.StringIO()
    reporter = ScanProgressReporter(
        stream=buf, isatty=False, output_level=OutputLevel.NORMAL
    )
    reporter.mark_plan_shown()
    reporter("Capped to max_sections=3.")
    reporter("Skipped 1 front-matter/TOC section(s) (use --include-front-matter to keep).")
    reporter("chunk 1/2 failed validation")
    reporter("scan completed with partial coverage")
    text = buf.getvalue()
    assert "Capped to max_sections" not in text
    assert "Skipped " not in text
    assert "failed validation" in text.casefold()
    assert "partial coverage" in text.casefold()


class _OfflineOpenAI(MockProvider):
    """Offline stand-in so Luna reasoning scans need no live HTTP."""

    provider_id = "openai"
    display_name = "OpenAI"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str:
        return "gpt-6-luna"

    def context_window_tokens(self, model=None):
        from tads.llm.openai_models import openai_model_policy

        return openai_model_policy(model or self.default_model()).context_window_tokens

    def complete(self, messages, *, model=None, max_output_tokens=4096, reasoning_effort=None):
        from tads.schemas.cost import TokenUsage

        _ = (max_output_tokens, reasoning_effort)
        prompt = "\n".join(m.content for m in messages)
        payload = self._payload_for_prompt(prompt)
        content = json.dumps(payload)
        return LLMResponse(
            content=content,
            usage=TokenUsage(
                input_tokens=self.estimate_tokens(prompt),
                output_tokens=self.estimate_tokens(content),
            ),
            model=model or self.default_model(),
        )


def test_offline_luna_scan_reasoning_warning_counts(tmp_path: Path):
    from tads.llm.providers.openai_provider import OpenAIProvider

    src = _write_doc(tmp_path / "doc.txt", n=4)
    register_provider(_OfflineOpenAI())
    try:
        none = CliRunner().invoke(
            app,
            [
                "scan",
                str(src),
                "--doc-id",
                "RFC9999",
                "--provider",
                "openai",
                "--model",
                "gpt-6-luna",
                "--reasoning-effort",
                "none",
                "--force-sections",
                "--max-sections-per-request",
                "2",
                "--max-cost-usd",
                "5",
                "-y",
                "--overwrite",
                "-o",
                str(tmp_path / "none"),
            ],
        )
        assert none.exit_code == 0, none.output
        assert none.output.count("reasoning_effort: none") == 1
        assert none.output.count("this estimate is a lower bound") == 0
        assert none.output.count("Cached-input discounts are not modeled.") == 1
        assert none.output.count("preflight estimate guard") == 1

        low = CliRunner().invoke(
            app,
            [
                "scan",
                str(src),
                "--doc-id",
                "RFC9999",
                "--provider",
                "openai",
                "--model",
                "gpt-6-luna",
                "--reasoning-effort",
                "low",
                "--force-sections",
                "--max-sections-per-request",
                "2",
                "--max-cost-usd",
                "5",
                "-y",
                "--overwrite",
                "-o",
                str(tmp_path / "low"),
            ],
        )
        assert low.exit_code == 0, low.output
        assert low.output.count("reasoning_effort: low") == 1
        assert low.output.count("this estimate is a lower bound") == 1
    finally:
        register_provider(OpenAIProvider())
