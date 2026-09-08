# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Apply lightweight deterministic and source checks to findings."""

from __future__ import annotations

import re

from tads.schemas.findings import Finding, ValidationStatus
from tads.schemas.horizon import HorizonValidation, TimeRepresentationParams
from tads.schemas.taxonomy import TimeDomain
from tads.validators import (
    Y2036_NTP_ERA,
    Y2038_SIGNED32,
    Y2106_UNSIGNED32,
    TimeRepresentation,
    assert_rollover_date,
    parse_iso_date,
    validate_time_representation,
)
from tads.validators.epochs import parse_epoch_kind

# Years 1500–2999 keep UUID/NTFS/MJD-era and Y2106/Y2217 horizons reachable
# while still requiring a YYYY-MM-DD shape; parse_iso_date rejects invalid dates.
_ISO_DATE = re.compile(r"\b((?:1[5-9]\d{2}|2\d{3})-\d{2}-\d{2})\b")
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


_HORIZON_STATUS_TO_VALIDATION: dict[str, ValidationStatus] = {
    "verified": ValidationStatus.VERIFIED,
    "contradicted": ValidationStatus.FAILED,
    "insufficient_parameters": ValidationStatus.NOT_APPLICABLE,
    "ambiguous_signedness": ValidationStatus.NOT_APPLICABLE,
    "not_applicable": ValidationStatus.NOT_APPLICABLE,
    "unsupported": ValidationStatus.NOT_APPLICABLE,
    "error": ValidationStatus.FAILED,
}


def _params_to_representation(
    params: TimeRepresentationParams,
) -> TimeRepresentation:
    return TimeRepresentation(
        width_bits=params.width_bits,
        signed=params.signed,
        epoch_kind=params.epoch_kind,
        epoch=params.epoch,
        unit=params.unit,
        ticks_per_second=params.ticks_per_second,
        claimed_horizon=params.claimed_horizon,
        rollover_behavior=params.rollover_behavior,
    )


def _horizon_result_to_model(
    result,
    *,
    nest_interpretations: bool = True,
) -> HorizonValidation:
    signed_interp = None
    unsigned_interp = None
    if nest_interpretations:
        if result.signed_interpretation is not None:
            signed_interp = _horizon_result_to_model(
                result.signed_interpretation, nest_interpretations=False
            )
        if result.unsigned_interpretation is not None:
            unsigned_interp = _horizon_result_to_model(
                result.unsigned_interpretation, nest_interpretations=False
            )
    return HorizonValidation(
        validation_type=result.validation_type,
        status=result.status,
        minimum_value=result.minimum_value,
        maximum_value=result.maximum_value,
        earliest_representable=result.earliest_representable,
        last_representable=result.last_representable,
        first_out_of_range=result.first_out_of_range,
        claimed_horizon=result.claimed_horizon,
        claim_consistent=result.claim_consistent,
        notes=list(result.notes),
        signedness_resolved=result.signedness_resolved,
        signed_interpretation=signed_interp,
        unsigned_interpretation=unsigned_interp,
    )


def apply_horizon_validation(finding: Finding) -> Finding:
    """
    Run the fixed-width/epoch calculator when ``time_representation`` is set.

    Updates ``horizon_validation``, ``validation_status``, and ``validation_detail``.
    Does **not** change ``disposition`` or semantic candidate meaning.
    """
    params = finding.time_representation
    if params is None:
        return finding

    prior_disposition = finding.disposition
    calc = validate_time_representation(_params_to_representation(params))
    horizon = _horizon_result_to_model(calc)
    finding.horizon_validation = horizon
    finding.validation_status = _HORIZON_STATUS_TO_VALIDATION.get(
        calc.status, ValidationStatus.UNVERIFIED
    )
    detail_parts = [f"horizon:{calc.status}"]
    if calc.claim_consistent is not None:
        detail_parts.append(f"claim_consistent={calc.claim_consistent}")
    if calc.status == "ambiguous_signedness":
        signed = calc.signed_interpretation
        unsigned = calc.unsigned_interpretation
        if signed and signed.last_representable is not None:
            detail_parts.append(
                f"signed_last={signed.last_representable.isoformat()}"
            )
        if unsigned and unsigned.last_representable is not None:
            detail_parts.append(
                f"unsigned_last={unsigned.last_representable.isoformat()}"
            )
    elif calc.last_representable is not None:
        detail_parts.append(
            f"last_representable={calc.last_representable.isoformat()}"
        )
    if calc.notes:
        detail_parts.append(calc.notes[0])
    finding.validation_detail = "; ".join(detail_parts)
    finding.disposition = prior_disposition
    return finding


def enrich_finding_validation(finding: Finding) -> Finding:
    """
    Best-effort deterministic enrichment.

    Prefer structured ``time_representation`` / horizon calculator when present.
    Otherwise, if a finding asserts an ISO date near a known horizon domain,
    check it. A pass sets ``validation_status=verified`` (deterministically
    checked candidate), not a validated finding.
    """
    if finding.time_representation is not None:
        return apply_horizon_validation(finding)

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
