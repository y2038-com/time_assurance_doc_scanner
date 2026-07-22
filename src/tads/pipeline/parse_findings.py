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

    title = str(item.get("title") or "").strip() or "Untitled finding"
    description = str(item.get("description") or title).strip()
    section_id = item.get("section_id") or default_section_id
    section_title = item.get("section_title") or default_section_title
    location = FindingLocation(section_id=section_id, section_title=section_title)
    evidence = []
    for ev in item.get("evidence") or []:
        if isinstance(ev, str) and ev.strip():
            evidence.append(Evidence(quote=ev.strip()))
        elif isinstance(ev, dict) and ev.get("quote"):
            evidence.append(
                Evidence(
                    quote=str(ev["quote"]),
                    note=(str(ev["note"]) if ev.get("note") is not None else None),
                )
            )
    domains = _coerce_domains(item.get("domains") or [])
    rec = item.get("recommendation_level1")
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
        machine_interpretation=str(
            item.get("machine_interpretation") or description
        ).strip(),
        recommendation_level1=str(rec).strip() if rec else None,
    )


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
