# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic document-local section ID uniquification."""

from __future__ import annotations

from tads.parsing.document import Section

# Suffixes use ASCII double-hyphen so they cannot collide with dotted clause
# numbers embedded in generated ids (e.g. ``s-1.2``).
_SUFFIX_SEP = "--"


def uniquify_section_ids(sections: list[Section]) -> list[Section]:
    """
    Return sections whose ``id`` values uniquely identify each instance.

    Algorithm (document order):

    1. Reserve every original nonempty section ID (the set of IDs as parsed,
       before any suffix assignment). Empty IDs are replaced with the base
       ``section`` before reservation.
    2. Walk sections in order. The first time an ID is assigned, keep it
       unchanged (after empty→``section`` normalization).
    3. On a later collision, assign ``{original}--2``, ``{original}--3``, …
       skipping any candidate that is already assigned **or** present in the
       reserved original set (so a preexisting ``s-1--2`` is never stolen).

    Example originals ``[s-1, s-1, s-1--2, s-1]`` become
    ``[s-1, s-1--3, s-1--2, s-1--4]``.

    Identity is never inferred from title or content; order is the only
    tie-breaker. All instances are retained.
    """
    if not sections:
        return []

    originals = [(_normalize_base_id(s.id), s) for s in sections]
    reserved = {oid for oid, _ in originals}
    used: set[str] = set()
    out: list[Section] = []

    for original_id, section in originals:
        if original_id not in used:
            assigned = original_id
        else:
            assigned = _next_suffix(original_id, used=used, reserved=reserved)
        used.add(assigned)
        if assigned == section.id:
            out.append(section)
        else:
            out.append(
                Section(
                    id=assigned,
                    title=section.title,
                    text=section.text,
                    level=section.level,
                    start_char=section.start_char,
                    end_char=section.end_char,
                    parent_id=section.parent_id,
                )
            )
    return out


def _normalize_base_id(section_id: str) -> str:
    text = (section_id or "").strip()
    return text if text else "section"


def _next_suffix(base: str, *, used: set[str], reserved: set[str]) -> str:
    n = 2
    while True:
        candidate = f"{base}{_SUFFIX_SEP}{n}"
        if candidate not in used and candidate not in reserved:
            return candidate
        n += 1
