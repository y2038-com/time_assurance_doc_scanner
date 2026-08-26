# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""OASIS adapter and fetch Phase 3 tests."""

from __future__ import annotations

import pytest

from tads.corpus import detect_corpus, get_adapter, list_corpus_profiles
from tads.corpus.oasis import OasisAdapter, curated_oasis_ids, resolve_oasis_doc_id
from tads.fetch import FetchResolveError, fetch_to_path, looks_like_direct_document_uri


def test_oasis_registered_as_fetch_enabled():
    profiles = {p["corpus_id"]: p for p in list_corpus_profiles()}
    assert profiles["oasis"]["supports_remote_fetch"] == "true"
    assert get_adapter("oasis").supports_remote_fetch is True
    assert "OpenFormula" in curated_oasis_ids()
    assert "OpenFormula-1.3" in curated_oasis_ids()


def test_resolve_oasis_doc_id_and_aliases():
    assert resolve_oasis_doc_id("OpenFormula") == "OpenFormula"
    assert resolve_oasis_doc_id("openformula") == "OpenFormula"
    assert resolve_oasis_doc_id("open formula") == "OpenFormula"
    assert resolve_oasis_doc_id("OpenFormula-1.4") == "OpenFormula-1.4"
    assert resolve_oasis_doc_id("OpenFormula-1.3") == "OpenFormula-1.3"
    assert resolve_oasis_doc_id("odf-formula") == "OpenFormula"
    with pytest.raises(ValueError, match="Supported"):
        resolve_oasis_doc_id("SAML")
    with pytest.raises(ValueError):
        resolve_oasis_doc_id("")


def test_oasis_resolve_curated_uris():
    adapter = OasisAdapter()
    ref = adapter.resolve("OpenFormula")
    assert ref.doc_id == "OpenFormula"
    assert ref.source_uri and ref.source_uri.endswith(
        "OpenDocument-v1.4-os-part4-formula.pdf"
    )
    assert ref.media_type == "application/pdf"
    assert ref.metadata.get("version_policy") == "curated"
    assert looks_like_direct_document_uri(ref.source_uri)

    ref13 = adapter.resolve("OpenFormula-1.3")
    assert "v1.3" in (ref13.source_uri or "")
    assert looks_like_direct_document_uri(ref13.source_uri)


def test_detect_corpus_oasis():
    assert detect_corpus("OpenFormula") == "oasis"
    assert detect_corpus("openformula") == "oasis"


def test_oasis_fetch_to_path_uses_catalog_url(tmp_path, monkeypatch):
    from tads.ingest import IngestOptions, IngestResult

    out = tmp_path / "OpenFormula.txt"
    seen: dict = {}

    def fake_ingest(source: str, *, options: IngestOptions | None = None):
        seen["source"] = source
        out.write_text("OpenFormula date serials and epochs\n", encoding="utf-8")
        return IngestResult(
            text=out.read_text(encoding="utf-8"),
            source=source,
            media_type="pdf",
            converter="pymupdf",
            saved_text_path=str(out),
        )

    monkeypatch.setattr("tads.fetch.ingest_to_text", fake_ingest)
    written = fetch_to_path("OpenFormula", out, corpus="oasis")
    assert written == str(out)
    assert seen["source"].endswith("OpenDocument-v1.4-os-part4-formula.pdf")


def test_oasis_unknown_id_raises(tmp_path):
    with pytest.raises(FetchResolveError, match="Supported|Unrecognized"):
        fetch_to_path("SAML", tmp_path / "x.txt", corpus="oasis")
