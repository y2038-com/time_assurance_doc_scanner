# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tests for scan console progress and runtime reporting."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.progress import (
    ProgressClocks,
    ScanProgressReporter,
    format_duration,
    format_utc_timestamp,
    is_progress_live_message,
)
from tads.schemas.report import Report


class _FakeClock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 10, 17, 5, 16, 7, 123000, tzinfo=timezone.utc)
        self.mono = 1000.0

    def wall_now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def advance(self, *, seconds: float = 0.0, wall_seconds: float | None = None) -> None:
        self.mono += seconds
        delta = wall_seconds if wall_seconds is not None else seconds
        self.wall = self.wall + timedelta(seconds=delta)


def _clocks(fake: _FakeClock) -> ProgressClocks:
    return ProgressClocks(wall_now=fake.wall_now, monotonic=fake.monotonic)


def test_utc_timestamp_format_millisecond_precision_and_z():
    moment = datetime(2026, 10, 17, 5, 16, 7, 123456, tzinfo=timezone.utc)
    assert format_utc_timestamp(moment) == "[2026-10-17T05:16:07.123Z]"
    # Naive treated as UTC.
    naive = datetime(2026, 10, 17, 5, 16, 7, 123000)
    assert format_utc_timestamp(naive) == "[2026-10-17T05:16:07.123Z]"
    # Non-UTC converted.
    eastern = datetime(2026, 10, 17, 1, 16, 7, 123000, tzinfo=timezone(timedelta(hours=-4)))
    assert format_utc_timestamp(eastern) == "[2026-10-17T05:16:07.123Z]"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    (
        (0.0, "0.0s"),
        (0.04, "0.0s"),
        (0.05, "0.1s"),
        (0.3, "0.3s"),
        (0.99, "1.0s"),
        (42.84, "42.8s"),
        (42.85, "42.9s"),
        (60.0, "1m 00.0s"),
        (68.4, "1m 08.4s"),
        (728.4, "12m 08.4s"),
        (1842.8, "30m 42.8s"),
        (1330.5, "22m 10.5s"),
        (3600.0, "1h 0m 0.0s"),
        (4940.5, "1h 22m 20.5s"),
        (-3.0, "0.0s"),
    ),
)
def test_duration_formatting_boundaries(seconds: float, expected: str):
    assert format_duration(seconds) == expected


def test_monotonic_duration_ignores_wall_clock_jumps():
    fake = _FakeClock()
    reporter = ScanProgressReporter(
        stream=io.StringIO(), clocks=_clocks(fake), isatty=False
    )
    reporter.mark_start()
    # Wall jumps backward; monotonic advances normally.
    fake.advance(seconds=12.5, wall_seconds=-3600)
    assert abs(reporter.elapsed_seconds() - 12.5) < 1e-9
    assert format_duration(reporter.elapsed_seconds()) == "12.5s"


def test_live_message_classification():
    assert is_progress_live_message("analyzing chunk 1/2 (8 sections)")
    assert is_progress_live_message("correcting evidence grounding for chunk 1/2")
    assert is_progress_live_message("repairing JSON for chunk 1/2")
    assert is_progress_live_message("analyzing whole document (scoped)")
    assert not is_progress_live_message("done: 3 candidates")
    assert not is_progress_live_message("mode=section_aware model=mock-model")


def test_tty_live_line_uses_carriage_return_and_clears_shorter_replacement():
    buf = io.StringIO()
    fake = _FakeClock()
    reporter = ScanProgressReporter(stream=buf, clocks=_clocks(fake), isatty=True)
    reporter.mark_start()
    reporter("analyzing chunk 1/10 (8 sections)")
    fake.advance(seconds=1.0)
    first = buf.getvalue()
    assert "\r" in first
    assert "\n" not in first.replace("\r", "")
    assert "Analyzing chunk 1/10 (8 sections, 10.0%, elapsed 0.0s)" in first
    long_width = len(
        "[2026-10-17T05:16:07.123Z] Analyzing chunk 1/10 (8 sections, 10.0%, elapsed 0.0s)"
    )
    # Shorter follow-up must pad/clear leftovers.
    fake.wall = fake.wall + timedelta(milliseconds=1)
    reporter("analyzing chunk 2/10 (1 sections)")
    text = buf.getvalue()
    assert text.count("\r") >= 2
    assert "Analyzing chunk 2/10 (1 sections, 20.0%, elapsed 1.0s)" in text
    # Final segment after last CR is at least as wide as the prior live line.
    last = text.rsplit("\r", 1)[-1]
    assert len(last) >= long_width
    assert last.endswith(" ") or "elapsed 1.0s)" in last


