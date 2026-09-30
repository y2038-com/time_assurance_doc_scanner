# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Strict Policy A findings item validation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tads.pipeline.parse_findings import (
    _NULLABLE_FINDING_FIELDS,
    _REQUIRED_FINDING_FIELDS,
    FindingParseError,
    parse_findings_payload,
)
from tads.prompts import FINDING_JSON_INSTRUCTIONS, PROMPT_FRAMEWORK_VERSION
from tads.schemas.findings import Disposition, ScopeRelevance, ValidationStatus
from tads.schemas.horizon import EpochKind
from tads.schemas.taxonomy import TimeDomain


def _valid_item(**overrides) -> dict:
    item = {
        "finding_type": "time_assurance_gap",
        "title": "Era wrap guidance may be incomplete",
        "description": "32-bit NTP seconds wrap on a known horizon.",
        "severity": "medium",
        "confidence": "medium",
        "domains": ["y2036", "rollover"],
        "section_id": "s-1",
        "section_title": "1. Introduction",
        "evidence": [
            {"quote": "Timestamps are 32-bit seconds", "note": "width mentioned"}
        ],
        "machine_interpretation": "Potential Y2036-related assurance gap.",
        "recommendation_level1": "Clarify era rollover.",
        "scope_relevance": "core",
        "scope_rationale": (
            "This matters to time assurance because NTP seconds are a "
            "fixed-width counter that wraps on a known horizon."
        ),
        "time_representation": {
            "width_bits": 32,
            "signed": False,
            "epoch": "1900-01-01T00:00:00Z",
            "unit": "seconds",
            "ticks_per_second": None,
            "claimed_horizon": "2036-02-07",
            "rollover_behavior": "wrap",
        },
    }
    item.update(overrides)
    return item


def test_empty_findings_array_is_valid_zero_finding_response():
    assert parse_findings_payload({"findings": []}) == []


def test_one_valid_finding_is_unchanged():
    findings = parse_findings_payload({"findings": [_valid_item()]})
    assert len(findings) == 1
    finding = findings[0]
    assert finding.id == "F-001"
    assert finding.title == "Era wrap guidance may be incomplete"
    assert finding.severity.value == "medium"
    assert TimeDomain.Y2036 in finding.domains
    assert finding.disposition == Disposition.NEW
    assert finding.validation_status == ValidationStatus.UNVERIFIED
    assert finding.source_verified is False
    assert finding.horizon_validation is None
    assert finding.time_representation is not None
    assert finding.time_representation.width_bits == 32
    assert finding.time_representation.signed is False


def test_canonical_valid_output_is_stable():
    first = parse_findings_payload({"findings": [_valid_item()]})[0]
    second = parse_findings_payload({"findings": [_valid_item()]})[0]
    assert first.model_dump() == second.model_dump()


def test_two_similar_valid_items_are_both_kept():
    left = _valid_item(title="First copy")
    right = _valid_item(title="First copy", description="Second distinct body.")
    findings = parse_findings_payload({"findings": [left, right]})
    assert len(findings) == 2
    assert findings[0].id == "F-001"
    assert findings[1].id == "F-002"


def test_enum_trim_and_casefold_only():
    item = _valid_item(
        severity=" HIGH ",
        confidence="Low",
        scope_relevance="CORE",
        domains=["Y2036", " Rollover "],
    )
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.severity.value == "high"
    assert finding.confidence.value == "low"
    assert finding.scope_relevance == ScopeRelevance.CORE
    assert finding.domains == [TimeDomain.Y2036, TimeDomain.ROLLOVER]


def test_malformed_item_fails_the_payload():
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid field severity"):
        parse_findings_payload({"findings": [_valid_item(severity="banana")]})


def test_mixed_valid_then_malformed_fails():
    with pytest.raises(FindingParseError, match=r"Finding item 1:"):
        parse_findings_payload(
            {"findings": [_valid_item(), _valid_item(severity="banana")]}
        )


def test_mixed_malformed_then_valid_fails():
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload(
            {"findings": [_valid_item(severity="banana"), _valid_item()]}
        )


