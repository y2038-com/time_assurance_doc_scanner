# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Apply lightweight deterministic and source checks to findings."""

from __future__ import annotations

import re

from tads.schemas.findings import Finding, ValidationStatus
from tads.schemas.taxonomy import TimeDomain
from tads.validators import (
    Y2036_NTP_ERA,
    Y2038_SIGNED32,
    Y2106_UNSIGNED32,
    assert_rollover_date,
    parse_iso_date,
)

_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_LABEL_HINTS = (
    (TimeDomain.Y2036, "y2036"),
    (TimeDomain.Y2038, "y2038"),
    (TimeDomain.Y2106, "y2106"),
)
# Ignore tiny quotes that would match almost anything after normalization.
_MIN_QUOTE_CHARS = 16


def normalize_for_quote_match(text: str) -> str:
    """Case-fold and collapse whitespace for quote↔source comparison."""
    collapsed = re.sub(r"\s+", " ", text.casefold()).strip()
    return collapsed


def verify_finding_source(finding: Finding, document_text: str) -> Finding:
    """
    Mark ``source_verified`` when an evidence quote appears in document text.

    Uses a normalized substring match against the analyzed (scoped) document
    text. Does not promote human confirmation or deterministic validation.
    """
    quotes = [e.quote.strip() for e in finding.evidence if e.quote and e.quote.strip()]
    if not quotes:
        finding.source_verified = False
        finding.source_verification_detail = (
            "No evidence quotes to verify against source."
        )
        return finding

    haystack = normalize_for_quote_match(document_text)
    if not haystack:
        finding.source_verified = False
        finding.source_verification_detail = (
            "Analyzed document text is empty; cannot verify evidence quotes."
        )
        return finding

    matched = 0
    considered = 0
    for quote in quotes:
        needle = normalize_for_quote_match(quote)
        if len(needle) < _MIN_QUOTE_CHARS:
            continue
        considered += 1
        if needle in haystack:
            matched += 1

    if considered == 0:
        finding.source_verified = False
        finding.source_verification_detail = (
            f"Evidence quotes shorter than {_MIN_QUOTE_CHARS} characters after "
            "normalization; skipped source check."
        )
        return finding

    finding.source_verified = matched > 0
    finding.source_verification_detail = (
        f"Matched {matched} of {considered} evidence quote(s) in analyzed "
        "document text."
    )
    return finding


def enrich_finding_validation(finding: Finding) -> Finding:
    """
    Best-effort deterministic enrichment.

    If a finding asserts an ISO date near a known horizon domain, check it.
    A pass sets ``validation_status=verified`` (deterministically checked
    candidate), not a validated finding. Otherwise leave status as-is
    (typically unverified).
    """
    blob = " ".join(
        [
            finding.title,
            finding.description,
            finding.machine_interpretation,
            " ".join(e.quote for e in finding.evidence),
        ]
    )
    dates = [parse_iso_date(m) for m in _ISO_DATE.findall(blob)]
    dates = [d for d in dates if d is not None]
    if not dates:
        return finding

    for domain, label in _LABEL_HINTS:
        if domain not in finding.domains and label not in blob.lower():
            continue
        # Prefer a date close to the canonical horizon year
        expected = {
            "y2036": Y2036_NTP_ERA,
            "y2038": Y2038_SIGNED32,
            "y2106": Y2106_UNSIGNED32,
        }[label]
        claimed = min(dates, key=lambda d: abs((d - expected).days))
        if abs((claimed - expected).days) > 366:
            continue
        result = assert_rollover_date(label, claimed)
        finding.validation_status = (
            ValidationStatus.VERIFIED if result.ok else ValidationStatus.FAILED
        )
        finding.validation_detail = result.detail
        return finding

    # Generic: if the only date is exactly a known horizon, mark verified.
    known = {Y2036_NTP_ERA, Y2038_SIGNED32, Y2106_UNSIGNED32}
    exact = [d for d in dates if d in known]
    if len(exact) == 1:
        finding.validation_status = ValidationStatus.VERIFIED
        finding.validation_detail = f"Mentioned date {exact[0].isoformat()} matches a known horizon."
    return finding


def signed32_max_unix() -> int:
    """2**31 - 1"""
    return 2_147_483_647


def verify_unix_seconds_claim(seconds: int) -> tuple[bool, str]:
    """Check whether a claimed max signed 32-bit Unix second is correct."""
    expected = signed32_max_unix()
    if seconds == expected:
        return True, f"Signed 32-bit max seconds {seconds} is correct."
    return False, f"Claimed max seconds {seconds}; expected {expected}."
