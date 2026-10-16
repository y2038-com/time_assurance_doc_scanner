# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic multi-section chunk packing tests (offline)."""

from __future__ import annotations

import pytest

from tads.cost import estimated_request_input_tokens
from tads.llm.openai_models import GPT_6_LUNA, LUNA_LONG_CONTEXT_INPUT_THRESHOLD
from tads.parsing.chunks import (
    DEFAULT_MAX_SECTIONS_PER_REQUEST,
    DuplicateSectionIdError,
    OversizedSectionError,
    estimate_chunk_input_tokens,
    opaque_section_ref,
    pack_section_chunks,
    planned_output_tokens,
)
from tads.parsing.document import Section
from tads.parsing.sections import REQUEST_OVERHEAD_TOKENS, estimate_tokens
from tads.pipeline import plan_scan
from tads.schemas.report import AnalysisMode, RequestBatching


def _section(num: int, *, chars: int = 40, section_id: str | None = None) -> Section:
    text = ("word " * (chars // 5)).strip() + "."
    sid = section_id or f"s-{num}"
    return Section(
        id=sid,
        title=f"{num}. Title {num}",
        text=text,
        level=1,
        start_char=0,
        end_char=len(text),
    )


def _pack(sections, **kwargs):
    defaults = dict(
        context_window=100_000,
        max_output_tokens=16_384,
        max_sections_per_request=DEFAULT_MAX_SECTIONS_PER_REQUEST,
        document_id="RFC9999",
        document_title="Doc",
        section_summary="summary",
        section_chunking=True,
    )
    defaults.update(kwargs)
    return pack_section_chunks(sections, **defaults)


def test_default_cap_is_eight():
    assert DEFAULT_MAX_SECTIONS_PER_REQUEST == 8


def test_several_adjacent_sections_pack_into_one_chunk():
    sections = [_section(i) for i in range(1, 5)]
    chunks = _pack(sections)
    assert len(chunks) == 1
    assert chunks[0].section_count == 4
    assert [b.opaque_ref for b in chunks[0].bindings] == [
        "S0001",
        "S0002",
        "S0003",
        "S0004",
    ]


def test_default_cap_of_eight_sections():
    sections = [_section(i) for i in range(1, 10)]
    chunks = _pack(sections)
    assert [c.section_count for c in chunks] == [8, 1]
    assert chunks[0].bindings[-1].opaque_ref == "S0008"
    assert chunks[1].bindings[0].opaque_ref == "S0009"


def test_user_provided_smaller_cap():
    sections = [_section(i) for i in range(1, 6)]
    chunks = _pack(sections, max_sections_per_request=2)
    assert [c.section_count for c in chunks] == [2, 2, 1]


def test_exact_context_equality_fits():
    max_out = 4_000
    sec = _section(1, chars=200)
    alone = _pack([sec], context_window=10**9, max_output_tokens=max_out)[0]
    # Choose a window where estimated_input + max_out equals the window exactly.
    window = alone.estimated_input_tokens + max_out
    chunks = _pack([sec], context_window=window, max_output_tokens=max_out)
    assert len(chunks) == 1
    assert chunks[0].estimated_input_tokens + max_out == window
    # One token less window → singleton no longer fits → plan failure.
    with pytest.raises(OversizedSectionError):
        _pack([sec], context_window=window - 1, max_output_tokens=max_out)


def test_one_token_beyond_boundary_splits():
    max_out = 4_000
    first = _section(1, chars=200)
    second = _section(2, chars=200)
    one = _pack([first], context_window=10**9, max_output_tokens=max_out)[0]
    both_bindings = list(one.bindings) + list(
        _pack([second], context_window=10**9, max_output_tokens=max_out)[0].bindings
    )
    # Re-estimate as a single candidate chunk with consecutive opaque refs.
    from tads.parsing.chunks import SectionBinding

    rebound = [
        SectionBinding(
            opaque_ref=opaque_section_ref(i + 1),
            section=b.section,
            canonical_id=b.canonical_id,
            canonical_title=b.canonical_title,
        )
        for i, b in enumerate(both_bindings)
    ]
    combined = estimate_chunk_input_tokens(
        rebound,
        document_id="RFC9999",
        document_title="Doc",
        section_summary="summary",
    )
    assert combined > one.estimated_input_tokens
    # Window fits first exactly; one more input token of headroom absent → split.
    tight_window = one.estimated_input_tokens + max_out
    chunks = _pack(
        [first, second],
        context_window=tight_window,
        max_output_tokens=max_out,
    )
    assert len(chunks) == 2
    assert all(c.section_count == 1 for c in chunks)


def test_large_section_isolated_in_own_chunk():
    huge = _section(1, chars=8_000)
    tiny_a = _section(2, chars=40)
    tiny_b = _section(3, chars=40)
    # Window fits huge alone and tinies together, but not huge+tiny.
    huge_chunk = _pack([huge], context_window=50_000, max_output_tokens=4_000)[0]
    window = huge_chunk.estimated_input_tokens + 4_000  # exact for huge alone
    chunks = _pack(
        [huge, tiny_a, tiny_b],
        context_window=window,
        max_output_tokens=4_000,
    )
    assert chunks[0].section_count == 1
    assert chunks[0].bindings[0].canonical_id == huge.id
    assert sum(c.section_count for c in chunks[1:]) == 2


def test_oversized_singleton_fails_at_planning():
    huge = _section(1, chars=50_000)
    with pytest.raises(OversizedSectionError, match="does not fit alone"):
        _pack([huge], context_window=10_000, max_output_tokens=8_000)


def test_oversized_singleton_fails_in_plan_scan_before_complete():
    huge = "1. Big\n" + ("token " * 40_000)
    with pytest.raises(OversizedSectionError):
        plan_scan(
            huge,
            doc_id="RFC9999",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            context_token_budget=20_000,
            max_output_tokens=16_384,
        )


def test_repeated_planning_identical_chunks():
    sections = [_section(i, chars=100) for i in range(1, 12)]
    a = _pack(sections, max_sections_per_request=3)
    b = _pack(sections, max_sections_per_request=3)
    assert [c.section_count for c in a] == [c.section_count for c in b]
    assert [
        tuple(x.opaque_ref for x in c.bindings) for c in a
    ] == [
        tuple(x.opaque_ref for x in c.bindings) for c in b
    ]


def test_no_section_chunking_one_request_per_section():
    sections = [_section(i) for i in range(1, 5)]
    chunks = _pack(sections, section_chunking=False)
    assert len(chunks) == 4
    assert all(c.section_count == 1 for c in chunks)
    assert [b.opaque_ref for c in chunks for b in c.bindings] == [
        "S0001",
        "S0002",
        "S0003",
        "S0004",
    ]


def test_force_sections_uses_chunking_by_default():
    text = "\n\n".join(
        f"{i}. Sec {i}\n" + ("body text " * 20) for i in range(1, 6)
    )
    plan = plan_scan(
        text,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert plan.analysis_mode == AnalysisMode.SECTION_AWARE
    assert plan.request_batching == RequestBatching.SECTION_CHUNKS
    assert plan.section_chunking is True
    assert len(plan.chunk_section_counts) == 1
    assert plan.chunk_section_counts[0] == 5


def test_opaque_references_deterministic():
    assert opaque_section_ref(1) == "S0001"
    assert opaque_section_ref(12) == "S0012"
    sections = [_section(i, section_id=f"hostile-id-{i}") for i in range(1, 4)]
    chunks = _pack(sections)
    refs = [b.opaque_ref for b in chunks[0].bindings]
    assert refs == ["S0001", "S0002", "S0003"]
    assert all(b.canonical_id.startswith("hostile-id-") for b in chunks[0].bindings)


def test_duplicate_canonical_ids_fail_at_planning():
    sections = [
        _section(1, section_id="dup"),
        _section(2, section_id="dup"),
    ]
    with pytest.raises(DuplicateSectionIdError, match="duplicate canonical"):
        _pack(sections)


def test_estimated_output_uses_min_max_output_and_1500_times_n():
    assert planned_output_tokens(3, 16_384) == 4_500
    assert planned_output_tokens(20, 10_000) == 10_000


def test_request_overhead_charged_once_per_chunk():
    sections = [_section(i) for i in range(1, 4)]
    chunks = _pack(sections)
    assert len(chunks) == 1
    contentish = chunks[0].estimated_input_tokens - REQUEST_OVERHEAD_TOKENS
    assert contentish > 0
    # Not N * overhead.
    assert chunks[0].estimated_input_tokens < (
        estimated_request_input_tokens(10) * 3
    )


def test_luna_short_long_pricing_per_chunk():
    from tads.cost import estimate_request_cost_usd

    # Two independent chunk input estimates around the threshold.
    short_in = LUNA_LONG_CONTEXT_INPUT_THRESHOLD
    long_in = LUNA_LONG_CONTEXT_INPUT_THRESHOLD + 1
    short_cost, short_notes = estimate_request_cost_usd(
        provider="openai",
        model=GPT_6_LUNA,
        input_tokens=short_in,
        output_tokens=1000,
    )
    long_cost, long_notes = estimate_request_cost_usd(
        provider="openai",
        model=GPT_6_LUNA,
        input_tokens=long_in,
        output_tokens=1000,
    )
    assert any("short-context" in n for n in short_notes)
    assert any("long-context" in n for n in long_notes)
    assert long_cost > short_cost


def test_plan_scan_no_provider_http(monkeypatch):
    calls: list[str] = []

    def boom(*_a, **_k):
        calls.append("http")
        raise AssertionError("plan must not call HTTP")

    monkeypatch.setattr("tads.llm.http.post_json", boom)
    text = "1. A\nTimestamps are 32-bit seconds.\n\n2. B\nMore text.\n"
    plan = plan_scan(
        text,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
    )
    assert plan.chunks
    assert calls == []


def test_overhead_constant_shared():
    # 6000 after prompt 0.9.0 chunk system + native schema (~4700 measured).
    assert REQUEST_OVERHEAD_TOKENS == 6_000
    assert estimate_tokens("abcd") == 1


def test_max_sections_per_request_zero_raises_chunking_error():
    from tads.parsing.chunks import SectionChunkingError

    with pytest.raises(SectionChunkingError, match="max_sections_per_request"):
        _pack([_section(1)], max_sections_per_request=0)
    with pytest.raises(SectionChunkingError, match="max_sections_per_request"):
        _pack([_section(1)], max_sections_per_request=-3)


def test_all_singleton_automatic_packing_records_per_section():
    text = "\n\n".join(
        f"{i}. Sec {i}\n" + ("body text " * 20) for i in range(1, 4)
    )
    plan = plan_scan(
        text,
        doc_id="RFC9999",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        max_sections_per_request=1,
        section_chunking=True,
    )
    assert plan.section_chunking is True
    assert plan.chunk_section_counts == (1, 1, 1)
    assert plan.request_batching == RequestBatching.PER_SECTION


def test_live_framing_estimate_tracks_section_metadata_length():
    from tads.parsing.chunks import SectionBinding, render_section_chunk_user_message
    from tads.parsing.document import ParsedDocument
    from tads.prompts import build_section_chunk_prompt

    short = Section(
        id="s-1",
        title="1. Short",
        text="Timestamps are 32-bit seconds.",
        level=1,
        start_char=0,
        end_char=30,
    )
    long_title = Section(
        id="s-1",
        title="1. " + ("VeryLongTitleWord " * 40),
        text="Timestamps are 32-bit seconds.",
        level=1,
        start_char=0,
        end_char=30,
    )

    def _bind(section: Section) -> list[SectionBinding]:
        return [
            SectionBinding(
                opaque_ref="S0001",
                section=section,
                canonical_id="s-1",
                canonical_title=section.title,
            )
        ]

    short_est = estimate_chunk_input_tokens(
        _bind(short),
        document_id="RFC9999",
        document_title="Doc",
        section_summary="summary",
    )
    long_est = estimate_chunk_input_tokens(
        _bind(long_title),
        document_id="RFC9999",
        document_title="Doc",
        section_summary="summary",
    )
    assert long_est > short_est
    # Planning serialization matches prompt construction serialization.
    doc = ParsedDocument(
        corpus="ietf", doc_id="RFC9999", title="Doc", text=short.text, sections=[]
    )
    bundle = build_section_chunk_prompt(
        doc,
        opaque_refs=["S0001"],
        sections=[short],
        document_summary="summary",
    )
    live = render_section_chunk_user_message(
        document_id="RFC9999",
        document_title="Doc",
        section_summary="summary",
        opaque_refs=["S0001"],
        sections=[short],
    )
    assert bundle.user == live
    assert short_est == estimate_tokens(live) + REQUEST_OVERHEAD_TOKENS


def test_empty_section_list_packs_to_empty():
    assert _pack([]) == []
