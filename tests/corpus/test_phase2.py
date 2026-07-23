"""Phase 2 corpus adapter tests."""

from tads.corpus import detect_corpus, get_adapter, list_corpora
from tads.corpus.etsi import ETSIAdapter
from tads.corpus.threegpp import ThreeGPPAdapter
from tads.fetch import FetchNotSupportedError, fetch_text
import pytest

ETSI_SAMPLE = """\
ETSI TS 103 246-1

Sample Time Assurance Spec

1 Scope

This document specifies time behaviour.

2 References

Normative references are listed here.

3 Definitions

timestamp: a representation of time

4 Time representation

4.1 Epoch

The epoch is defined as ...

Annex A (normative): Encoding

Bit layouts go here.
"""

GPP_SAMPLE = """\
3GPP TS 23.501

System architecture for the 5G System

1 Scope

The present document defines ...

4 Architecture model and concepts

4.1 General concepts

Clocks and timers may be used.

Annex A (informative): Change history

History table.
"""


def test_tier1_and_tier2_registered():
    corpora = list_corpora()
    for required in ("ietf", "etsi", "3gpp", "ieee", "iso", "nist", "w3c", "oasis", "itu-t"):
        assert required in corpora


def test_detect_corpus():
    assert detect_corpus("RFC5905") == "ietf"
    assert detect_corpus("draft-ietf-ntp-something-01") == "ietf"
    assert detect_corpus("TS 23.501") == "3gpp"
    assert detect_corpus("3GPP TS 33.501 V18.1.0") == "3gpp"
    assert detect_corpus("ETSI TS 103 246-1") == "etsi"
    assert detect_corpus("EN 302 637-2") == "etsi"


def test_etsi_parse_clauses():
    adapter = ETSIAdapter()
    ref = adapter.resolve("TS 103 246-1")
    assert ref.doc_id == "ETSI TS 103 246-1"
    doc = adapter.parse(ETSI_SAMPLE, ref)
    ids = [s.id for s in doc.sections]
    assert "s-1" in ids
    assert "s-4.1" in ids
    assert any(s.id.lower().startswith("annex") for s in doc.sections)
    profile = adapter.describe()
    assert profile["tier"] == "1"
    assert "shall" in profile["normative_language"].lower() or "should" in profile["normative_language"].lower()


def test_3gpp_parse_clauses():
    adapter = ThreeGPPAdapter()
    ref = adapter.resolve("TS 23.501")
    assert ref.doc_id == "3GPP TS 23.501"
    doc = adapter.parse(GPP_SAMPLE, ref)
    ids = [s.id for s in doc.sections]
    assert "s-4" in ids
    assert "s-4.1" in ids


def test_tier2_stub_parse():
    adapter = get_adapter("ieee")
    ref = adapter.resolve("IEEE 1588-2019")
    doc = adapter.parse(
        "IEEE 1588\n\n1 Overview\n\nClocks synchronize.\n\n2 Normative references\n\nNone.\n",
        ref,
    )
    assert doc.corpus == "ieee"
    assert any(s.id == "s-1" for s in doc.sections)
    assert adapter.describe()["status"] == "stub"


def test_fetch_not_supported_for_etsi():
    with pytest.raises(FetchNotSupportedError):
        fetch_text("ETSI TS 103 246-1", corpus="etsi")
