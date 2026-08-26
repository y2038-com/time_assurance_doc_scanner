# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""W3C adapter and fetch Phase 1 tests."""

from __future__ import annotations

import pytest

from tads.corpus import detect_corpus, get_adapter, list_corpus_profiles
from tads.corpus.w3c import (
    W3CAdapter,
    extract_w3c_shortname,
    w3c_tr_latest_uri,
)
from tads.fetch import FetchResolveError, fetch_to_path


def test_w3c_registered_as_fetch_enabled():
    profiles = {p["corpus_id"]: p for p in list_corpus_profiles()}
    assert profiles["w3c"]["supports_remote_fetch"] == "true"
    assert get_adapter("w3c").supports_remote_fetch is True


def test_extract_w3c_shortname_and_aliases():
    assert extract_w3c_shortname("hr-time-3") == "hr-time-3"
    assert extract_w3c_shortname("HR-TIME-3") == "hr-time-3"
    assert extract_w3c_shortname("W3C hr-time-3") == "hr-time-3"
    assert extract_w3c_shortname("hr-time") == "hr-time-3"
    assert extract_w3c_shortname("high resolution time") == "hr-time-3"
    assert (
        extract_w3c_shortname("https://www.w3.org/TR/hr-time-3/") == "hr-time-3"
    )
    assert (
        extract_w3c_shortname("https://www.w3.org/TR/hr-time-3/Overview.html")
        == "hr-time-3"
    )
    with pytest.raises(ValueError):
        extract_w3c_shortname("")
    with pytest.raises(ValueError):
        extract_w3c_shortname("!!!")


def test_w3c_resolve_latest_tr_uri():
    adapter = W3CAdapter()
    ref = adapter.resolve("hr-time-3")
    assert ref.doc_id == "hr-time-3"
    assert ref.source_uri == "https://www.w3.org/TR/hr-time-3/"
    assert ref.media_type == "text/html"
    assert ref.metadata.get("version_policy") == "latest"
    assert "latest" in (ref.metadata.get("version_note") or "").lower()
    assert w3c_tr_latest_uri("hr-time-3") == ref.source_uri


def test_detect_corpus_w3c():
    assert detect_corpus("hr-time-3") == "w3c"
    assert detect_corpus("https://www.w3.org/TR/hr-time-3/") == "w3c"


def test_w3c_parse_sample():
    adapter = W3CAdapter()
    ref = adapter.resolve("hr-time-3")
    doc = adapter.parse(
        "High Resolution Time\n\n1 Introduction\n\n"
        "This specification defines a monotonic clock.\n\n"
        "2 Time origin\n\nDetails.\n",
        ref,
    )
    assert doc.corpus == "w3c"
    assert any(s.id == "s-1" for s in doc.sections)
    assert "fetch" in adapter.describe()["fetch"].lower()


def test_w3c_fetch_to_path_uses_tr_url(tmp_path, monkeypatch):
    from tads.ingest import IngestOptions, IngestResult

    out = tmp_path / "hr-time-3.txt"
    seen: dict = {}

    def fake_ingest(source: str, *, options: IngestOptions | None = None):
        seen["source"] = source
        seen["allow_html"] = bool(options and options.allow_html)
        out.write_text("High Resolution Time\n\nDOMHighResTimeStamp\n", encoding="utf-8")
        return IngestResult(
            text=out.read_text(encoding="utf-8"),
            source=source,
            media_type="html",
            converter="html-text",
            saved_text_path=str(out),
        )

    monkeypatch.setattr("tads.fetch.ingest_to_text", fake_ingest)
    written = fetch_to_path("hr-time-3", out, corpus="w3c")
    assert written == str(out)
    assert seen["source"] == "https://www.w3.org/TR/hr-time-3/"
    assert seen["allow_html"] is True


def test_w3c_bad_id_raises_resolve_error(tmp_path):
    with pytest.raises(FetchResolveError):
        fetch_to_path("!!!", tmp_path / "x.txt", corpus="w3c")
