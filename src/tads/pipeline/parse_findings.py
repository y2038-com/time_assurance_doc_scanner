# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Parse LLM JSON payloads into Finding objects."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from tads.schemas.findings import (
    Evidence,
    Finding,
    FindingLocation,
    FindingType,
    ScopeRelevance,
    Severity,
)
from tads.schemas.horizon import EpochKind, TimeRepresentationParams
from tads.schemas.taxonomy import Confidence, TimeDomain

_THINK_BLOCK = re.compile(
    r"<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)
_OPENING_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n", re.IGNORECASE)
_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")

_REQUIRED_FINDING_FIELDS = (
    "finding_type",
    "title",
    "description",
    "severity",
    "confidence",
    "domains",
    "evidence",
    "machine_interpretation",
    "scope_relevance",
    "scope_rationale",
)
_NULLABLE_FINDING_FIELDS = (
    "recommendation_level1",
    "time_representation",
    "section_id",
    "section_title",
)
_ALLOWED_FINDING_FIELDS = frozenset(
    _REQUIRED_FINDING_FIELDS + _NULLABLE_FINDING_FIELDS
)
_ALLOWED_EVIDENCE_FIELDS = frozenset({"quote", "note"})
_ALLOWED_TIME_REP_FIELDS = frozenset(
    {
        "width_bits",
        "signed",
        "epoch_kind",
        "epoch",
        "unit",
        "ticks_per_second",
        "claimed_horizon",
        "rollover_behavior",
    }
)
_TIME_UNITS = frozenset(
    {
        "seconds",
        "milliseconds",
        "microseconds",
        "nanoseconds",
        "days",
        "weeks",
        "ticks",
    }
)
_TIME_REP_VALUE_FIELDS = (
    "width_bits",
    "signed",
    "epoch_kind",
    "epoch",
    "unit",
    "ticks_per_second",
    "claimed_horizon",
    "rollover_behavior",
)


class FindingParseError(ValueError):
    """Raised when model output cannot be parsed into findings JSON.

    ``raw`` may hold the provider payload for opt-in raw persistence only.
    It is sensitive internal state: never include it in ``str(exc)``, logs,
    or progress output, and do not chain it as another exception.
    """

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


class _ItemValidationError(ValueError):
    """Item-level failure with a controlled category and field name only."""

    def __init__(self, category: str, field: Optional[str] = None) -> None:
        self.category = category
        self.field = field
        super().__init__(_item_error_text(0, category, field).replace("Finding item 0: ", "", 1))


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Accept one complete findings JSON object after think-block and single-fence cleanup.

    Mixed prose, multiple fences, list-root JSON, extra trailing data, or duplicate
    object keys are rejected so a later protected LLM repair can run. This function
    does not search for the longest embedded object.
    """
    cleaned = _THINK_BLOCK.sub("", text).strip()
    cleaned = _unwrap_single_fence(cleaned)
    data: Any | None = None
    parse_error: FindingParseError | None = None
    try:
        data = _loads_reject_duplicate_keys(cleaned)
    except DuplicateJsonKeyError:
        parse_error = FindingParseError("Duplicate JSON object key", raw=text)
    except json.JSONDecodeError:
        repaired = _repair_json(cleaned)
        try:
            data = _loads_reject_duplicate_keys(repaired)
        except DuplicateJsonKeyError:
            parse_error = FindingParseError("Duplicate JSON object key", raw=text)
        except json.JSONDecodeError:
            parse_error = FindingParseError(
                "Could not parse JSON object from model response",
                raw=text,
            )
    if parse_error is not None:
        raise parse_error
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


def _loads_reject_duplicate_keys(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def _reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateJsonKeyError()
        out[key] = value
    return out


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
    """Validate every finding item. One malformed item fails the whole payload."""
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise FindingParseError("Expected a JSON object with a findings array")
    findings: list[Finding] = []
    next_id = id_start
    tads_locators = (
        default_section_id is not None or default_section_title is not None
    )
    for index, item in enumerate(raw_findings):
        item_error: FindingParseError | None = None
        try:
            finding = _validate_finding(
                item,
                finding_id=f"F-{next_id:03d}",
                default_section_id=default_section_id,
                default_section_title=default_section_title,
                tads_locators=tads_locators,
            )
        except _ItemValidationError as exc:
            item_error = FindingParseError(
                _item_error_text(index, exc.category, exc.field)
            )
        if item_error is not None:
            raise item_error
        findings.append(finding)
        next_id += 1
    return findings


def _item_error_text(index: int, category: str, field: Optional[str]) -> str:
    if field and _SAFE_FIELD_NAME.fullmatch(field):
        return f"Finding item {index}: {category} field {field}"
    return f"Finding item {index}: {category}"


def _validate_finding(
    item: Any,
    *,
    finding_id: str,
    default_section_id: Optional[str],
    default_section_title: Optional[str],
    tads_locators: bool,
) -> Finding:
    if not isinstance(item, dict):
        raise _ItemValidationError("invalid type")
    extra = [key for key in item if key not in _ALLOWED_FINDING_FIELDS]
    if extra:
        raise _ItemValidationError("extra", extra[0])
    for field in _REQUIRED_FINDING_FIELDS:
        if field not in item:
            raise _ItemValidationError("missing", field)

    finding_type = _enum_value(item["finding_type"], FindingType, "finding_type")
    title = _require_nonempty_str(item["title"], "title")
    description = _require_nonempty_str(item["description"], "description")
    severity = _enum_value(item["severity"], Severity, "severity")
    confidence = _enum_value(item["confidence"], Confidence, "confidence")
    domains = _validate_domains(item["domains"])
    evidence = _validate_evidence(item["evidence"])
    machine_interpretation = _require_nonempty_str(
        item["machine_interpretation"], "machine_interpretation"
    )
    recommendation = _require_string_or_null(
        item.get("recommendation_level1"), "recommendation_level1"
    )
    scope_relevance = _enum_value(
        item["scope_relevance"], ScopeRelevance, "scope_relevance"
    )
    scope_rationale = _require_nonempty_str(
        item["scope_rationale"], "scope_rationale"
    )
    time_rep = _validate_time_representation(item.get("time_representation"))
    section_id, section_title = _validate_section_fields(
        item,
        default_section_id=default_section_id,
        default_section_title=default_section_title,
        tads_locators=tads_locators,
    )
    invalid = False
    try:
        finding = Finding(
            id=finding_id,
            finding_type=finding_type,
            title=title,
            description=description,
            severity=severity,
            confidence=confidence,
            domains=domains,
            location=FindingLocation(
                section_id=section_id, section_title=section_title
            ),
            evidence=evidence,
            machine_interpretation=machine_interpretation,
            recommendation_level1=recommendation,
            time_representation=time_rep,
            scope_relevance=scope_relevance,
            scope_rationale=scope_rationale,
        )
    except Exception:
        invalid = True
        finding = None
    if invalid or finding is None:
        raise _ItemValidationError("invalid")
    return finding


def _validate_section_fields(
    item: dict[str, Any],
    *,
    default_section_id: Optional[str],
    default_section_title: Optional[str],
    tads_locators: bool,
) -> tuple[Optional[str], Optional[str]]:
    model_id = _optional_string_or_null(item, "section_id")
    model_title = _optional_string_or_null(item, "section_title")
    if tads_locators:
        return default_section_id, default_section_title
    return model_id, model_title


def _optional_string_or_null(item: dict[str, Any], field: str) -> Optional[str]:
    if field not in item or item[field] is None:
        return None
    value = item[field]
    if not isinstance(value, str):
        raise _ItemValidationError("invalid type", field)
    text = value.strip()
    return text or None


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise _ItemValidationError("invalid type", field)
    text = value.strip()
    if not text:
        raise _ItemValidationError("invalid", field)
    return text


def _require_string_or_null(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _ItemValidationError("invalid type", field)
    text = value.strip()
    if not text:
        raise _ItemValidationError("invalid", field)
    return text


def _enum_value(value: Any, enum_cls: type, field: str) -> Any:
    if not isinstance(value, str):
        raise _ItemValidationError("invalid type", field)
    token = value.strip().casefold()
    if not token:
        raise _ItemValidationError("invalid", field)
    allowed = {member.value for member in enum_cls}
    if token not in allowed:
        raise _ItemValidationError("invalid", field)
    return enum_cls(token)


def _validate_domains(value: Any) -> list[TimeDomain]:
    if not isinstance(value, list):
        raise _ItemValidationError("invalid type", "domains")
    out: list[TimeDomain] = []
    seen: set[TimeDomain] = set()
    for entry in value:
        domain = _enum_value(entry, TimeDomain, "domains")
        if domain not in seen:
            seen.add(domain)
            out.append(domain)
    return out


def _validate_evidence(value: Any) -> list[Evidence]:
    if not isinstance(value, list):
        raise _ItemValidationError("invalid type", "evidence")
    evidence: list[Evidence] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _ItemValidationError("invalid type", "evidence")
        extra = [key for key in entry if key not in _ALLOWED_EVIDENCE_FIELDS]
        if extra:
            raise _ItemValidationError("extra", extra[0])
        if "quote" not in entry:
            raise _ItemValidationError("missing", "evidence.quote")
        quote = _require_nonempty_str(entry["quote"], "evidence.quote")
        note: Optional[str] = None
        if "note" in entry:
            note = _require_string_or_null(entry["note"], "evidence.note")
        evidence.append(Evidence(quote=quote, note=note))
    return evidence


def _validate_time_representation(value: Any) -> Optional[TimeRepresentationParams]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _ItemValidationError("invalid type", "time_representation")
    extra = [key for key in value if key not in _ALLOWED_TIME_REP_FIELDS]
    if extra:
        raise _ItemValidationError("extra", extra[0])
    width_bits = _optional_json_int(value, "width_bits")
    signed = _optional_json_bool(value, "signed")
    epoch_kind = _optional_enum(value, "epoch_kind", EpochKind)
    epoch = _optional_epoch_datetime(value, "epoch")
    unit = _optional_unit(value, "unit")
    ticks = _optional_json_number(value, "ticks_per_second")
    claimed = _optional_horizon_moment(value, "claimed_horizon")
    rollover = None
    if "rollover_behavior" in value:
        rollover = _require_string_or_null(
            value["rollover_behavior"], "rollover_behavior"
        )
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
    if all(getattr(params, name) is None for name in _TIME_REP_VALUE_FIELDS):
        return None
    return params


def _optional_enum(obj: dict[str, Any], field: str, enum_cls: type) -> Any:
    if field not in obj or obj[field] is None:
        return None
    return _enum_value(obj[field], enum_cls, field)


def _optional_unit(obj: dict[str, Any], field: str) -> Optional[str]:
    if field not in obj or obj[field] is None:
        return None
    if not isinstance(obj[field], str):
        raise _ItemValidationError("invalid type", field)
    token = obj[field].strip().casefold()
    if not token:
        raise _ItemValidationError("invalid", field)
    if token not in _TIME_UNITS:
        raise _ItemValidationError("invalid", field)
    return token


def _optional_json_int(obj: dict[str, Any], field: str) -> Optional[int]:
    if field not in obj or obj[field] is None:
        return None
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ItemValidationError("invalid type", field)
    return value


def _optional_json_bool(obj: dict[str, Any], field: str) -> Optional[bool]:
    if field not in obj or obj[field] is None:
        return None
    value = obj[field]
    if not isinstance(value, bool):
        raise _ItemValidationError("invalid type", field)
    return value


def _optional_json_number(obj: dict[str, Any], field: str) -> Optional[float]:
    if field not in obj or obj[field] is None:
        return None
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ItemValidationError("invalid type", field)
    number = float(value)
    if not math.isfinite(number):
        raise _ItemValidationError("invalid", field)
    return number


def _optional_epoch_datetime(obj: dict[str, Any], field: str) -> Optional[datetime]:
    moment = _optional_horizon_moment(obj, field)
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return moment
    return datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)


def _optional_horizon_moment(obj: dict[str, Any], field: str):
    if field not in obj or obj[field] is None:
        return None
    value = obj[field]
    if not isinstance(value, str):
        raise _ItemValidationError("invalid type", field)
    text = value.strip()
    if not text:
        raise _ItemValidationError("invalid", field)
    parsed = _parse_iso_moment(text)
    if parsed is None:
        raise _ItemValidationError("invalid", field)
    return parsed


def _parse_iso_moment(text: str):
    """Parse claimed_horizon / epoch as datetime or date; None if not ISO."""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        tail = text[10:]
        if tail == "" or tail.upper() in {"Z"}:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
