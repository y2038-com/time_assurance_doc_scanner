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

    A predicted finding matches an expected label when finding_type agrees and
    either section_id or quote substring constraints succeed (if provided).
    """
    predicted_list = list(predicted)
    expected_list = list(expected)
    used: set[int] = set()
    matches: list[EvalMatch] = []
    tp = 0

    for exp in expected_list:
        hit_idx: Optional[int] = None
        reason = "no candidate"
        for idx, pred in enumerate(predicted_list):
            if idx in used:
                continue
            if pred.get("finding_type") != exp.finding_type.value:
                continue
            section = (pred.get("location") or {}).get("section_id") or ""
            quotes = " ".join(e.get("quote", "") for e in pred.get("evidence") or [])
            if exp.section_id_contains and exp.section_id_contains not in section:
                reason = "type matched; section constraint failed"
                continue
            if exp.quote_contains and exp.quote_contains not in quotes:
                reason = "type matched; quote constraint failed"
                continue
            hit_idx = idx
            reason = "matched"
            break
        if hit_idx is not None:
            used.add(hit_idx)
            tp += 1
            matches.append(
                EvalMatch(
                    expected_id=exp.id,
                    matched_finding_id=predicted_list[hit_idx].get("id"),
                    matched=True,
                    reason=reason,
                )
            )
        else:
            matches.append(
                EvalMatch(expected_id=exp.id, matched=False, reason=reason)
            )

    return EvalSummary(
        doc_id="",
        expected_count=len(expected_list),
        predicted_count=len(predicted_list),
        true_positives=tp,
        false_negatives=len(expected_list) - tp,
        matches=matches,
    )