def test_several_malformed_items_fail_on_first():
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload(
            {
                "findings": [
                    _valid_item(severity="banana"),
                    _valid_item(confidence="nope"),
                ]
            }
        )


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_missing_required_field_fails(field: str):
    item = _valid_item()
    del item[field]
    with pytest.raises(
        FindingParseError, match=rf"Finding item 0: missing field {field}"
    ):
        parse_findings_payload({"findings": [item]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", "   "),
        ("description", ""),
        ("machine_interpretation", ""),
        ("scope_rationale", ""),
        ("severity", ""),
        ("finding_type", "not_a_type"),
        ("scope_relevance", "primary"),
        ("scope_relevance", "out-of-scope"),
        ("confidence", "0.9"),
    ],
)
def test_invalid_required_string_or_enum_fails(field: str, value: object):
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload({"findings": [_valid_item(**{field: value})]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", 123),
        ("confidence", True),
        ("confidence", 0.9),
        ("confidence", None),
        ("severity", None),
        ("domains", "y2038"),
        ("evidence", "a quote"),
        ("finding_type", 1),
        ("section_id", ["6", "8"]),
        ("section_title", ["Data Types", "On-Wire Protocol"]),
        ("recommendation_level1", False),
        ("time_representation", False),
        ("time_representation", "null"),
    ],
)
def test_strict_types_fail(field: str, value: object):
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid type"):
        parse_findings_payload({"findings": [_valid_item(**{field: value})]})


def test_empty_domains_and_evidence_arrays_are_allowed():
    item = _valid_item(domains=[], evidence=[], time_representation=None)
    findings = parse_findings_payload({"findings": [item]})
    assert findings[0].domains == []
    assert findings[0].evidence == []
    assert findings[0].time_representation is None


def test_recommendation_null_is_allowed():
    finding = parse_findings_payload(
        {"findings": [_valid_item(recommendation_level1=None)]}
    )[0]
    assert finding.recommendation_level1 is None


def test_unknown_domain_is_not_mapped_to_other():
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid field domains"):
        parse_findings_payload({"findings": [_valid_item(domains=["ntp era"])]})


def test_documented_other_domain_is_allowed():
    finding = parse_findings_payload({"findings": [_valid_item(domains=["other"])]})[0]
    assert finding.domains == [TimeDomain.OTHER]


def test_gps_alias_is_rejected_gps_time_token_is_kept():
    with pytest.raises(FindingParseError, match=r"invalid field domains"):
        parse_findings_payload({"findings": [_valid_item(domains=["gps"])]})
    finding = parse_findings_payload({"findings": [_valid_item(domains=["gps_time"])]})[0]
    assert finding.domains == [TimeDomain.GPS_TIME]


def test_non_object_finding_item_fails():
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid type"):
        parse_findings_payload({"findings": ["not an object"]})


def test_extra_and_protected_fields_are_rejected():
    for field, value in [
        ("disposition", "accepted"),
        ("reviewer_notes", "forged"),
        ("validation_status", "verified"),
        ("validation_detail", "forged"),
        ("source_verified", True),
        ("source_verification_detail", "forged"),
        ("horizon_validation", {"status": "verified"}),
        ("id", "F-999"),
        ("location", {"section_id": "x"}),
        ("bonus", "nope"),
    ]:
        with pytest.raises(FindingParseError, match=r"Finding item 0: extra field"):
            parse_findings_payload({"findings": [_valid_item(**{field: value})]})


def test_exception_omits_item_values():
    canary = "CANARY_SECRET_xyz_not_in_logs"
    with pytest.raises(FindingParseError) as exc:
        parse_findings_payload(
            {"findings": [_valid_item(title=canary, severity="banana")]}
        )
    text = str(exc.value)
    assert canary not in text
    assert "Finding item 0" in text
    assert "severity" in text


def test_unsafe_extra_field_name_is_omitted_from_error():
    item = _valid_item()
    item["!!!CANARY_FIELD!!!"] = "secret"
    with pytest.raises(FindingParseError) as exc:
        parse_findings_payload({"findings": [item]})
    text = str(exc.value)
    assert "CANARY_FIELD" not in text
    assert "Finding item 0: extra" in text


def test_time_representation_rejects_stringified_and_aliased_values():
    item = _valid_item(
        time_representation={
            "width_bits": "32",
            "signed": "unsigned",
            "epoch": "1900-01-01",
            "unit": "seconds",
        }
    )
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload({"findings": [item]})


def test_time_representation_canonical_integers_and_bools():
    item = _valid_item(
        time_representation={
            "width_bits": 32,
            "signed": False,
            "epoch_kind": "ntp",
            "epoch": "1900-01-01",
            "unit": "seconds",
            "claimed_horizon": "2036-02-07",
            "ticks_per_second": None,
            "rollover_behavior": None,
        }
    )
    rep = parse_findings_payload({"findings": [item]})[0].time_representation
    assert rep is not None
    assert rep.width_bits == 32
    assert rep.signed is False
    assert rep.epoch_kind == EpochKind.NTP
    assert rep.epoch is not None
    assert rep.epoch.year == 1900
    assert rep.unit == "seconds"
    assert rep.claimed_horizon == date(2036, 2, 7)


