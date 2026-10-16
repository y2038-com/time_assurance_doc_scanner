# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Whole-document prompts must not acquire chunk-only rules."""

from __future__ import annotations

from tads.parsing.document import ParsedDocument
from tads.prompts import (
    CHUNK_TASK,
    FINDING_JSON_INSTRUCTIONS,
    PROMPT_FRAMEWORK_VERSION,
    SECTION_TASK,
    SYSTEM_PROMPT,
    UNTRUSTED_DATA_POLICY,
    WHOLE_DOCUMENT_TASK,
    build_whole_document_prompt,
)


def test_prompt_framework_version_is_091():
    assert PROMPT_FRAMEWORK_VERSION == "0.9.1"


def test_whole_document_system_excludes_chunk_only_rules():
    doc = ParsedDocument(
        corpus="ietf",
        doc_id="RFC9999",
        title="Sample",
        text="Timestamps are 32-bit seconds.",
        sections=[],
    )
    bundle = build_whole_document_prompt(doc)
    # Chunk-only opaque-reference task must not appear.
    assert "kind=section_chunk" not in bundle.system
    assert "opaque" not in bundle.system.lower()
    assert "S0001" not in bundle.system
    assert "Trusted opaque section references" not in bundle.system
    assert "allowlist" not in bundle.system.lower()
    assert CHUNK_TASK.split("{allowlist}")[0] not in bundle.system
    # Shared finding contract + WD task remain.
    assert WHOLE_DOCUMENT_TASK.strip() in bundle.system
    assert FINDING_JSON_INSTRUCTIONS.strip() in bundle.system
    assert SYSTEM_PROMPT.strip() in bundle.system
    assert UNTRUSTED_DATA_POLICY.strip() in bundle.system
    # Single-section task is separate; WD must not require it either.
    assert SECTION_TASK.strip() not in bundle.system


def test_finding_json_instructions_do_not_require_opaque_refs():
    assert "opaque" not in FINDING_JSON_INSTRUCTIONS.lower()
    assert "S0001" not in FINDING_JSON_INSTRUCTIONS
    assert "allowlist" not in FINDING_JSON_INSTRUCTIONS.lower()
