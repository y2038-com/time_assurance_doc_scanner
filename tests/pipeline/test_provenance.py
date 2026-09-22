# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Local-file identity and ingest provenance tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tads.cli import app
from tads.export import report_to_markdown
from tads.ingest import IngestOptions, ingest_to_text
from tads.ingest.fetch import FetchedBytes
from tads.pipeline import plan_scan, run_scan
from tads.schemas.report import DocumentIdentity, PrivacyMode, Report, RunMetadata
from datetime import datetime, timezone


SAMPLE = """\
Title: Local sample

1 Intro

Timers may wrap in 2038.
"""


def test_unidentified_plan_uses_generic_and_local_path(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    ingested = ingest_to_text(str(src))
    assert ingested.source_uri is None
    assert ingested.source_path == str(src)

    plan = plan_scan(
        ingested.text,
        doc_id="TESTDOC",
        provider="mock",
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
    )
    assert plan.document.corpus == "generic"
    assert plan.document.doc_id == "TESTDOC"
    assert plan.document.source_uri is None
    assert plan.document.source_path == str(src)
    assert plan.document.title == "Local sample"
    assert "ietf.org" not in (plan.ref.source_uri or "")


def test_explicit_ietf_normalizes_id_but_local_uri_stays_null(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    ingested = ingest_to_text(str(src))
    plan = plan_scan(
        ingested.text,
        doc_id="TESTDOC",
        corpus="ietf",
        provider="mock",
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
    )
    assert plan.document.corpus == "ietf"
    assert plan.document.doc_id == "draft-TESTDOC"
    assert plan.document.source_uri is None
    assert plan.document.source_path == str(src)
    assert plan.ref.source_uri is None


def test_detected_ietf_local_file_has_no_source_uri(tmp_path: Path):
    src = tmp_path / "rfc5905.txt"
    src.write_text("1 Intro\n\nNTP timestamps.\n", encoding="utf-8")
    ingested = ingest_to_text(str(src))
    plan = plan_scan(
        ingested.text,
        doc_id="RFC5905",
        provider="mock",
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
    )
    assert plan.document.corpus == "ietf"
    assert plan.document.doc_id == "RFC5905"
    assert plan.document.source_uri is None
    assert plan.document.source_path == str(src)


def test_run_scan_unidentified_local_report(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    ingested = ingest_to_text(str(src))
    report = run_scan(
        ingested.text,
        doc_id="TESTDOC",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
    )
    assert report.document.corpus == "generic"
    assert report.document.doc_id == "TESTDOC"
    assert report.document.source_uri is None
    assert report.document.source_path == str(src)
    md = report_to_markdown(report)
    assert "**Source path:**" in md
    assert str(src) in md
    assert "**Source URI:**" not in md
    assert "ietf.org" not in md
    assert "draft-TESTDOC" not in md


def test_markdown_shows_both_source_fields_when_present():
    report = Report(
        document=DocumentIdentity(
            corpus="generic",
            doc_id="TESTDOC",
            title="Local sample",
            source_uri="https://example.com/spec.txt",
            source_path="/tmp/saved.txt",
        ),
        run=RunMetadata(
            scanner_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            provider="mock",
            model="mock-model",
            privacy_mode=PrivacyMode.EPHEMERAL,
            prompt_framework_version="0.5.0",
        ),
        findings=[],
    )
    md = report_to_markdown(report)
    assert "**Source URI:** https://example.com/spec.txt" in md
    assert "**Source path:** /tmp/saved.txt" in md
    assert "**Source:**" not in md


def test_markdown_omits_missing_source_fields():
    report = Report(
        document=DocumentIdentity(corpus="generic", doc_id="TESTDOC"),
        run=RunMetadata(
            scanner_version="0.0.0-test",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            provider="mock",
            model="mock-model",
            privacy_mode=PrivacyMode.EPHEMERAL,
            prompt_framework_version="0.5.0",
        ),
        findings=[],
    )
    md = report_to_markdown(report)
    assert "**Source URI:**" not in md
    assert "**Source path:**" not in md
    assert "**Source:**" not in md


def test_remote_ingest_save_text_keeps_both_facts(tmp_path: Path, monkeypatch):
    def _fake_fetch(url: str, **_kwargs):
        return FetchedBytes(
            data=b"1 Intro\n\nBody.\n",
            url="https://example.com/spec.txt",
            filename="spec.txt",
            content_type="text/plain",
            media_type="text",
            notes=["Fetched 16 bytes from URL."],
        )

    monkeypatch.setattr("tads.ingest.pipeline.fetch_url", _fake_fetch)
    saved = tmp_path / "saved.txt"
    result = ingest_to_text(
        "https://example.com/spec.txt?token=secret",
        options=IngestOptions(save_text_path=str(saved), max_download_bytes=1000),
    )
    assert result.source_uri == "https://example.com/spec.txt"
    assert result.source_path == str(saved)
    assert result.saved_text_path == str(saved)


def test_cli_plan_unidentified_local_is_generic(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            str(src),
            "--doc-id",
            "TESTDOC",
            "--json",
            "--provider",
            "mock",
        ],
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["corpus"] == "generic"
    assert data["doc_id"] == "TESTDOC"
    assert data["source_uri"] is None
    assert data["source_path"] == str(src)
    assert "ietf.org" not in result.stdout


def test_cli_plan_explicit_ietf_local_uri_null(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "plan",
            str(src),
            "--doc-id",
            "TESTDOC",
            "--corpus",
            "ietf",
            "--json",
            "--provider",
            "mock",
        ],
    )
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["corpus"] == "ietf"
    assert data["doc_id"] == "draft-TESTDOC"
    assert data["source_uri"] is None
    assert data["source_path"] == str(src)


def test_cli_scan_unidentified_local(tmp_path: Path):
    src = tmp_path / "doc.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out_prefix = tmp_path / "out" / "TESTDOC"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--doc-id",
            "TESTDOC",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--output",
            str(out_prefix),
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((tmp_path / "out" / "TESTDOC.json").read_text(encoding="utf-8"))
    assert report["document"]["corpus"] == "generic"
    assert report["document"]["doc_id"] == "TESTDOC"
    assert report["document"]["source_uri"] is None
    assert report["document"]["source_path"] == str(src)
    md = (tmp_path / "out" / "TESTDOC.md").read_text(encoding="utf-8")
    assert "**Source path:**" in md
    assert "**Source URI:**" not in md
    assert "ietf.org" not in md
