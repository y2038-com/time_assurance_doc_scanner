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
    Severity,
)
from tads.schemas.horizon import TimeRepresentationParams
from tads.schemas.taxonomy import Confidence, TimeDomain

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK_BLOCK = re.compile(
    r"<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)

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
    """Extract a findings JSON object from messy model output."""
    candidates = _candidate_json_strings(text)
    errors: list[str] = []
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            return _as_findings_dict(data)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
        repaired = _repair_json(candidate)
        if repaired != candidate:
            try:
                data = json.loads(repaired)
                return _as_findings_dict(data)
            except json.JSONDecodeError as exc:
                errors.append(f"after repair: {exc}")
    detail = errors[-1] if errors else "no JSON object found"
    raise FindingParseError(
        f"Could not parse JSON object from model response ({detail})",
        raw=text,
    )


def _as_findings_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"findings": data}
    raise FindingParseError(f"Unexpected JSON root type: {type(data).__name__}")


def _candidate_json_strings(text: str) -> list[str]:
    stripped = _THINK_BLOCK.sub("", text).strip()
    candidates: list[str] = []

    for fence in _FENCE.finditer(stripped):
        candidates.append(fence.group(1).strip())

    candidates.append(stripped)

    # Prefer the object that looks like our schema if multiple `{...}` exist.
    for match in re.finditer(r"\{", stripped):
        snippet = _slice_balanced(stripped, match.start())
        if snippet:
            candidates.append(snippet)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    # Try denser / schema-like candidates first
    unique.sort(
        key=lambda s: (
            0 if '"findings"' in s else 1,
            0 if s.lstrip().startswith("{") else 1,
            -len(s),
        )
    )
    return unique


def _slice_balanced(text: str, start: int) -> Optional[str]:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    # Truncated JSON — return what we have for repair attempts
    return text[start:]


def _repair_json(text: str) -> str:
    """Best-effort repairs for common LLM JSON mistakes / truncation."""
    s = text.strip()
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    # Remove trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # If truncated mid-structure, close open braces/brackets outside strings.
    s = _close_open_containers(s)
    return s


def _close_open_containers(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    if in_string:
        text += '"'
    # Drop incomplete trailing key fragments after last comma/colon if obvious
    text = re.sub(r",[?\s]*$", "", text.rstrip())
    return text + "".join(reversed(stack))


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
        )
    except Exception:  # noqa: BLE001 - skip malformed findings rather than fail the scan
        return None


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

    width_bits = _coerce_int(value.get("width_bits"))
    signed = _coerce_bool(value.get("signed"))
    if signed is None and "signedness" in value:
        signed = _coerce_bool(value.get("signedness"))
    epoch_raw = _coerce_horizon_moment(value.get("epoch"))
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
