# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Adversarial CLI tests for default scan/fetch output containment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import _output_paths, app
from tads.path_safety import path_under, safe_filename

SAMPLE = """\
Title: Local sample

1 Intro

Timers may wrap in 2038.
"""

HOSTILE_IDS = (
    "../../escape",
    "../escape",
    "/tmp/escape",
    r"..\..\escape",
    r"C:\temp\escape",
    "C:/temp/escape",
    r"\\server\share\escape",
    "foo/bar",
    r"foo\bar",
    ".",
    "..",
    "id\nwith\nnewline",
    "CON",
)


def _absolute_canaries() -> list[Path]:
    return [
        Path("/tmp/escape"),
        Path("/tmp/escape.json"),
        Path("/tmp/escape.md"),
        Path("/tmp/escape.raw.txt"),
        Path("/tmp/escape.txt"),
    ]


def _snapshot_abs() -> dict[Path, tuple[bool, int | None]]:
    snapped: dict[Path, tuple[bool, int | None]] = {}
    for path in _absolute_canaries():
        if path.exists():
            snapped[path] = (True, path.stat().st_mtime_ns)
        else:
            snapped[path] = (False, None)
    return snapped


def _assert_abs_unchanged(before: dict[Path, tuple[bool, int | None]]) -> None:
    for path, (existed, mtime) in before.items():
        if not existed:
            assert not path.exists(), f"created absolute canary {path}"
        else:
            assert path.exists()
            assert path.stat().st_mtime_ns == mtime


def _outside_markers(cwd: Path) -> list[Path]:
    return [
        cwd / "escape",
        cwd / "escape.json",
        cwd / "escape.md",
        cwd / "escape.txt",
        cwd.parent / "escape",
        cwd.parent / "escape.json",
        cwd.parent / "escape.md",
        cwd.parent / "escape.txt",
        cwd / "temp" / "escape",
        cwd / "temp" / "escape.txt",
    ]


def test_default_scan_stays_under_outputs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    abs_before = _snapshot_abs()
    markers = _outside_markers(tmp_path)
    existed = {path: path.exists() for path in markers}

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "../../escape",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output

    stem = safe_filename("../../escape")
    json_path = tmp_path / "outputs" / f"{stem}.json"
    md_path = tmp_path / "outputs" / f"{stem}.md"
    raw_path = tmp_path / "outputs" / f"{stem}.raw.txt"
    assert json_path.is_file()
    assert md_path.is_file()
    assert json_path.resolve().is_relative_to((tmp_path / "outputs").resolve())
    assert md_path.resolve().is_relative_to((tmp_path / "outputs").resolve())
    assert raw_path.resolve().is_relative_to((tmp_path / "outputs").resolve())
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["document"]["doc_id"] == "../../escape"
    assert stem not in (report["document"]["doc_id"],)

    _assert_abs_unchanged(abs_before)
    for path in markers:
        if not existed[path]:
            assert not path.exists(), f"wrote outside canary {path}"


def test_default_scan_overwrite_still_contained(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    stem = safe_filename("../escape")
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    (out_dir / f"{stem}.json").write_text("old", encoding="utf-8")
    (out_dir / f"{stem}.md").write_text("old", encoding="utf-8")
    outside = tmp_path / "escape.json"
    outside.write_text("canary", encoding="utf-8")
    abs_before = _snapshot_abs()

    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "../escape",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / f"{stem}.json").read_text(encoding="utf-8") != "old"
    assert outside.read_text(encoding="utf-8") == "canary"
    _assert_abs_unchanged(abs_before)


@pytest.mark.parametrize("doc_id", HOSTILE_IDS)
def test_hostile_scan_ids_never_escape_outputs(
    tmp_path: Path, monkeypatch, doc_id: str
):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    abs_before = _snapshot_abs()
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            doc_id,
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output
    stem = safe_filename(doc_id)
    json_path = tmp_path / "outputs" / f"{stem}.json"
    md_path = tmp_path / "outputs" / f"{stem}.md"
    assert json_path.is_file()
    assert md_path.is_file()
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["document"]["doc_id"] == doc_id
    _assert_abs_unchanged(abs_before)
    assert not (tmp_path / "escape.json").exists()
    assert not (tmp_path.parent / "escape.json").exists()