def test_non_tty_emits_one_line_per_event_without_cr_or_ansi():
    buf = io.StringIO()
    fake = _FakeClock()
    reporter = ScanProgressReporter(stream=buf, clocks=_clocks(fake), isatty=False)
    reporter.mark_start()
    reporter("analyzing chunk 48/132 (8 sections)")
    fake.advance(seconds=7.0)
    fake.wall = fake.wall + timedelta(seconds=7)
    reporter("correcting evidence grounding for chunk 48/132")
    fake.advance(seconds=6.0)
    fake.wall = fake.wall + timedelta(seconds=6)
    reporter("analyzing chunk 49/132 (8 sections)")
    out = buf.getvalue()
    assert "\r" not in out
    assert "\033" not in out
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("Analyzing chunk 48/132 (8 sections)")
    assert "elapsed" not in lines[0]
    assert lines[1].endswith("Correcting evidence for chunk 48/132")
    assert "elapsed" not in lines[1]
    assert lines[2].endswith("Analyzing chunk 49/132 (8 sections)")


def test_finalize_live_before_permanent_messages():
    buf = io.StringIO()
    fake = _FakeClock()
    reporter = ScanProgressReporter(stream=buf, clocks=_clocks(fake), isatty=True)
    reporter.mark_start()
    reporter("analyzing chunk 1/2 (2 sections)")
    assert reporter._live_active
    reporter("repairing JSON for chunk 1/2")
    # Repair is also live — still active.
    assert reporter._live_active
    reporter("saved raw model output")
    assert not reporter._live_active
    assert "Saved raw diagnostic output" in buf.getvalue()
    assert buf.getvalue().endswith("\n")


def test_success_failure_interrupt_summaries():
    fake = _FakeClock()
    buf = io.StringIO()
    ok = ScanProgressReporter(stream=buf, clocks=_clocks(fake), isatty=False)
    ok.mark_start()
    fake.advance(seconds=1842.8)
    ok.success()
    assert "Completed successfully in 30m 42.8s" in buf.getvalue()

    fake2 = _FakeClock()
    buf2 = io.StringIO()
    bad = ScanProgressReporter(stream=buf2, clocks=_clocks(fake2), isatty=False)
    bad.mark_start()
    fake2.advance(seconds=1330.5)
    bad.fail("Finding item 0: invalid field evidence.quote")
    text = buf2.getvalue()
    assert "Scan failed: Finding item 0: invalid field evidence.quote" in text
    assert "Failed after 22m 10.5s" in text

    fake3 = _FakeClock()
    buf3 = io.StringIO()
    interrupted = ScanProgressReporter(
        stream=buf3, clocks=_clocks(fake3), isatty=True
    )
    interrupted.mark_start()
    interrupted("analyzing whole document (scoped)")
    fake3.advance(seconds=5.0)
    interrupted.interrupted()
    out = buf3.getvalue()
    assert "Interrupted after 5.0s" in out
    assert out.endswith("\n")


def _write_sample(path: Path) -> None:
    path.write_text(
        "1. Introduction\nUnix time and year 2038.\n"
        "2. Requirements\nHandle 32-bit time_t carefully.\n",
        encoding="utf-8",
    )


