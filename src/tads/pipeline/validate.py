"""Apply lightweight deterministic checks to findings."""

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


def enrich_finding_validation(finding: Finding) -> Finding:
    """
    Best-effort deterministic enrichment.

    If a finding asserts an ISO date near a known horizon domain, verify it.
    Otherwise leave validation_status as-is (typically unverified).
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