def test_all_null_time_representation_object_is_dropped():
    item = _valid_item(
        time_representation={
            "width_bits": None,
            "signed": None,
            "epoch": None,
            "unit": None,
            "claimed_horizon": None,
        }
    )
    assert (
        parse_findings_payload({"findings": [item]})[0].time_representation is None
    )


def test_epoch_kind_aliases_are_rejected():
    item = _valid_item(
        time_representation={"epoch_kind": "POSIX", "unit": "seconds"}
    )
    with pytest.raises(FindingParseError, match=r"invalid field epoch_kind"):
        parse_findings_payload({"findings": [item]})


def test_nonfinite_ticks_are_rejected():
    item = _valid_item(
        time_representation={"unit": "ticks", "ticks_per_second": float("nan")}
    )
    with pytest.raises(FindingParseError, match=r"Finding item 0: invalid field"):
        parse_findings_payload({"findings": [item]})


def test_date_only_claimed_horizon_stays_date():
    item = _valid_item(
        time_representation={
            "width_bits": 32,
            "signed": True,
            "epoch": "1970-01-01T00:00:00Z",
            "unit": "seconds",
            "claimed_horizon": "2038-01-19",
        }
    )
    claimed = parse_findings_payload({"findings": [item]})[0].time_representation.claimed_horizon
    assert claimed == date(2038, 1, 19)
    assert not hasattr(claimed, "hour")


def test_section_aware_tads_locators_are_authoritative():
    item = _valid_item(section_id="model-section", section_title="Model Title")
    finding = parse_findings_payload(
        {"findings": [item]},
        default_section_id="sec-tads",
        default_section_title="TADS Title",
    )[0]
    assert finding.location is not None
    assert finding.location.section_id == "sec-tads"
    assert finding.location.section_title == "TADS Title"


def test_whole_document_model_section_strings_are_used():
    item = _valid_item(section_id="  s-9  ", section_title=" On-Wire ")
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.location is not None
    assert finding.location.section_id == "s-9"
    assert finding.location.section_title == "On-Wire"


def test_whole_document_list_section_fields_fail():
    with pytest.raises(FindingParseError, match=r"invalid type field section_id"):
        parse_findings_payload({"findings": [_valid_item(section_id=["6", "8"])]})


def test_whole_document_null_section_locators_are_none():
    finding = parse_findings_payload(
        {"findings": [_valid_item(section_id=None, section_title=None)]}
    )[0]
    assert finding.location is not None
    assert finding.location.section_id is None
    assert finding.location.section_title is None


def test_whole_document_omitted_section_locators_are_none():
    item = _valid_item()
    del item["section_id"]
    del item["section_title"]
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.location is not None
    assert finding.location.section_id is None
    assert finding.location.section_title is None


def test_whitespace_only_section_locators_are_none():
    finding = parse_findings_payload(
        {"findings": [_valid_item(section_id="   ", section_title="\t")]}
    )[0]
    assert finding.location.section_id is None
    assert finding.location.section_title is None


@pytest.mark.parametrize("field", ["section_id", "section_title"])
@pytest.mark.parametrize("value", [6, True, False, ["6"], {"id": "s-1"}])
def test_invalid_section_locator_types_fail(field: str, value: object):
    with pytest.raises(
        FindingParseError, match=rf"Finding item 0: invalid type field {field}"
    ):
        parse_findings_payload({"findings": [_valid_item(**{field: value})]})


def test_section_aware_invalid_locator_type_still_fails():
    with pytest.raises(FindingParseError, match=r"invalid type field section_id"):
        parse_findings_payload(
            {"findings": [_valid_item(section_id=6)]},
            default_section_id="sec-tads",
            default_section_title="TADS Title",
        )


def test_section_aware_null_locators_remain_tads_authored():
    finding = parse_findings_payload(
        {"findings": [_valid_item(section_id=None, section_title=None)]},
        default_section_id="sec-tads",
        default_section_title="TADS Title",
    )[0]
    assert finding.location.section_id == "sec-tads"
    assert finding.location.section_title == "TADS Title"


def test_section_aware_model_cannot_spoof_another_section():
    finding = parse_findings_payload(
        {
            "findings": [
                _valid_item(section_id="sec-other", section_title="Other Title")
            ]
        },
        default_section_id="sec-tads",
        default_section_title="TADS Title",
    )[0]
    assert finding.location.section_id == "sec-tads"
    assert finding.location.section_title == "TADS Title"
    assert finding.location.section_id != "sec-other"