def test_cli_successful_whole_document_reports_runtime(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"
    # Force non-TTY for stable line capture.
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False, clocks=kwargs.get("clocks")
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "SMOKE1",
            "--corpus",
            "ietf",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "smoke"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Completed successfully in " in result.output
    assert "Starting analysis (" in result.output or "Analyzing whole document" in result.output
    report = Report.model_validate(
        json.loads((out / "smoke.json").read_text(encoding="utf-8"))
    )
    assert report.schema_version == "0.5.0"
    dumped = report.model_dump(mode="json")
    assert "console_elapsed" not in dumped
    assert "runtime" not in dumped.get("run", {})


def test_cli_chunked_scan_progress_and_runtime(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    # Enough sections to force section-aware with force-sections.
    parts = [f"{i}. Section {i}\nText about clocks and epochs.\n" for i in range(1, 6)]
    sample.write_text("\n".join(parts), encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "SMOKE2",
            "--corpus",
            "ietf",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "-y",
            "--overwrite",
            "-o",
            str(out / "chunks"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Analyzing chunk " in result.output
    assert "\r" not in result.output
    assert "Completed successfully in " in result.output
    report = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    assert report["run"]["request_batching"] in {"section_chunks", "per_section"}


def test_cli_per_section_scan_reports_runtime(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    sample.write_text(
        "1. One\nAlpha.\n\n2. Two\nBeta.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "SMOKE3",
            "--provider",
            "mock",
            "--force-sections",
            "--no-section-chunking",
            "-y",
            "--overwrite",
            "-o",
            str(out / "per"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Completed successfully in " in result.output
    assert "Analyzing chunk " in result.output


def test_cli_policy_a_failure_reports_elapsed(tmp_path: Path, monkeypatch):
    from tads.llm.providers.mock_provider import MockProvider
    from tads.pipeline import FindingParseError

    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"

    def boom(*_a, **_k):
        raise FindingParseError("Finding item 0: invalid field evidence.quote")

    monkeypatch.setattr(MockProvider, "complete", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "FAIL1",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "fail"),
        ],
    )
    assert result.exit_code == 1
    assert "Scan failed: Finding item 0: invalid field evidence.quote" in result.output
    assert "Failed after " in result.output
    assert "Traceback" not in result.output
    assert not (out / "fail.json").exists()


def test_cli_provider_failure_reports_elapsed(tmp_path: Path, monkeypatch):
    from tads.llm.providers.mock_provider import MockProvider

    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"

    def boom(*_a, **_k):
        raise RuntimeError("provider exploded with secret sk-test")

    monkeypatch.setattr(MockProvider, "complete", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "FAIL2",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "fail2"),
        ],
    )
    assert result.exit_code == 1
    assert "Scan failed: unexpected internal error" in result.output
    assert "Failed after " in result.output
    assert "sk-test" not in result.output
    assert "Traceback" not in result.output


def test_cli_generic_exception_failure_surface_stays_narrow(
    tmp_path: Path, monkeypatch
):
    """Progress failure path: one Scan failed, one Failed after, no broadening.

    Uses a benign generic exception only. Does not assert message-body contents.
    """
    from tads.llm.providers.mock_provider import MockProvider

    class GenericProviderError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("provider call failed")
            self.detail_marker = "ATTR_DETAIL_MARKER_should_not_print"
            self.nested = {"note": "NESTED_DETAIL_MARKER_should_not_print"}

        def __repr__(self) -> str:
            return (
                "GenericProviderError(detail_marker="
                f"{self.detail_marker!r}, nested={self.nested!r})"
            )

    def boom(*_a, **_k):
        try:
            raise ValueError("CAUSE_MARKER_should_not_print")
        except ValueError as inner:
            raise GenericProviderError() from inner

    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"
    fake = _FakeClock()

    def factory(stream=None, **kwargs):
        return ScanProgressReporter(
            stream=stream, clocks=_clocks(fake), isatty=True
        )

    monkeypatch.setattr(MockProvider, "complete", boom)
    monkeypatch.setattr("tads.cli.ScanProgressReporter", factory)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "FAILNARROW",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "failnarrow"),
        ],
    )
    blob = result.output or ""
    assert result.exit_code == 1
    assert blob.count("Scan failed:") == 1
    assert blob.count("Failed after ") == 1
    assert "Traceback" not in blob
    assert "GenericProviderError(detail_marker=" not in blob
    assert "CAUSE_MARKER_should_not_print" not in blob
    assert "ValueError" not in blob
    assert "ATTR_DETAIL_MARKER_should_not_print" not in blob
    assert "NESTED_DETAIL_MARKER_should_not_print" not in blob
    assert "detail_marker" not in blob
    # Live analyzing line must be cleared before permanent failure lines.
    assert blob.endswith("\n")
    assert "\r" in blob
    assert "Analyzing whole document" in blob
    failed_idx = blob.index("Scan failed:")
    prefix = blob[:failed_idx]
    assert prefix.rstrip(" ").endswith("\r") or "\r" + (" " * 8) in prefix


