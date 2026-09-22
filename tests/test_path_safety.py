# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for automatic output filename sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest

from tads.path_safety import MAX_STEM_LENGTH, path_under, safe_filename

SAFE_IDS = ("RFC5905", "draft-example", "TESTDOC")
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
    "",
    "   ",
    "CON",
    "con",
    "CON.txt",
    "NUL.",
    "foo.",
)


@pytest.mark.parametrize("doc_id", SAFE_IDS)
def test_safe_identifiers_unchanged(doc_id: str):
    assert safe_filename(doc_id) == doc_id


def test_legacy_space_to_underscore_has_no_digest():
    assert safe_filename("foo bar") == "foo_bar"
    assert safe_filename("TS 23.501") == "TS_23.501"
    # Pre-existing compatibility collision: space conversion equals a literal underscore.
    assert safe_filename("foo bar") == safe_filename("foo_bar")


@pytest.mark.parametrize("doc_id", HOSTILE_IDS)
def test_hostile_ids_are_single_safe_component(doc_id: str):
    name = safe_filename(doc_id)
    assert name not in {"", ".", ".."}
    assert "/" not in name
    assert "\\" not in name
    assert "\n" not in name
    assert not name.startswith(".")
    assert not name.endswith(".")
    assert not Path(name).is_absolute()
    assert len(name) <= MAX_STEM_LENGTH


def test_traversal_and_absolute_forms_do_not_keep_path_syntax():
    for raw in (
        "../../escape",
        "../escape",
        "/tmp/escape",
        r"..\..\escape",
        r"C:\temp\escape",
        "C:/temp/escape",
        r"\\server\share\escape",
        "foo/bar",
        r"foo\bar",
    ):
        name = safe_filename(raw)
        assert ".." not in Path(name).parts
        assert name != raw
        assert "_" in name  # digest or replaced separators


def test_collision_distinct_unsafe_ids_differ():
    slash = safe_filename("foo/bar")
    backslash = safe_filename(r"foo\bar")
    assert slash != backslash
    assert slash.startswith("foo_bar_")
    assert backslash.startswith("foo_bar_")


def test_digest_only_when_beyond_space_conversion():
    assert "_" * 12 not in safe_filename("RFC5905")
    hostile = safe_filename("../../escape")
    assert len(hostile.rsplit("_", 1)[-1]) == 12


def test_empty_and_dot_components_get_fallback_and_digest():
    for raw in ("", "   ", ".", "..", "..."):
        name = safe_filename(raw)
        assert name.startswith("doc_")
        assert len(name) == len("doc_") + 12


def test_control_character_is_sanitized():
    name = safe_filename("id\nwith\nnewline")
    assert "\n" not in name
    assert name.startswith("id_with_newline_")


def test_long_identifier_is_truncated_with_digest():
    raw = "A" * 500
    name = safe_filename(raw)
    assert len(name) <= MAX_STEM_LENGTH
    assert name.endswith(safe_filename(raw)[-12:])
    assert name != raw


def test_windows_reserved_names_unsafe_on_every_platform():
    for raw in ("CON", "con", "CON.txt", "NUL.", "COM1", "LPT9"):
        name = safe_filename(raw)
        assert name != raw
        assert not name.upper().startswith("CON.")
        stem = name.split(".")[0]
        # Suffixed digest must not be the bare reserved device.
        assert stem.upper() not in {"CON", "PRN", "AUX", "NUL"}


def test_trailing_period_is_unsafe():
    name = safe_filename("foo.")
    assert name != "foo."
    assert not name.endswith(".")
    assert name.startswith("foo_")


def test_helper_is_deterministic():
    samples = list(SAFE_IDS) + list(HOSTILE_IDS) + ["foo bar", "A" * 200]
    for raw in samples:
        assert safe_filename(raw) == safe_filename(raw)


def test_path_under_keeps_relative_child_and_rejects_separators(tmp_path: Path):
    child = path_under(tmp_path / "outputs", "RFC5905")
    assert child == tmp_path / "outputs" / "RFC5905"
    assert child.resolve().is_relative_to((tmp_path / "outputs").resolve())

    missing_root = tmp_path / "does-not-exist"
    nested = path_under(missing_root, "TESTDOC.txt")
    assert nested == missing_root / "TESTDOC.txt"
    assert nested.resolve().is_relative_to(missing_root.resolve())

    with pytest.raises(ValueError):
        path_under(tmp_path, "../escape")
    with pytest.raises(ValueError):
        path_under(tmp_path, r"..\escape")
    with pytest.raises(ValueError):
        path_under(tmp_path, "/tmp/escape")
