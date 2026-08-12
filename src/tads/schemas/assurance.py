# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Public assurance status derived from finding fields."""

from __future__ import annotations

from enum import StrEnum

from tads.schemas.findings import Disposition, Finding, ValidationStatus


class AssuranceStatus(StrEnum):
    """
    Public-facing assurance vocabulary.

    Derived from ``disposition``, ``validation_status``, and ``source_verified``.
    """

    CANDIDATE = "candidate"
    SOURCE_VERIFIED = "source_verified"
    DETERMINISTICALLY_VALIDATED = "deterministically_validated"
    CROSS_MODEL_SUPPORTED = "cross_model_supported"
    HUMAN_CONFIRMED = "human_confirmed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


# Precedence when multiple signals apply (highest first).
_PRECEDENCE: tuple[AssuranceStatus, ...] = (
    AssuranceStatus.REJECTED,
    AssuranceStatus.HUMAN_CONFIRMED,
    AssuranceStatus.CROSS_MODEL_SUPPORTED,
    AssuranceStatus.DETERMINISTICALLY_VALIDATED,
    AssuranceStatus.SOURCE_VERIFIED,
    AssuranceStatus.CANDIDATE,
    AssuranceStatus.DEFERRED,
)


def derive_assurance_status(finding: Finding) -> AssuranceStatus:
    """
    Map stored finding fields to a public assurance status.

    Mapping (precedence):
    - ``disposition=rejected`` → rejected
    - ``disposition=accepted`` → human_confirmed
    - ``disposition=needs_review`` → deferred
    - ``validation_status=verified`` → deterministically_validated
    - ``source_verified=True`` → source_verified
    - otherwise → candidate

    ``cross_model_supported`` is reserved for a later ensemble phase.
    """
    if finding.disposition == Disposition.REJECTED:
        return AssuranceStatus.REJECTED
    if finding.disposition == Disposition.ACCEPTED:
        return AssuranceStatus.HUMAN_CONFIRMED
    if finding.disposition == Disposition.NEEDS_REVIEW:
        return AssuranceStatus.DEFERRED
    if finding.validation_status == ValidationStatus.VERIFIED:
        return AssuranceStatus.DETERMINISTICALLY_VALIDATED
    if finding.source_verified:
        return AssuranceStatus.SOURCE_VERIFIED
    return AssuranceStatus.CANDIDATE


def assurance_status_label(status: AssuranceStatus) -> str:
    """
    Human-readable label for reports.

    Reserve the phrase **validated finding** for ``human_confirmed`` only.
    Automated deterministic passes are **deterministically checked candidate**.
    """
    return {
        AssuranceStatus.CANDIDATE: "candidate for review",
        AssuranceStatus.SOURCE_VERIFIED: "source-verified candidate",
        AssuranceStatus.DETERMINISTICALLY_VALIDATED: (
            "deterministically checked candidate"
        ),
        AssuranceStatus.CROSS_MODEL_SUPPORTED: "cross-model supported candidate",
        AssuranceStatus.HUMAN_CONFIRMED: "validated finding (human-confirmed)",
        AssuranceStatus.REJECTED: "rejected",
        AssuranceStatus.DEFERRED: "deferred",
    }[status]


def assurance_status_sort_key(status: AssuranceStatus) -> int:
    """Lower sorts first in summary listings (rejected before candidate, etc.)."""
    try:
        return _PRECEDENCE.index(status)
    except ValueError:
        return len(_PRECEDENCE)