def test_cli_tty_long_to_short_live_line_via_typer_path(
    tmp_path: Path, monkeypatch
):
    """Long→short live replacement through scan_cmd's reporter + CliRunner stdout."""
    sample = tmp_path / "doc.txt"
    parts = [f"{i}. Section {i}\nText about clocks and epochs.\n" for i in range(1, 21)]
    sample.write_text("\n".join(parts), encoding="utf-8")
    out = tmp_path / "out"
    fake = _FakeClock()

    def factory(stream=None, **kwargs):
        # Use CliRunner-captured stdout (the real Typer path), force TTY semantics.
        rep = ScanProgressReporter(
            stream=stream, clocks=_clocks(fake), isatty=True
        )
        original = rep.__call__

        def wrapped(message: str) -> None:
            fake.advance(seconds=1.0)
            return original(message)

        rep.__call__ = wrapped  # type: ignore[method-assign]
        return rep

    monkeypatch.setattr("tads.cli.ScanProgressReporter", factory)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "TTYCLR",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            "2",
            "-y",
            "--overwrite",
            "-o",
            str(out / "tty"),
        ],
    )
    assert result.exit_code == 0, result.output
    text = result.output or ""
    assert "\r" in text
    assert "\033" not in text
    assert text.endswith("\n")
    assert "Completed successfully in " in text
    # Live chunk updates must not become permanent newline rows.
    permanent_chunks = [
        ln for ln in text.split("\n") if ln.startswith("[") and "Analyzing chunk" in ln
    ]
    assert permanent_chunks == []
    # After the second live write, the CR segment must be padded to the prior width.
    segments = [seg for seg in text.split("\r") if "Analyzing chunk" in seg]
    assert len(segments) >= 2
    first = segments[0].rstrip("\n")
    second = segments[1].rstrip("\n")
    assert len(second) >= len(first.rstrip())


def test_cli_truncation_reports_elapsed_without_raw_leak(tmp_path: Path, monkeypatch):
    from tads.llm.base import ModelOutputTruncatedError
    from tads.llm.providers.mock_provider import MockProvider

    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"
    secret = "SECRET_MODEL_BODY_should_not_appear"

    def boom(*_a, **_k):
        raise ModelOutputTruncatedError(
            "Model output truncated: completion token limit reached",
            raw=secret,
        )

    monkeypatch.setattr(MockProvider, "complete", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "FAIL3",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "trunc"),
        ],
    )
    assert result.exit_code == 1
    assert "Failed after " in result.output
    assert secret not in result.output
    assert "Traceback" not in result.output


def test_cli_keyboard_interrupt_exit_130(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    _write_sample(sample)
    out = tmp_path / "out"

    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr("tads.cli.run_scan", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=True
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "INTR",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "intr"),
        ],
    )
    assert result.exit_code == 130
    assert "Interrupted after " in result.output
    assert "Traceback" not in result.output
    assert "KeyboardInterrupt" not in result.output


def test_pipeline_progress_strings_remain_consumable_by_list_callback():
    from tads.pipeline import run_scan

    seen: list[str] = []
    report = run_scan(
        "1. Intro\nUnix 2038.\n2. Body\nMore text.\n",
        doc_id="RFC9999",
        corpus="ietf",
        provider="mock",
        force_mode=None,
        on_progress=seen.append,
    )
    assert report.schema_version == "0.5.0"
    joined = "\n".join(seen)
    assert "analyzing" in joined
    assert any(m.startswith("done:") for m in seen)