def test_missing_time_representation_is_none():
    item = _valid_item()
    del item["time_representation"]
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.time_representation is None


def test_null_time_representation_is_none():
    finding = parse_findings_payload(
        {"findings": [_valid_item(time_representation=None)]}
    )[0]
    assert finding.time_representation is None


@pytest.mark.parametrize(
    "value",
    [
        True,
        "null",
        ["width_bits"],
        {"width_bits": "32", "signed": False, "unit": "seconds"},
    ],
)
def test_malformed_non_null_time_representation_fails(value: object):
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload({"findings": [_valid_item(time_representation=value)]})


def test_missing_recommendation_level1_is_none():
    item = _valid_item()
    del item["recommendation_level1"]
    finding = parse_findings_payload({"findings": [item]})[0]
    assert finding.recommendation_level1 is None


@pytest.mark.parametrize("value", [1, True, False, ["fix it"], {"text": "fix"}, "", "  "])
def test_invalid_non_null_recommendation_fails(value: object):
    with pytest.raises(FindingParseError, match=r"Finding item 0:"):
        parse_findings_payload(
            {"findings": [_valid_item(recommendation_level1=value)]}
        )


def test_malformed_item_does_not_keep_valid_siblings():
    with pytest.raises(FindingParseError, match=r"Finding item 1: invalid field title"):
        parse_findings_payload(
            {"findings": [_valid_item(), _valid_item(title="")]}
        )


def test_evidence_string_items_and_extra_keys_fail():
    with pytest.raises(FindingParseError, match=r"invalid type field evidence"):
        parse_findings_payload({"findings": [_valid_item(evidence=["just a quote"])]})
    with pytest.raises(FindingParseError, match=r"extra field"):
        parse_findings_payload(
            {
                "findings": [
                    _valid_item(
                        evidence=[{"quote": "Timestamps are 32-bit seconds", "loc": 1}]
                    )
                ]
            }
        )


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_openai_rfc868_null_locator_fixture_parses():
    payload = json.loads(
        (FIXTURES / "openai_rfc868_null_locators.json").read_text(encoding="utf-8")
    )
    findings = parse_findings_payload(payload)
    assert len(findings) == 1
    assert findings[0].location.section_id is None
    assert findings[0].location.section_title is None
    assert findings[0].time_representation is not None
    assert findings[0].time_representation.width_bits == 32


def test_openai_rfc5905_omitted_time_representation_fixture_parses():
    payload = json.loads(
        (FIXTURES / "openai_rfc5905_omitted_time_representation.json").read_text(
            encoding="utf-8"
        )
    )
    assert "time_representation" not in payload["findings"][0]
    findings = parse_findings_payload(payload)
    assert len(findings) == 1
    assert findings[0].time_representation is None
    assert findings[0].title


def test_prompt_example_parses_under_current_contract():
    start = FINDING_JSON_INSTRUCTIONS.index('{"findings":')
    end = FINDING_JSON_INSTRUCTIONS.index("}\nAbsence", start) + 1
    payload = json.loads(FINDING_JSON_INSTRUCTIONS[start:end])
    findings = parse_findings_payload(payload)
    assert len(findings) == 1
    assert findings[0].location.section_id is None
    assert findings[0].location.section_title is None
    assert findings[0].recommendation_level1 is None
    assert findings[0].time_representation is None


def test_prompt_and_parser_required_versus_nullable_fields_agree():
    assert PROMPT_FRAMEWORK_VERSION == "0.7.2"
    required_block, nullable_block = FINDING_JSON_INSTRUCTIONS.split("Nullable keys", 1)
    for field in _REQUIRED_FINDING_FIELDS:
        assert f"- {field}:" in required_block
        assert field not in _NULLABLE_FINDING_FIELDS
    assert "- recommendation_level1:" not in required_block
    assert "- time_representation:" not in required_block
    assert "- section_id" not in required_block
    assert "- section_title" not in required_block
    assert "- section_id / section_title:" in nullable_block
    assert "- recommendation_level1:" in nullable_block
    assert "- time_representation:" in nullable_block
    assert set(_NULLABLE_FINDING_FIELDS) == {
        "recommendation_level1",
        "time_representation",
        "section_id",
        "section_title",
    }
    assert "may be omitted or set to JSON null" in FINDING_JSON_INSTRUCTIONS
    assert "JSON null is not allowed" in required_block
    assert "Do not invent values" in FINDING_JSON_INSTRUCTIONS
    assert "do not point a finding at a different section" in FINDING_JSON_INSTRUCTIONS
