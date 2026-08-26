# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Prompt and validator smoke tests."""

from tads.corpus.ietf import IETFAdapter
from tads.prompts import PROMPT_FRAMEWORK_VERSION, build_section_prompt, build_whole_document_prompt
from tads.validators import Y2038_SIGNED32, assert_rollover_date


SAMPLE = """\
1. Introduction

Time uses signed 32-bit seconds.

2. Details

More text here about epochs.
"""


def test_prompts_include_evidence_instructions():
    adapter = IETFAdapter()
    ref = adapter.resolve("RFC9999")
    doc = adapter.parse(SAMPLE, ref)
    whole = build_whole_document_prompt(doc)
    assert "findings" in whole.user
    assert "time_representation" in whole.user
    assert "scope_relevance" in whole.user
    assert "out_of_scope" in whole.user
    assert "not a general" in whole.system.lower() or "time-assurance scanner" in whole.system
    assert "Do not invent" in whole.user or "Do not guess" in whole.system
    assert "Absence claims" in whole.system or "not addressed" in whole.system
    assert "search this full document" in whole.user
    assert "not addressed" in whole.user
    assert PROMPT_FRAMEWORK_VERSION.startswith("0.5")
    section = doc.sections[0]
    bundle = build_section_prompt(doc, section)
    assert section.id in bundle.user
    assert "time_representation" in bundle.user
    assert "scope_relevance" in bundle.user
    assert "document summary" in bundle.user.lower() or "section-local" in bundle.user


def test_rollover_validator():
    ok = assert_rollover_date("y2038", Y2038_SIGNED32)
    assert ok.ok
    bad = assert_rollover_date("y2038", Y2038_SIGNED32.replace(year=2037))
    assert not bad.ok
