# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""ECMA adapter and fetch Phase 2 tests."""

from __future__ import annotations

import pytest

from tads.corpus import detect_corpus, get_adapter, list_corpus_profiles
from tads.corpus.ecma import EcmaAdapter, curated_ecma_ids, resolve_ecma_doc_id
from tads.fetch import FetchResolveError, fetch_to_path, looks_like_direct_document_uri


def test_ecma_registered_as_fetch_enabled():
    profiles = {p["corpus_id"]: p for p in list_corpus_profiles()}
    assert profiles["ecma"]["supports_remote_fetch"] == "true"
    assert get_adapter("ecma").supports_remote_fetch is True
    assert "ECMA-404" in curated_ecma_ids()
    assert "ECMA-262" in curated_ecma_ids()


def test_resolve_ecma_doc_id_and_aliases():
    assert resolve_ecma_doc_id("ECMA-404") == "ECMA-404"
    assert resolve_ecma_doc_id("ecma-404") == "ECMA-404"
    assert resolve_ecma_doc_id("json") == "ECMA-404"
    assert resolve_ecma_doc_id("404") == "ECMA-404"
    assert resolve_ecma_doc_id("ECMA-262") == "ECMA-262"
    assert resolve_ecma_doc_id("ecmascript") == "ECMA-262"
    assert resolve_ecma_doc_id("es2026") == "ECMA-262"
    with pytest.raises(ValueError, match="not in the fetch catalog"):
        resolve_ecma_doc_id("ECMA-999")
    with pytest.raises(ValueError):
        resolve_ecma_doc_id("")


def test_ecma_resolve_curated_uris():
    adapter = EcmaAdapter()
    ref404 = adapter.resolve("ECMA-404")
    assert ref404.source_uri.endswith("ECMA-404.pdf")
    assert ref404.media_type == "application/pdf"
    assert ref404.metadata.get("version_policy") == "curated"
    assert looks_like_direct_document_uri(ref404.source_uri)

    ref262 = adapter.resolve("ECMA-262")
    assert "262.ecma-international.org" in (ref262.source_uri or "")
    assert ref262.media_type == "text/html"
    assert looks_like_direct_document_uri(ref262.source_uri)


def test_detect_corpus_ecma():
    assert detect_corpus("ECMA-404") == "ecma"
    assert detect_corpus("ecmascript") == "ecma"


def test_ecma_fetch_to_path_uses_catalog_url(tmp_path, monkeypatch):
    from tads.ingest import IngestOptions, IngestResult

    out = tmp_path / "ECMA-404.txt"
    seen: dict = {}

    def fake_ingest(source: str, *, options: IngestOptions | None = None):
        seen["source"] = source
        out.write_text("The JSON Data Interchange Syntax\n", encoding="utf-8")
        return IngestResult(
            text=out.read_text(encoding="utf-8"),
            source=source,
            media_type="pdf",
            converter="pymupdf",
            saved_text_path=str(out),
        )

    monkeypatch.setattr("tads.fetch.ingest_to_text", fake_ingest)
    written = fetch_to_path("ECMA-404", out, corpus="ecma")
    assert written == str(out)
    assert seen["source"].endswith("ECMA-404.pdf")


def test_ecma_unknown_id_raises(tmp_path):
    with pytest.raises(FetchResolveError, match="catalog"):
        fetch_to_path("ECMA-999", tmp_path / "x.txt", corpus="ecma")
