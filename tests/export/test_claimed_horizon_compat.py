# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Load/render compatibility for claimed_horizon across report schemas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.export import (
    load_report_json,
    report_to_json,
    report_to_markdown,
    write_report_json,
    write_report_markdown,
)
from tads.export.markdown import atx_heading_lines, is_allowed_atx_heading
from tads.pipeline import run_scan
from tads.pipeline.parse_findings import FindingParseError, parse_findings_payload
from tads.pipeline.validate import apply_horizon_validation
from tads.schemas.findings import (
    Evidence,
    Finding,
    FindingType,
    Severity,
)
from tads.schemas.horizon import (
    ClaimedHorizon,
    HorizonPrecision,
    HorizonValidation,
    TimeRepresentationParams,
)
from tads.schemas.report import DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds. TIME is valid until the year 2036.

2. Details

More text about epochs.
"""


def _legacy_finding(**overrides) -> dict:
    item = {
        "id": "F-001",
        "finding_type": "time_assurance_gap",
        "title": "Until 2036",
        "description": "32-bit seconds wrap.",
        "severity": "medium",
        "confidence": "medium",
        "machine_interpretation": "Potential wrap.",
        "disposition": "new",
        "validation_status": "verified",
        "time_representation": {
            "width_bits": 32,
            "signed": False,
            "epoch": "1900-01-01T00:00:00Z",
            "unit": "seconds",
            "claimed_horizon": "2036-02-07",
        },
        "horizon_validation": {
            "status": "verified",
            "claimed_horizon": "2036-02-07",
            "claim_consistent": True,
            "notes": ["Deterministic bounds computed."],
        },
    }
    item.update(overrides)
    return item


def _legacy_report(schema_version: str, finding: dict | None = None) -> str:
    payload = {
        "schema_version": schema_version,
        "document": {"corpus": "ietf", "doc_id": "RFC868", "title": "Time Protocol"},
        "run": {"scanner_version": "0.5.1"},
        "findings": [finding or _legacy_finding()],
    }
    return json.dumps(payload)


def test_schema_010_and_020_scalar_claims_load_and_render():
    for version in ("0.1.0", "0.2.0"):
        report = Report.model_validate_json(_legacy_report(version))
        assert report.schema_version == version
        claimed = report.findings[0].time_representation.claimed_horizon
        assert claimed is not None
        assert claimed.value == "2036-02-07"
        assert claimed.precision == HorizonPrecision.DAY
        md = report_to_markdown(report)
        assert "2036-02-07" in md
        assert "(day precision)" not in md
        assert "(year precision)" not in md
        assert "Model-stated horizon" in md
        dumped = json.loads(report_to_json(report))
        assert dumped["schema_version"] == version
        assert dumped["findings"][0]["time_representation"]["claimed_horizon"] == "2036-02-07"


def test_schema_020_datetime_scalar_keeps_instant_render():
    finding = _legacy_finding()
    finding["time_representation"]["claimed_horizon"] = "2036-02-07T06:28:16Z"
    finding["horizon_validation"]["claimed_horizon"] = "2036-02-07T06:28:16Z"
    report = Report.model_validate_json(_legacy_report("0.2.0", finding))
    claimed = report.findings[0].time_representation.claimed_horizon
    assert claimed is not None
    assert claimed.precision == HorizonPrecision.INSTANT
    assert claimed.value == "2036-02-07T06:28:16Z"
    md = report_to_markdown(report)
    assert "2036-02-07T06:28:16Z" in md
    assert "(instant precision)" not in md
    dumped = json.loads(report_to_json(report))
    assert dumped["findings"][0]["time_representation"]["claimed_horizon"] == (
        "2036-02-07T06:28:16Z"
    )


def test_render_does_not_rewrite_legacy_json_file(tmp_path: Path):
    path = tmp_path / "old.json"
    original = _legacy_report("0.2.0")
    path.write_text(original, encoding="utf-8")
    before = path.read_bytes()
    report = load_report_json(path)
    report_to_markdown(report)
    runner = CliRunner()
    md_path = tmp_path / "old.md"
    result = runner.invoke(app, ["render", str(path), "-o", str(md_path)])
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert "2036-02-07" in md_path.read_text(encoding="utf-8")


def test_new_reports_write_schema_030():
    report = run_scan(SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False)
    assert report.schema_version == "0.3.0"
    dumped = json.loads(report_to_json(report))
    assert dumped["schema_version"] == "0.3.0"
    claim = dumped["findings"][0]["time_representation"]["claimed_horizon"]
    assert claim == {"value": "2036-02-07", "precision": "day"}


def test_structured_claims_render_without_inventing_precision():
    finding = apply_horizon_validation(
        Finding(
            id="F-001",
            finding_type=FindingType.TIME_ASSURANCE_GAP,
            title="Until 2036",
            description="The document says until the year 2036.",
            severity=Severity.MEDIUM,
            confidence=Confidence.MEDIUM,
            machine_interpretation="Year-only source claim.",
            evidence=[Evidence(quote="until the year 2036")],
            time_representation=TimeRepresentationParams(
                width_bits=32,
                signed=False,
                epoch_kind="ntp",
                unit="seconds",
                claimed_horizon=ClaimedHorizon(
                    value="2036", precision=HorizonPrecision.YEAR
                ),
            ),
        )
    )
    report = Report(
        document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
        run=RunMetadata(scanner_version="0.6.0rc2"),
        findings=[finding],
    )
    assert report.schema_version == "0.3.0"
    md = report_to_markdown(report)
    assert "2036 (year precision)" in md
    assert "2036-01-01" not in md
    assert "2036-12-31" not in md
    assert "not compared as an exact instant" in md
    assert "Source-stated horizon" in md
    json_text = report_to_json(report)
    assert '"value": "2036"' in json_text
    assert '"precision": "year"' in json_text
    assert "2036-01-01" not in json_text

    month = finding.model_copy(
        update={
            "time_representation": TimeRepresentationParams(
                width_bits=32,
                signed=False,
                epoch_kind="ntp",
                unit="seconds",
                claimed_horizon=ClaimedHorizon(
                    value="2036-02", precision=HorizonPrecision.MONTH
                ),
            )
        }
    )
    month_md = report_to_markdown(
        Report(
            document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
            run=RunMetadata(scanner_version="0.6.0rc2"),
            findings=[apply_horizon_validation(month)],
        )
    )
    assert "2036-02 (month precision)" in month_md
    assert "2036-02-01" not in month_md

    day_md = report_to_markdown(
        Report(
            document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
            run=RunMetadata(scanner_version="0.6.0rc2"),
            findings=[
                apply_horizon_validation(
                    finding.model_copy(
                        update={
                            "time_representation": TimeRepresentationParams(
                                width_bits=32,
                                signed=False,
                                epoch_kind="ntp",
                                unit="seconds",
                                claimed_horizon=ClaimedHorizon(
                                    value="2036-02-07",
                                    precision=HorizonPrecision.DAY,
                                ),
                            )
                        }
                    )
                )
            ],
        )
    )
    assert "2036-02-07 (day precision)" in day_md

    instant_md = report_to_markdown(
        Report(
            document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
            run=RunMetadata(scanner_version="0.6.0rc2"),
            findings=[
                apply_horizon_validation(
                    finding.model_copy(
                        update={
                            "time_representation": TimeRepresentationParams(
                                width_bits=32,
                                signed=False,
                                epoch_kind="ntp",
                                unit="seconds",
                                claimed_horizon=ClaimedHorizon(
                                    value="2036-02-07T06:28:16Z",
                                    precision=HorizonPrecision.INSTANT,
                                ),
                            )
                        }
                    )
                )
            ],
        )
    )
    assert "2036-02-07T06:28:16Z" in instant_md
    assert "(instant precision)" not in instant_md


def _horizon_report_markdown(**repr_kwargs) -> tuple[str, object]:
    finding = apply_horizon_validation(
        Finding(
            id="F-001",
            finding_type=FindingType.TIME_ASSURANCE_GAP,
            title="Until 2036",
            description="The document says until the year 2036.",
            severity=Severity.MEDIUM,
            confidence=Confidence.MEDIUM,
            machine_interpretation="Source claim.",
            evidence=[Evidence(quote="until the year 2036")],
            time_representation=TimeRepresentationParams(**repr_kwargs),
        )
    )
    md = report_to_markdown(
        Report(
            document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
            run=RunMetadata(scanner_version="0.6.0rc2"),
            findings=[finding],
        )
    )
    return md, finding


def test_year_and_month_partial_precision_result_when_validation_completes():
    year_md, year_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        epoch_kind="ntp",
        unit="seconds",
        claimed_horizon=ClaimedHorizon(value="2036", precision=HorizonPrecision.YEAR),
    )
    assert year_finding.horizon_validation is not None
    assert year_finding.horizon_validation.status == "verified"
    assert year_finding.horizon_validation.claim_consistent is None
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        in year_md
    )
    assert "validation not completed" not in year_md

    month_md, month_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        epoch_kind="ntp",
        unit="seconds",
        claimed_horizon=ClaimedHorizon(
            value="2036-02", precision=HorizonPrecision.MONTH
        ),
    )
    assert month_finding.horizon_validation is not None
    assert month_finding.horizon_validation.status == "verified"
    assert month_finding.horizon_validation.claim_consistent is None
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        in month_md
    )
    assert "validation not completed" not in month_md


def test_year_and_month_insufficient_parameters_result_takes_precedence():
    year_md, year_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        claimed_horizon=ClaimedHorizon(value="2036", precision=HorizonPrecision.YEAR),
    )
    assert year_finding.horizon_validation is not None
    assert year_finding.horizon_validation.status == "insufficient_parameters"
    assert year_finding.horizon_validation.claim_consistent is None
    assert "validation not completed" in year_md
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        not in year_md
    )

    month_md, month_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        claimed_horizon=ClaimedHorizon(
            value="2036-02", precision=HorizonPrecision.MONTH
        ),
    )
    assert month_finding.horizon_validation is not None
    assert month_finding.horizon_validation.status == "insufficient_parameters"
    assert month_finding.horizon_validation.claim_consistent is None
    assert "validation not completed" in month_md
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        not in month_md
    )


def test_day_and_instant_result_lines_remain_exact_comparison():
    day_md, day_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        epoch_kind="ntp",
        unit="seconds",
        claimed_horizon=ClaimedHorizon(
            value="2036-02-07", precision=HorizonPrecision.DAY
        ),
    )
    assert day_finding.horizon_validation is not None
    assert day_finding.horizon_validation.claim_consistent is True
    assert "- Result: **Consistent** with deterministic calculation" in day_md
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        not in day_md
    )

    instant_md, instant_finding = _horizon_report_markdown(
        width_bits=32,
        signed=False,
        epoch_kind="ntp",
        unit="seconds",
        claimed_horizon=ClaimedHorizon(
            value="2036-02-07T06:28:16Z", precision=HorizonPrecision.INSTANT
        ),
    )
    assert instant_finding.horizon_validation is not None
    assert instant_finding.horizon_validation.claim_consistent is True
    assert "- Result: **Consistent** with deterministic calculation" in instant_md
    assert (
        "- Result: not compared as an exact instant (partial source precision)"
        not in instant_md
    )
    assert "2036-02-07T06:28:16Z" in instant_md
    assert "(instant precision)" not in instant_md


def test_markdown_does_not_mutate_json(tmp_path: Path):
    report = run_scan(SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False)
    json_path = tmp_path / "report.json"
    write_report_json(report, json_path)
    before = json_path.read_bytes()
    md1 = report_to_markdown(report)
    write_report_markdown(report, tmp_path / "report.md")
    after = json_path.read_bytes()
    assert before == after
    loaded = load_report_json(json_path)
    assert report_to_markdown(loaded) == md1
    assert json.loads(before.decode("utf-8"))["schema_version"] == "0.3.0"


def test_scan_markdown_matches_tads_render(tmp_path: Path):
    report = run_scan(SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False)
    scan_md = report_to_markdown(report)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    write_report_json(report, json_path)
    runner = CliRunner()
    result = runner.invoke(app, ["render", str(json_path), "-o", str(md_path)])
    assert result.exit_code == 0, result.output
    assert md_path.read_bytes() == scan_md.encode("utf-8")


def test_hostile_claim_adjacent_text_stays_escaped():
    finding = Finding(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="## Findings",
        description="<script>x</script>\n[phish](javascript:1)",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        machine_interpretation="interp",
        evidence=[Evidence(quote="until the year 2036")],
        time_representation=TimeRepresentationParams(
            claimed_horizon=ClaimedHorizon(
                value="2036", precision=HorizonPrecision.YEAR
            ),
            rollover_behavior="wrap\n## Findings\n[x](javascript:1)",
        ),
        horizon_validation=HorizonValidation(
            status="verified",
            claimed_horizon=ClaimedHorizon(
                value="2036", precision=HorizonPrecision.YEAR
            ),
            claim_consistent=None,
            notes=["Source-stated claimed_horizon has year or month precision."],
        ),
    )
    md = report_to_markdown(
        Report(
            document=DocumentIdentity(corpus="ietf", doc_id="RFC868"),
            run=RunMetadata(scanner_version="0.6.0rc2"),
            findings=[finding],
        )
    )
    assert "2036 (year precision)" in md
    for marker, text in atx_heading_lines(md):
        assert is_allowed_atx_heading(marker, text), (marker, text)
    assert md.splitlines()[0] == "# Time Assurance Scan Report"
    assert "<script>" not in md
    assert "](javascript:1)" not in md


def test_parser_rejects_legacy_scalar_on_new_contract():
    payload = {
        "findings": [
            {
                "finding_type": "time_assurance_gap",
                "title": "Until 2036",
                "description": "desc",
                "severity": "medium",
                "confidence": "medium",
                "domains": ["y2036"],
                "evidence": [{"quote": "until the year 2036", "note": None}],
                "machine_interpretation": "interp",
                "scope_relevance": "core",
                "scope_rationale": "This matters because the counter wraps.",
                "time_representation": {
                    "width_bits": 32,
                    "signed": False,
                    "unit": "seconds",
                    "claimed_horizon": "2036",
                },
            }
        ]
    }
    with pytest.raises(FindingParseError):
        parse_findings_payload(payload)
