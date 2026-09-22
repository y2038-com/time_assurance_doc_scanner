# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Generic corpus adapter tests."""

import pytest

from tads.corpus import get_adapter
from tads.corpus.generic import GenericAdapter, extract_generic_title
from tads.fetch import FetchNotSupportedError, fetch_text


def test_generic_preserves_id_and_has_no_fetch_uri():
    adapter = GenericAdapter()
    assert adapter.normalize_id("  TESTDOC  ") == "TESTDOC"
    ref = adapter.resolve("TESTDOC")
    assert ref.corpus == "generic"
    assert ref.doc_id == "TESTDOC"
    assert ref.source_uri is None
    assert adapter.supports_remote_fetch is False
    assert get_adapter("generic") is not None


def test_generic_title_from_field_or_markdown_h1_only():
    assert extract_generic_title("Title: Local sample\n\nBody.\n") == "Local sample"
    assert extract_generic_title("# Heading title\n\nBody.\n") == "Heading title"
    assert extract_generic_title("## Not a title\n\nBody.\n") is None
    assert (
        extract_generic_title(
            "Internet Engineering Task Force (IETF)\n\n"
            "Network Time Protocol Sample Spec\n\n"
            "1. Introduction\n\nBody.\n"
        )
        is None
    )


def test_generic_parse_uses_conservative_title():
    adapter = GenericAdapter()
    ref = adapter.resolve("TESTDOC")
    doc = adapter.parse("Title: Local sample\n\n1 Intro\n\nBody text.\n", ref)
    assert doc.corpus == "generic"
    assert doc.doc_id == "TESTDOC"
    assert doc.title == "Local sample"
    assert doc.source_uri is None


def test_generic_fetch_not_supported():
    with pytest.raises(FetchNotSupportedError):
        fetch_text("TESTDOC", corpus="generic")
