# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Derived provider schema matches the model-facing findings contract."""

from __future__ import annotations

from tads.llm.structured_output import (
    OPENAI_SCHEMA_NAME,
    OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS,
    gemini_response_schema,
    model_findings_json_schema,
    openai_response_format,
)
from tads.pipeline.parse_findings import (
    _ALLOWED_EVIDENCE_FIELDS,
    _ALLOWED_TIME_REP_FIELDS,
    _NULLABLE_FINDING_FIELDS,
    _REQUIRED_FINDING_FIELDS,
    _TIME_UNITS,
)
from tads.schemas.horizon import HorizonPrecision
from tads.schemas.model_output import MODEL_TIME_UNITS
from tads.schemas.taxonomy import TimeDomain

_TADS_OWNED_FIELDS = {
    "id",
    "disposition",
    "horizon_validation",
    "validation_status",
    "validation_detail",
    "source_verified",
    "source_verification_detail",
    "reviewer_notes",
    "location",
}


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for value in node.values():
        yield from _walk(value)


def _find_property(schema: dict, name: str) -> dict | None:
    for node in _walk(schema):
        properties = node.get("properties")
        if isinstance(properties, dict) and name in properties:
            found = properties[name]
            if isinstance(found, dict):
                return found
    return None


def _enum_values(node: dict | None) -> set[str]:
    values: set[str] = set()
    if not node:
        return values
    for item in _walk(node):
        raw = item.get("enum")
        if isinstance(raw, list):
            values.update(str(part) for part in raw)
    return values


def _allows_null(node: dict | None) -> bool:
    if not node:
        return False
    if node.get("type") in {"null", "NULL"}:
        return True
    if node.get("nullable") is True:
        return True
    for item in node.get("anyOf") or []:
        if isinstance(item, dict) and _allows_null(item):
            return True
    return False


def test_model_schema_is_top_level_object_with_required_findings():
    schema = model_findings_json_schema()
    assert schema.get("type") == "object"
    assert "findings" in schema.get("properties", {})
    assert "findings" in schema.get("required", [])
    assert schema.get("additionalProperties") is False


def test_finding_required_and_nullable_fields_match_parser_contract():
    schema = model_findings_json_schema()
    item = schema["$defs"]["ModelFindingItem"]
    required = set(item.get("required") or [])
    properties = set(item.get("properties") or {})
    assert set(_REQUIRED_FINDING_FIELDS) <= required
    assert set(_NULLABLE_FINDING_FIELDS) <= properties
    assert not (set(_NULLABLE_FINDING_FIELDS) & required)
    assert item.get("additionalProperties") is False
    assert properties.isdisjoint(_TADS_OWNED_FIELDS)


def test_time_units_match_local_parser():
    assert set(MODEL_TIME_UNITS) == set(_TIME_UNITS)


def test_claimed_horizon_is_object_or_null_with_precision_enum():
    schema = model_findings_json_schema()
    fragment = _find_property(schema, "claimed_horizon")
    assert fragment is not None
    assert _allows_null(fragment)
    horizon = schema["$defs"]["ClaimedHorizon"]
    assert set(horizon["properties"]) == {"value", "precision"}
    assert set(horizon["required"]) == {"value", "precision"}
    assert horizon.get("additionalProperties") is False
    assert horizon["properties"]["value"].get("type") == "string"
    precision = schema["$defs"]["HorizonPrecision"]
    assert set(precision["enum"]) == {member.value for member in HorizonPrecision}


def test_schema_does_not_encode_lexical_or_calendar_horizon_rules():
    schema = model_findings_json_schema()
    horizon = schema["$defs"]["ClaimedHorizon"]
    value = horizon["properties"]["value"]
    assert "pattern" not in value
    assert "format" not in value


def test_domain_enum_is_canonical_time_domain():
    schema = model_findings_json_schema()
    domain = schema["$defs"]["TimeDomain"]
    assert set(domain["enum"]) == {member.value for member in TimeDomain}


def test_openai_response_format_is_strict_json_schema():
    fmt = openai_response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == OPENAI_SCHEMA_NAME
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema.get("additionalProperties") is False
    assert set(schema["required"]) == set(schema["properties"])
    for node in _walk(schema):
        properties = node.get("properties")
        if isinstance(properties, dict):
            assert node.get("additionalProperties") is False
            assert set(node.get("required") or []) == set(properties)
    fragment = _find_property(schema, "claimed_horizon")
    assert _allows_null(fragment)
    precision = schema["$defs"]["HorizonPrecision"]
    assert set(precision["enum"]) == {"year", "month", "day", "instant"}


def test_openai_serialized_schema_has_no_unsupported_keywords():
    schema = openai_response_format()["json_schema"]["schema"]
    forbidden = set(OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS)
    for node in _walk(schema):
        assert forbidden.isdisjoint(node.keys())


def test_provider_model_matches_parser_field_contract():
    """Catch drift between ModelFindingsResponse and the live parser.

    Pre-existing difference: the parser accepts extra top-level envelope keys
    besides ``findings``; the provider/OpenAI schema forbids them.
    OpenAI strict also requires nullable keys to be present as JSON null.
    """
    schema = model_findings_json_schema()
    item = schema["$defs"]["ModelFindingItem"]
    assert set(item["properties"]) == set(_REQUIRED_FINDING_FIELDS) | set(
        _NULLABLE_FINDING_FIELDS
    )
    assert set(item["required"]) == set(_REQUIRED_FINDING_FIELDS)
    evidence = schema["$defs"]["ModelEvidence"]
    assert set(evidence["properties"]) == set(_ALLOWED_EVIDENCE_FIELDS)
    time_rep = schema["$defs"]["ModelTimeRepresentation"]
    assert set(time_rep["properties"]) == set(_ALLOWED_TIME_REP_FIELDS)
    assert set(schema["$defs"]["TimeDomain"]["enum"]) == {
        member.value for member in TimeDomain
    }
    assert set(MODEL_TIME_UNITS) == set(_TIME_UNITS)
    assert set(schema["$defs"]["HorizonPrecision"]["enum"]) == {
        member.value for member in HorizonPrecision
    }


def test_gemini_schema_inlines_claimed_horizon_object_or_null():
    schema = gemini_response_schema()
    assert schema.get("type") == "OBJECT"
    assert "findings" in schema.get("properties", {})
    assert "$ref" not in str(schema)
    fragment = _find_property(schema, "claimed_horizon")
    assert fragment is not None
    assert fragment.get("nullable") is True
    assert fragment.get("type") == "OBJECT"
    properties = fragment.get("properties") or {}
    assert set(properties) == {"value", "precision"}
    assert _enum_values(properties["precision"]) == {"year", "month", "day", "instant"}
    assert "additionalProperties" not in schema
    domain = _find_property(schema, "domains")
    assert domain is not None
    assert _enum_values(domain) == {member.value for member in TimeDomain}
