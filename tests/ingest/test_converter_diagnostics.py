# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Converter and auxiliary diagnostic sanitation tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads import __version__
from tads.cli import app
from tads.ingest.docx_convert import docx_to_text
from tads.ingest.fetch import IngestError
from tads.ingest.pdf_convert import pdf_to_text
from tads.llm.http import diagnostic_endpoint_url
from tads.llm.providers.ollama_provider import OllamaProvider
from tads.prompts import PROMPT_FRAMEWORK_VERSION
from tads.schemas.report import Report

PDF_CANARY = "PYMUPDF_SECRET_path_/tmp/leaked.pdf"
DOCX_CANARY = "PYTHON_DOCX_SECRET_payload"
HOST_USER = "ollama-user"
HOST_SECRET = "OLLAMA_HOST_SECRET_xyz"
HOST_QUERY = "OLLAMA_QUERY_CANARY"


def test_package_prompt_and_schema_versions_unchanged():
    assert __version__ == "0.6.0rc2"
    assert PROMPT_FRAMEWORK_VERSION == "0.8.0"
    assert Report.model_fields["schema_version"].default == "0.3.0"


def test_pdf_failure_uses_controlled_message(monkeypatch):
    class FakeFitz:
        @staticmethod
        def open(*_a, **_k):
            raise RuntimeError(PDF_CANARY)

    monkeypatch.setitem(sys.modules, "fitz", FakeFitz)
    with pytest.raises(IngestError, match="Invalid PDF") as exc_info:
        pdf_to_text(b"%PDF-1.4 leaked")
    err = exc_info.value
    assert PDF_CANARY not in str(err)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_docx_failure_uses_controlled_message(monkeypatch):
    monkeypatch.setattr(
        "tads.ingest.docx_convert.preflight_docx_package", lambda *_a, **_k: None
    )

    class Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError(DOCX_CANARY)

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = Boom
    monkeypatch.setitem(sys.modules, "docx", fake_docx)
    with pytest.raises(IngestError, match="Invalid DOCX") as exc_info:
        docx_to_text(b"not-inspected")
    err = exc_info.value
    assert DOCX_CANARY not in str(err)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_eval_manifest_sanitizes_source_uri(tmp_path: Path):
    manifest = tmp_path / "seed.yaml"
    manifest.write_text(
        (
            "version: '0.1.0'\n"
            "documents:\n"
            "  - doc_id: RFC1\n"
            "    title: Example\n"
            "    rationale: Seed\n"
            "    source_uri: "
            "'https://user:SECRETURI@example.com/rfc.txt?token=LEAKTOKEN#frag'\n"
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["eval-manifest", "--manifest", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "SECRETURI" not in result.output
    assert "LEAKTOKEN" not in result.output
    assert "user:" not in result.output
    assert "example.com/rfc.txt" in result.output
    assert "#" not in result.output.split("example.com/rfc.txt", 1)[-1][:20]


def test_ollama_404_omits_custom_host_credentials(monkeypatch):
    monkeypatch.setenv(
        "OLLAMA_HOST",
        f"https://{HOST_USER}:{HOST_SECRET}@evil.example:11434/llm?api_key={HOST_QUERY}",
    )
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-test-key")
    endpoint = diagnostic_endpoint_url(
        f"https://{HOST_USER}:{HOST_SECRET}@evil.example:11434/llm?api_key={HOST_QUERY}"
    )

    def fake_post_json(url, *, headers, payload, timeout=None, retries=None, provider_id=None):
        _ = (headers, payload, timeout, retries, provider_id)
        raise RuntimeError(
            f"ollama HTTP 404 for {diagnostic_endpoint_url(url)} (non-retryable)"
        )

    monkeypatch.setattr(
        "tads.llm.providers.ollama_provider.post_json", fake_post_json
    )
    provider = OllamaProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.complete(
            [type("M", (), {"role": "user", "content": "hi"})()],
            model="missing-model",
        )
    msg = str(exc_info.value)
    assert HOST_SECRET not in msg
    assert HOST_QUERY not in msg
    assert HOST_USER not in msg
    assert "HTTP 404" in msg
    assert endpoint in msg or "evil.example" in msg
    assert "?" not in msg
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
