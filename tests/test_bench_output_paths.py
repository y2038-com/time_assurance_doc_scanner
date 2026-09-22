# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Benchmark-derived output paths stay under --outputs-dir."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tads.path_safety import safe_filename

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"


def _load_bench_module():
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_bench_scans",
        _SCRIPTS / "run_bench_scans.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench_module()


def test_bench_safe_names_keep_legacy_shape(tmp_path: Path):
    prefix = bench._output_prefix(
        tmp_path / "outputs",
        "RFC5905",
        "openai",
        "gpt-4.1-mini",
        "bench2026",
    )
    assert prefix == tmp_path / "outputs" / "RFC5905__openai__gpt-4.1-mini__bench2026"
    assert prefix.resolve().is_relative_to((tmp_path / "outputs").resolve())


@pytest.mark.parametrize(
    "tag",
    (
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
    ),
)
def test_hostile_tag_stays_under_outputs_dir(tmp_path: Path, tag: str):
    root = tmp_path / "outputs"
    prefix = bench._output_prefix(root, "RFC5905", "openai", "gpt-4.1-mini", tag)
    assert prefix.resolve().is_relative_to(root.resolve())
    assert prefix.parent == root
    assert ".." not in prefix.parts
    assert prefix.name.startswith("RFC5905__openai__gpt-4.1-mini__")
    assert prefix.name.endswith(safe_filename(tag))
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.parametrize(
    "doc_id",
    (
        "../../escape",
        "/tmp/escape",
        r"C:\temp\escape",
        r"\\server\share\escape",
        "foo/bar",
        "CON",
        "TS 23.501",
    ),
)
def test_hostile_or_spaced_doc_id_stays_under_outputs_dir(tmp_path: Path, doc_id: str):
    root = tmp_path / "outputs"
    prefix = bench._output_prefix(root, doc_id, "openai", "gpt-4.1-mini", None)
    assert prefix.resolve().is_relative_to(root.resolve())
    assert prefix.parent == root
    assert prefix.name.startswith(f"{safe_filename(doc_id)}__")
    if doc_id == "TS 23.501":
        assert prefix.name.startswith("TS_23.501__")


def test_distinct_hostile_tags_do_not_collide(tmp_path: Path):
    root = tmp_path / "outputs"
    a = bench._output_prefix(root, "RFC5905", "openai", "gpt-4.1-mini", "foo/bar")
    b = bench._output_prefix(root, "RFC5905", "openai", "gpt-4.1-mini", r"foo\bar")
    assert a != b
    assert a.parent == b.parent == root
