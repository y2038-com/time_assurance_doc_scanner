# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Raw-on-error persistence, overwrite, permissions, and parse-error tests."""

from __future__ import annotations

import inspect
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app, scan_cmd
from tads.export import write_report_json, write_report_markdown
from tads.llm.base import LLMResponse
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.registry import register_provider
from tads.pipeline import (
    RAW_ON_ERROR_MAX_BYTES,
    RAW_ON_ERROR_TRUNCATION_MARKER,
    RAW_SAVE_FAILED_NOTICE,
    _RAW_TMP_PREFIX,
    _save_raw_on_error,
    cap_raw_output,
    run_scan,
)
from tads.pipeline.parse_findings import FindingParseError, extract_json_object
from tads.schemas.report import AnalysisMode

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds.

2. Details

More text about epochs.
"""

CANARY = "CANARY_SECRET_raw_persist_xyz"


@pytest.fixture
def restore_mock_provider():
    yield
    register_provider(MockProvider())


class _InvalidJsonProvider(MockProvider):
    def complete(self, messages, *, model=None, max_output_tokens=4096):
        _ = (messages, model, max_output_tokens)
        return LLMResponse(content=f"not valid findings JSON {CANARY}")


def _posix_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_raw_installed(path: Path) -> None:
    assert path.is_file()
    assert not path.is_symlink()
    if os.name == "posix":
        assert _posix_mode(path) == 0o600


def test_raw_on_error_cap_and_mode_remain_unchanged():
    assert RAW_ON_ERROR_MAX_BYTES == 256 * 1024
    assert RAW_ON_ERROR_TRUNCATION_MARKER == "\n...[truncated]...\n"


def test_scan_help_documents_opt_in_raw_flag():
    param = inspect.signature(scan_cmd).parameters["save_raw_on_error"]
    option = param.default
    assert option.default is False
    decls = " ".join(getattr(option, "param_decls", ()) or ())
    assert "save-raw-on-error" in decls
    assert "no-save-raw-on-error" in decls
    help_text = option.help or ""
    assert "Disabled by default" in help_text
    assert "256 KiB" in help_text
    assert "troubleshooting" in help_text.lower()
    assert "sensitive as a whole" in help_text
    assert "field-level" in help_text


def test_raw_saving_disabled_by_default(tmp_path: Path, restore_mock_provider):
    register_provider(_InvalidJsonProvider())
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert not Path(str(out) + ".raw.txt").exists()
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()
    assert CANARY not in result.output


def test_explicit_save_raw_on_error_creates_file(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_InvalidJsonProvider())
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--save-raw-on-error",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code != 0
    raw = Path(str(out) + ".raw.txt")
    _assert_raw_installed(raw)
    assert CANARY in raw.read_text(encoding="utf-8")
    assert CANARY not in result.output
    assert not Path(str(out) + ".json").exists()
    assert not Path(str(out) + ".md").exists()


def test_raw_destination_participates_in_overwrite(
    tmp_path: Path, monkeypatch, restore_mock_provider
):
    register_provider(_InvalidJsonProvider())
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    raw = tmp_path / "out.raw.txt"
    raw.write_text("KEEP-RAW", encoding="utf-8")
    monkeypatch.setattr("tads.cli.Confirm.ask", lambda *_a, **_k: False)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--save-raw-on-error",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    assert raw.read_text(encoding="utf-8") == "KEEP-RAW"
    assert "out.raw.txt" in result.output


def test_new_raw_file_is_0600_on_posix(tmp_path: Path):
    dest = tmp_path / "diag.raw.txt"
    assert _save_raw_on_error(str(dest), "hello raw") is True
    _assert_raw_installed(dest)
    assert dest.read_text(encoding="utf-8") == "hello raw"
    leftovers = list(tmp_path.glob(f"{_RAW_TMP_PREFIX}*"))
    assert leftovers == []


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_replacing_world_readable_raw_ends_as_0600(tmp_path: Path, mode: int):
    dest = tmp_path / "diag.raw.txt"
    dest.write_text("old", encoding="utf-8")
    os.chmod(dest, mode)
    assert _save_raw_on_error(str(dest), "new-content") is True
    _assert_raw_installed(dest)
    assert dest.read_text(encoding="utf-8") == "new-content"


def test_existing_raw_symlink_is_refused(tmp_path: Path):
    target = tmp_path / "secret-target.txt"
    target.write_text("UNCHANGED", encoding="utf-8")
    dest = tmp_path / "diag.raw.txt"
    try:
        dest.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available on this platform")
    assert _save_raw_on_error(str(dest), "SHOULD-NOT-WRITE") is False
    assert dest.is_symlink()
    assert target.read_text(encoding="utf-8") == "UNCHANGED"
    leftovers = list(tmp_path.glob(f"{_RAW_TMP_PREFIX}*"))
    assert leftovers == []


def test_replace_failure_does_not_leave_partial_or_temp(
    tmp_path: Path, monkeypatch
):
    dest = tmp_path / "diag.raw.txt"

    def boom(_src, _dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("tads.pipeline.os.replace", boom)
    assert _save_raw_on_error(str(dest), "hello") is False
    assert not dest.exists()
    leftovers = list(tmp_path.glob(f"{_RAW_TMP_PREFIX}*"))
    assert leftovers == []


def test_write_failure_does_not_leave_partial_final(
    tmp_path: Path, monkeypatch
):
    dest = tmp_path / "diag.raw.txt"
    real_fdopen = os.fdopen

    def boom(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)

        def fail(*_a, **_k):
            raise OSError("simulated write failure")

        handle.write = fail  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr("tads.pipeline.os.fdopen", boom)
    assert _save_raw_on_error(str(dest), "hello") is False
    assert not dest.exists()
    leftovers = list(tmp_path.glob(f"{_RAW_TMP_PREFIX}*"))
    assert leftovers == []


def test_raw_save_failure_preserves_finding_parse_error(
    tmp_path: Path, monkeypatch, restore_mock_provider
):
    register_provider(_InvalidJsonProvider())
    dest = tmp_path / "fail.raw.txt"
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"
    json_path.write_text('{"keep":"json"}', encoding="utf-8")
    md_path.write_text("keep markdown", encoding="utf-8")
    monkeypatch.setattr("tads.pipeline._save_raw_on_error", lambda *_a, **_k: False)
    seen: list[str] = []
    with pytest.raises(FindingParseError) as exc_info:
        run_scan(
            SAMPLE,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.WHOLE_DOCUMENT,
            save_raw_on_error=str(dest),
            on_progress=seen.append,
        )
    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert CANARY not in str(err)
    assert str(dest) not in str(err)
    assert RAW_SAVE_FAILED_NOTICE in seen
    joined = "\n".join(seen)
    assert str(dest) not in joined
    assert CANARY not in joined
    assert json_path.read_text(encoding="utf-8") == '{"keep":"json"}'
    assert md_path.read_text(encoding="utf-8") == "keep markdown"


def test_raw_cap_marker_and_multibyte_utf8():
    prefix = "x" * (RAW_ON_ERROR_MAX_BYTES - 1)
    text = prefix + "\U0001f600 extra"
    capped = cap_raw_output(text)
    encoded = capped.encode("utf-8")
    assert capped.endswith(RAW_ON_ERROR_TRUNCATION_MARKER)
    assert "\U0001f600" not in capped
    assert len(encoded) <= RAW_ON_ERROR_MAX_BYTES
    marker = RAW_ON_ERROR_TRUNCATION_MARKER.encode("utf-8")
    assert encoded.endswith(marker) or len(encoded) + len(marker) > RAW_ON_ERROR_MAX_BYTES
    assert CANARY.encode("utf-8") not in encoded


def test_whole_document_and_section_aware_raw_on_error(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_InvalidJsonProvider())
    for mode, name in (
        (AnalysisMode.WHOLE_DOCUMENT, "whole"),
        (AnalysisMode.SECTION_AWARE, "section"),
    ):
        raw = tmp_path / f"{name}.raw.txt"
        with pytest.raises(FindingParseError):
            run_scan(
                SAMPLE,
                doc_id="RFC9999",
                provider="mock",
                force_mode=mode,
                save_raw_on_error=str(raw),
            )
        _assert_raw_installed(raw)
        assert "not valid findings JSON" in raw.read_text(encoding="utf-8")


def test_canonical_reports_keep_validated_evidence(
    tmp_path: Path, restore_mock_provider
):
    report = run_scan(
        SAMPLE,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.WHOLE_DOCUMENT,
    )
    assert report.findings
    quote = report.findings[0].evidence[0].quote
    assert quote
    json_path = tmp_path / "ok.json"
    md_path = tmp_path / "ok.md"
    write_report_json(report, json_path)
    write_report_markdown(report, md_path)
    json_text = json_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    assert quote in json_text
    assert quote in md_text
    assert report.findings[0].description in json_text
    assert "32-bit seconds" in quote


def test_json_decode_error_is_not_chained():
    payload = '{"findings": ' + CANARY
    with pytest.raises(FindingParseError) as exc_info:
        extract_json_object(payload)
    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert CANARY not in str(err)
    assert CANARY in err.raw
    assert "JSONDecodeError" not in str(err)
    assert ".doc" not in str(err)


class _BoomProvider(MockProvider):
    def complete(self, messages, *, model=None, max_output_tokens=4096):
        _ = (messages, model, max_output_tokens)
        raise RuntimeError(
            "openai HTTP 401 for https://api.openai.com/v1/chat/completions "
            "(non-retryable)"
        )


def test_existing_reports_unchanged_after_provider_failure(
    tmp_path: Path, restore_mock_provider
):
    register_provider(_BoomProvider())
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"
    json_path.write_text('{"keep": "json"}', encoding="utf-8")
    md_path.write_text("keep markdown", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    assert json_path.read_text(encoding="utf-8") == '{"keep": "json"}'
    assert md_path.read_text(encoding="utf-8") == "keep markdown"
    assert "HTTP 401" in result.output
    assert CANARY not in result.output

