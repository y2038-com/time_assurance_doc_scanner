# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""CLI overwrite-guard helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer import Exit

from tads.cli import (
    _confirm_overwrite,
    _existing_output_files,
    _resolve_text_output_path,
    _source_stem,
)


def test_source_stem_and_resolve(tmp_path: Path):
    assert _source_stem("https://example.com/a/rfc5905.pdf") == "rfc5905"
    assert _source_stem(str(tmp_path / "spec.docx")) == "spec"

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    resolved = _resolve_text_output_path(str(out_dir) + "/", source="x/body.pdf")
    assert resolved == out_dir / "body.txt"

    file_path = tmp_path / "custom.txt"
    assert _resolve_text_output_path(str(file_path), source="ignored.pdf") == file_path


def test_existing_output_files(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("x", encoding="utf-8")
    assert _existing_output_files([a, b, tmp_path]) == [a]


def test_confirm_overwrite_force_skips_prompt(tmp_path: Path, monkeypatch):
    path = tmp_path / "out.txt"
    path.write_text("old", encoding="utf-8")

    def _boom(*_a, **_k):
        raise AssertionError("Confirm should not be called with overwrite=True")

    monkeypatch.setattr("tads.cli.Confirm.ask", _boom)
    _confirm_overwrite([path], overwrite=True)


def test_confirm_overwrite_abort(tmp_path: Path, monkeypatch):
    path = tmp_path / "out.txt"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr("tads.cli.Confirm.ask", lambda *_a, **_k: False)
    with pytest.raises(Exit) as exc:
        _confirm_overwrite([path], overwrite=False)
    assert exc.value.exit_code == 1


def test_confirm_overwrite_accept(tmp_path: Path, monkeypatch):
    path = tmp_path / "out.txt"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr("tads.cli.Confirm.ask", lambda *_a, **_k: True)
    _confirm_overwrite([path], overwrite=False)


_MIN_REPORT_JSON = """\
{
  "schema_version": "0.1.0",
  "document": {"corpus": "ietf", "doc_id": "RFC9999"},
  "run": {"scanner_version": "0.4.0", "prompt_framework_version": "0.5.0"},
  "findings": []
}
"""


def test_render_overwrite_flag(tmp_path: Path):
    from typer.testing import CliRunner

    from tads.cli import app

    report_json = tmp_path / "r.json"
    md_path = tmp_path / "r.md"
    report_json.write_text(_MIN_REPORT_JSON, encoding="utf-8")
    md_path.write_text("old markdown", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["render", str(report_json), "-f"])
    assert result.exit_code == 0, result.output
    assert "wrote" in result.output
    assert "Time Assurance Scan Report" in md_path.read_text(encoding="utf-8")
    assert "old markdown" not in md_path.read_text(encoding="utf-8")


def test_render_aborts_without_overwrite(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from tads.cli import app

    report_json = tmp_path / "r.json"
    md_path = tmp_path / "r.md"
    report_json.write_text(_MIN_REPORT_JSON, encoding="utf-8")
    md_path.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr("tads.cli.Confirm.ask", lambda *_a, **_k: False)

    runner = CliRunner()
    result = runner.invoke(app, ["render", str(report_json)])
    assert result.exit_code == 1
    assert md_path.read_text(encoding="utf-8") == "keep me"
