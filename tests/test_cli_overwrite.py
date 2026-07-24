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
