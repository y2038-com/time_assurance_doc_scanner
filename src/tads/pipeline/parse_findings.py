# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Parse LLM JSON payloads into Finding objects."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from tads.schemas.findings import (
    Evidence,
    Finding,
    FindingLocation,
    FindingType,
    ScopeRelevance,
    Severity,
)
from tads.schemas.horizon import TimeRepresentationParams
from tads.schemas.taxonomy import Confidence, TimeDomain

_THINK_BLOCK = re.compile(
    r"<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)
_OPENING_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n", re.IGNORECASE)

_DOMAIN_ALIASES = {
    "y2036": TimeDomain.Y2036,
    "ntp era": TimeDomain.Y2036,
    "y2038": TimeDomain.Y2038,
    "year 2038": TimeDomain.Y2038,
    "y2100": TimeDomain.Y2100,
    "y2106": TimeDomain.Y2106,
    "epoch": TimeDomain.EPOCH,
    "representation": TimeDomain.REPRESENTATION,
    "signedness": TimeDomain.SIGNEDNESS,
    "date_range": TimeDomain.DATE_RANGE,
    "date range": TimeDomain.DATE_RANGE,
    "calendar": TimeDomain.CALENDAR,
    "leap_year": TimeDomain.LEAP_YEAR,
    "leap year": TimeDomain.LEAP_YEAR,
    "leap_second": TimeDomain.LEAP_SECOND,
    "leap second": TimeDomain.LEAP_SECOND,
    "utc": TimeDomain.UTC,
    "tai": TimeDomain.TAI,
    "gps": TimeDomain.GPS_TIME,
    "gps_time": TimeDomain.GPS_TIME,
    "monotonic": TimeDomain.MONOTONIC,
    "serialization": TimeDomain.SERIALIZATION,
    "persistence": TimeDomain.PERSISTENCE,
    "synchronization": TimeDomain.SYNCHRONIZATION,
    "archival": TimeDomain.ARCHIVAL,
    "certificate": TimeDomain.CERTIFICATE_VALIDITY,
    "certificate_validity": TimeDomain.CERTIFICATE_VALIDITY,
    "scheduling": TimeDomain.SCHEDULING,
    "migration": TimeDomain.MIGRATION,
    "rollover": TimeDomain.ROLLOVER,
    "missing_documentation": TimeDomain.MISSING_DOCUMENTATION,
}


class FindingParseError(ValueError):
    """Raised when model output cannot be parsed into findings JSON."""

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Accept one complete findings JSON object after think-block and single-fence cleanup.

    Mixed prose, multiple fences, list-root JSON, or extra trailing data are rejected
    so a later protected LLM repair can run. This function does not search for the
    longest embedded object.
    """
    cleaned = _THINK_BLOCK.sub("", text).strip()
    cleaned = _unwrap_single_fence(cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _repair_json(cleaned)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise FindingParseError(
                f"Could not parse JSON object from model response ({exc})",
                raw=text,
            ) from exc
    if not isinstance(data, dict):
        raise FindingParseError(
            f"Expected a JSON object with a findings array; got {type(data).__name__}",
            raw=text,
        )
    if not isinstance(data.get("findings"), list):
        raise FindingParseError(
            "Expected a JSON object with a findings array",
            raw=text,
        )
    return data


def _unwrap_single_fence(text: str) -> str:
    """Unwrap when the entire remainder is exactly one Markdown fence."""
    if text.count("```") != 2:
        return text
    match = _OPENING_FENCE.match(text)
    if match is None:
        return text
    rest = text[match.end() :]
    closing = rest.rstrip()
    if not closing.endswith("```"):
        return text
    inner = closing[: closing.rfind("```")].strip()
    return inner


def _repair_json(text: str) -> str:
    """Normalize quotes and trailing commas on the entire leftover string."""
    s = text.strip()
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s


def parse_findings_payload(
    payload: dict[str, Any],
    *,
    id_start: int = 1,
    default_section_id: Optional[str] = None,
    default_section_title: Optional[str] = None,
) -> list[Finding]:
    raw_findings = payload.get("findings", payload if isinstance(payload, list) else [])
    if not isinstance(raw_findings, list):
        raise ValueError("Model payload missing findings array")
    findings: list[Finding] = []
    next_id = id_start
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        finding = _coerce_finding(
            item,
            finding_id=f"F-{next_id:03d}",
            default_section_id=default_section_id,
            default_section_title=default_section_title,
        )
        if finding is not None:
            findings.append(finding)
            next_id += 1
    return findings


def _coerce_optional_str(value: Any) -> Optional[str]:
    """Normalize model quirks (lists, numbers) into an optional string."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_coerce_optional_str(v) for v in value]
        joined = "; ".join(p for p in parts if p)
        return joined or None
    text = str(value).strip()
    return text or None


def _coerce_finding(
    item: dict[str, Any],
    *,
    finding_id: str,
    default_section_id: Optional[str],
    default_section_title: Optional[str],
) -> Optional[Finding]:
    try:
        finding_type = FindingType(_normalize_enum(item.get("finding_type"), FindingType))
        severity = Severity(_normalize_enum(item.get("severity"), Severity) or "medium")
        confidence = Confidence(
            _normalize_enum(item.get("confidence"), Confidence) or "medium"
        )
    except ValueError:
        return None

    title = _coerce_optional_str(item.get("title")) or "Untitled finding"
    description = _coerce_optional_str(item.get("description")) or title
    section_id = _coerce_optional_str(item.get("section_id")) or default_section_id
    section_title = (
        _coerce_optional_str(item.get("section_title")) or default_section_title
    )
    location = FindingLocation(section_id=section_id, section_title=section_title)
    evidence = []
    for ev in item.get("evidence") or []:
        if isinstance(ev, str) and ev.strip():
            evidence.append(Evidence(quote=ev.strip()))
        elif isinstance(ev, dict) and ev.get("quote"):
            quote = _coerce_optional_str(ev.get("quote"))
            if not quote:
                continue
            evidence.append(
                Evidence(
                    quote=quote,
                    note=_coerce_optional_str(ev.get("note")),
                )
            )
    domains = _coerce_domains(item.get("domains") or [])
    rec = _coerce_optional_str(item.get("recommendation_level1"))
    time_rep = _coerce_time_representation(item.get("time_representation"))
    scope_relevance = _coerce_scope_relevance(item.get("scope_relevance"))
    scope_rationale = _coerce_optional_str(item.get("scope_rationale"))
    try:
        return Finding(
            id=finding_id,
            finding_type=finding_type,
            title=title,
            description=description,
            severity=severity,
            confidence=confidence,
            domains=domains,
            location=location,
            evidence=evidence,
            machine_interpretation=(
                _coerce_optional_str(item.get("machine_interpretation")) or description
            ),
            recommendation_level1=rec,
            time_representation=time_rep,
            scope_relevance=scope_relevance,
            scope_rationale=scope_rationale,
        )
    except Exception:  # noqa: BLE001 - skip malformed findings rather than fail the scan
        return None


def _coerce_scope_relevance(value: Any) -> ScopeRelevance:
    """Map model scope labels; invalid/missing → core (safe back-compat default)."""
    if value is None or value == "":
        return ScopeRelevance.CORE
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "core": ScopeRelevance.CORE,
        "primary": ScopeRelevance.CORE,
        "supporting": ScopeRelevance.SUPPORTING,
        "support": ScopeRelevance.SUPPORTING,
        "incidental": ScopeRelevance.INCIDENTAL,
        "minor": ScopeRelevance.INCIDENTAL,
        "out_of_scope": ScopeRelevance.OUT_OF_SCOPE,
        "oos": ScopeRelevance.OUT_OF_SCOPE,
        "irrelevant": ScopeRelevance.OUT_OF_SCOPE,
        "unrelated": ScopeRelevance.OUT_OF_SCOPE,
    }
    return aliases.get(text, ScopeRelevance.CORE)


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "signed", "twos_complement", "two's_complement"}:
        return True
    if text in {"false", "no", "unsigned"}:
        return False
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip().lower().replace("bits", "").replace("-bit", "").strip()
    try:
        return int(text, 10)
    except ValueError:
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _coerce_horizon_moment(value: Any):
    """Parse claimed_horizon / epoch as datetime or date; return None on failure."""
    from datetime import date, datetime, timezone

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Date-only strings must stay as ``date`` so claim matching can use
    # calendar-day equality (models often cite 2036-02-07, not 06:28:15Z).
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        tail = text[10:]
        if tail == "" or tail.upper() in {"Z"}:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                pass
    # Normalize Zulu for full datetimes
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _coerce_time_representation(value: Any) -> Optional[TimeRepresentationParams]:
    if value is None or value is False:
        return None
    if not isinstance(value, dict):
        return None
    from datetime import date, datetime, timezone

    from tads.schemas.horizon import EpochKind
    from tads.validators.epochs import parse_epoch_kind

    width_bits = _coerce_int(value.get("width_bits"))
    signed = _coerce_bool(value.get("signed"))
    if signed is None and "signedness" in value:
        signed = _coerce_bool(value.get("signedness"))

    epoch_kind = parse_epoch_kind(value.get("epoch_kind"))
    epoch_raw = _coerce_horizon_moment(value.get("epoch"))
    # Allow epoch string aliases like "unix" / "mjd" when not an ISO datetime.
    if epoch_raw is None and isinstance(value.get("epoch"), str):
        maybe_kind = parse_epoch_kind(value.get("epoch"))
        if maybe_kind is not None and maybe_kind != EpochKind.OTHER:
            epoch_kind = epoch_kind or maybe_kind

    epoch: Optional[datetime]
    if isinstance(epoch_raw, datetime):
        epoch = epoch_raw
    elif isinstance(epoch_raw, date):
        epoch = datetime(
            epoch_raw.year, epoch_raw.month, epoch_raw.day, tzinfo=timezone.utc
        )
    else:
        epoch = None
    unit = _coerce_optional_str(value.get("unit"))
    ticks = _coerce_float(value.get("ticks_per_second"))
    claimed = _coerce_horizon_moment(value.get("claimed_horizon"))
    rollover = _coerce_optional_str(value.get("rollover_behavior"))
    params = TimeRepresentationParams(
        width_bits=width_bits,
        signed=signed,
        epoch_kind=epoch_kind,
        epoch=epoch,
        unit=unit,
        ticks_per_second=ticks,
        claimed_horizon=claimed,
        rollover_behavior=rollover,
    )
    # Omit empty objects so heuristic ISO enrichment can still run.
    if all(
        getattr(params, name) is None
        for name in (
            "width_bits",
            "signed",
            "epoch_kind",
            "epoch",
            "unit",
            "ticks_per_second",
            "claimed_horizon",
            "rollover_behavior",
        )
    ):
        return None
    return params


def _normalize_enum(value: Any, enum_cls: type) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    allowed = {m.value for m in enum_cls}
    if text in allowed:
        return text
    return None


def _coerce_domains(values: list[Any]) -> list[TimeDomain]:
    out: list[TimeDomain] = []
    seen: set[TimeDomain] = set()
    for value in values:
        key = str(value).strip().lower().replace("-", "_")
        domain = _DOMAIN_ALIASES.get(key)
        if domain is None:
            try:
                domain = TimeDomain(key)
            except ValueError:
                domain = TimeDomain.OTHER
        if domain not in seen:
            seen.add(domain)
            out.append(domain)
    return out
