# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""NIST adapter and fetch Phase 4 tests."""

from __future__ import annotations

import pytest

from tads.corpus import detect_corpus, get_adapter, list_corpus_profiles
from tads.corpus.nist import NistAdapter, curated_nist_ids, resolve_nist_doc_id
from tads.fetch import FetchResolveError, fetch_to_path, looks_like_direct_document_uri


def test_nist_registered_as_fetch_enabled():
    profiles = {p["corpus_id"]: p for p in list_corpus_profiles()}
    assert profiles["nist"]["supports_remote_fetch"] == "true"
    assert get_adapter("nist").supports_remote_fetch is True
    assert "SP-800-57pt1r5" in curated_nist_ids()
    assert "FIPS-140-3" in curated_nist_ids()


def test_resolve_nist_doc_id_and_aliases():
    assert resolve_nist_doc_id("SP-800-57pt1r5") == "SP-800-57pt1r5"
    assert resolve_nist_doc_id("SP 800-57 Part 1 Rev. 5") == "SP-800-57pt1r5"
    assert resolve_nist_doc_id("sp 800-57 part 1 rev 5") == "SP-800-57pt1r5"
    assert resolve_nist_doc_id("FIPS 140-3") == "FIPS-140-3"
    assert resolve_nist_doc_id("SP 800-90A Rev. 1") == "SP-800-90Ar1"
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_nist_doc_id("SP 800-57")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_nist_doc_id("SP-800-57")
    with pytest.raises(ValueError, match="Supported|Unrecognized"):
        resolve_nist_doc_id("SP 800-999")
    with pytest.raises(ValueError):
        resolve_nist_doc_id("")


def test_nist_resolve_curated_uris():
    adapter = NistAdapter()
    ref = adapter.resolve("SP 800-57 Part 1 Rev. 5")
    assert ref.doc_id == "SP-800-57pt1r5"
    assert ref.source_uri and ref.source_uri.endswith("NIST.SP.800-57pt1r5.pdf")
    assert ref.media_type == "application/pdf"
    assert ref.metadata.get("version_policy") == "curated"
    assert looks_like_direct_document_uri(ref.source_uri)


def test_detect_corpus_nist():
    assert detect_corpus("SP 800-57") == "nist"
    assert detect_corpus("SP-800-57pt1r5") == "nist"
    assert detect_corpus("FIPS 140-3") == "nist"


def test_nist_fetch_to_path_uses_catalog_url(tmp_path, monkeypatch):
    from tads.ingest import IngestOptions, IngestResult

    out = tmp_path / "SP-800-57pt1r5.txt"
    seen: dict = {}

    def fake_ingest(source: str, *, options: IngestOptions | None = None):
        seen["source"] = source
        out.write_text("Key management cryptoperiod guidance\n", encoding="utf-8")
        return IngestResult(
            text=out.read_text(encoding="utf-8"),
            source=source,
            media_type="pdf",
            converter="pymupdf",
            saved_text_path=str(out),
        )

    monkeypatch.setattr("tads.fetch.ingest_to_text", fake_ingest)
    written = fetch_to_path("SP-800-57pt1r5", out, corpus="nist")
    assert written == str(out)
    assert seen["source"].endswith("NIST.SP.800-57pt1r5.pdf")


def test_nist_ambiguous_raises(tmp_path):
    with pytest.raises(FetchResolveError, match="ambiguous"):
        fetch_to_path("SP 800-57", tmp_path / "x.txt", corpus="nist")
