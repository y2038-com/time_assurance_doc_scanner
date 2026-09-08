# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Evaluation harness utilities for the bootstrap corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from pydantic import BaseModel, Field

from tads.schemas.findings import FindingType
from tads.schemas.taxonomy import TimeDomain


class ExpectedFinding(BaseModel):
    """Gold label for a known issue in a seed document."""

    id: str
    finding_type: FindingType
    domains: list[TimeDomain] = Field(default_factory=list)
    section_id_contains: Optional[str] = None
    quote_contains: Optional[str] = None
    notes: Optional[str] = None


class SeedDocument(BaseModel):
    doc_id: str
    title: str
    rationale: str
    source_uri: str
    labels_file: Optional[str] = None


class SeedManifest(BaseModel):
    version: str = "0.1.0"
    documents: list[SeedDocument]


class EvalMatch(BaseModel):
    expected_id: str
    matched_finding_id: Optional[str] = None
    matched: bool = False
    reason: str = ""
    # Set when matched: whether predicted finding_type equals the gold label.
    type_agreement: Optional[bool] = None


class EvalSummary(BaseModel):
    doc_id: str
    expected_count: int
    predicted_count: int
    true_positives: int
    false_negatives: int
    matches: list[EvalMatch] = Field(default_factory=list)

    @property
    def recall(self) -> float:
        if self.expected_count == 0:
            return 1.0
        return self.true_positives / self.expected_count


def load_manifest(path: Path) -> SeedManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SeedManifest.model_validate(data)


def load_labels(path: Path) -> list[ExpectedFinding]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ExpectedFinding.model_validate(item) for item in data.get("findings", [])]


def match_findings(
    expected: Iterable[ExpectedFinding],
    predicted: Iterable[dict[str, Any]],
) -> EvalSummary:
    """
    Lightweight matcher for bootstrap eval.

    Matches primarily on section / quote evidence. ``finding_type`` agreement is
    recorded separately and is preferred when multiple evidence matches exist,
    but type mismatch alone does not prevent a match when evidence aligns.
    """
    predicted_list = list(predicted)
    expected_list = list(expected)
    used: set[int] = set()
    matches: list[EvalMatch] = []
    tp = 0

    for exp in expected_list:
        candidates: list[tuple[int, bool, str]] = []
        reason = "no candidate"
        has_evidence = bool(exp.section_id_contains or exp.quote_contains)

        for idx, pred in enumerate(predicted_list):
            if idx in used:
                continue
            section = (pred.get("location") or {}).get("section_id") or ""
            quotes = " ".join(e.get("quote", "") for e in pred.get("evidence") or [])
            pred_type = pred.get("finding_type")
            type_ok = pred_type == exp.finding_type.value

            if has_evidence:
                if (
                    exp.section_id_contains
                    and exp.section_id_contains not in section
                ):
                    reason = "evidence constraints; section failed"
                    continue
                if exp.quote_contains and exp.quote_contains not in quotes:
                    reason = "evidence constraints; quote failed"
                    continue
                candidates.append(
                    (
                        idx,
                        type_ok,
                        "matched"
                        if type_ok
                        else "matched; finding_type disagreement",
                    )
                )
            else:
                # Labels without section/quote constraints: type is the signal.
                if not type_ok:
                    reason = "type mismatch (no evidence constraints)"
                    continue
                candidates.append((idx, True, "matched"))

        if not candidates:
            matches.append(
                EvalMatch(expected_id=exp.id, matched=False, reason=reason)
            )
            continue

        # Prefer type agreement among evidence-equal candidates.
        candidates.sort(key=lambda item: (not item[1], item[0]))
        hit_idx, type_ok, hit_reason = candidates[0]
        used.add(hit_idx)
        tp += 1
        matches.append(
            EvalMatch(
                expected_id=exp.id,
                matched_finding_id=predicted_list[hit_idx].get("id"),
                matched=True,
                reason=hit_reason,
                type_agreement=type_ok,
            )
        )

    return EvalSummary(
        doc_id="",
        expected_count=len(expected_list),
        predicted_count=len(predicted_list),
        true_positives=tp,
        false_negatives=len(expected_list) - tp,
        matches=matches,
    )