def test_safe_scan_ids_keep_legacy_filenames(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "RFC5905",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "outputs" / "RFC5905.json").is_file()
    report = json.loads((tmp_path / "outputs" / "RFC5905.json").read_text())
    assert report["document"]["doc_id"] == "RFC5905"


def test_explicit_scan_output_remains_unrestricted(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    dest = tmp_path / "elsewhere" / "custom"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "../../escape",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--output",
            str(dest),
        ],
    )
    assert result.exit_code == 0, result.output
    assert dest.with_suffix(".json").is_file()
    assert dest.with_suffix(".md").is_file()
    assert not (tmp_path / "outputs").exists()
    report = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["document"]["doc_id"] == "../../escape"


def test_raw_error_paths_inherit_safe_prefix(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prefix = path_under(Path("outputs"), safe_filename(r"C:\temp\escape"))
    json_path, md_path = _output_paths(prefix)
    raw_path = json_path.with_suffix(".raw.txt")
    outputs = (tmp_path / "outputs").resolve()
    assert json_path.resolve().is_relative_to(outputs)
    assert md_path.resolve().is_relative_to(outputs)
    assert raw_path.resolve().is_relative_to(outputs)
    assert json_path.name.endswith(".json")
    assert md_path.name.endswith(".md")
    assert raw_path.name.endswith(".raw.txt")
    assert json_path.stem == md_path.stem == raw_path.name[: -len(".raw.txt")]


def test_default_fetch_stays_under_inputs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _fake_fetch(doc_id: str, path: Path, **_kwargs) -> str:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"fetched:{doc_id}\n", encoding="utf-8")
        return str(dest)

    monkeypatch.setattr("tads.cli.fetch_to_path", _fake_fetch)
    abs_before = _snapshot_abs()
    result = CliRunner().invoke(
        app,
        ["fetch", "../../escape", "--overwrite"],
    )
    assert result.exit_code == 0, result.output
    # IETF normalize turns ids containing "." into draft-...
    from tads.corpus.ietf import IETFAdapter

    normalized = IETFAdapter().normalize_id("../../escape")
    stem = safe_filename(normalized)
    dest = tmp_path / "inputs" / f"{stem}.txt"
    assert dest.is_file()
    assert dest.resolve().is_relative_to((tmp_path / "inputs").resolve())
    _assert_abs_unchanged(abs_before)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.parametrize(
    "doc_id",
    (
        "../escape",
        "/tmp/escape",
        r"..\..\escape",
        r"C:\temp\escape",
        "C:/temp/escape",
        r"\\server\share\escape",
        "foo/bar",
        r"foo\bar",
        ".",
        "CON",
    ),
)
def test_hostile_fetch_ids_never_escape_inputs(
    tmp_path: Path, monkeypatch, doc_id: str
):
    monkeypatch.chdir(tmp_path)

    def _fake_fetch(_doc_id: str, path: Path, **_kwargs) -> str:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("fetched\n", encoding="utf-8")
        return str(dest)

    monkeypatch.setattr("tads.cli.fetch_to_path", _fake_fetch)
    abs_before = _snapshot_abs()
    result = CliRunner().invoke(app, ["fetch", doc_id, "--overwrite"])
    assert result.exit_code == 0, result.output
    written = list((tmp_path / "inputs").glob("*.txt"))
    assert written
    for path in written:
        assert path.resolve().is_relative_to((tmp_path / "inputs").resolve())
    _assert_abs_unchanged(abs_before)


def test_safe_fetch_id_keeps_legacy_filename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _fake_fetch(_doc_id: str, path: Path, **_kwargs) -> str:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("fetched\n", encoding="utf-8")
        return str(dest)

    monkeypatch.setattr("tads.cli.fetch_to_path", _fake_fetch)
    result = CliRunner().invoke(app, ["fetch", "RFC5905", "--overwrite"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "inputs" / "RFC5905.txt").is_file()


def test_explicit_fetch_output_remains_unrestricted(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "elsewhere" / "custom.txt"

    def _fake_fetch(_doc_id: str, path: Path, **_kwargs) -> str:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("fetched\n", encoding="utf-8")
        return str(out)

    monkeypatch.setattr("tads.cli.fetch_to_path", _fake_fetch)
    result = CliRunner().invoke(
        app,
        ["fetch", "../../escape", "--output", str(dest), "--overwrite"],
    )
    assert result.exit_code == 0, result.output
    assert dest.is_file()
    assert not (tmp_path / "inputs").exists()
