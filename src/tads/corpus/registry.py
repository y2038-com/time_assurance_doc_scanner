# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Corpus adapter registry and detection helpers."""

from __future__ import annotations

import re

from tads.corpus.base import CorpusAdapter
from tads.corpus.ecma import EcmaAdapter
from tads.corpus.etsi import ETSIAdapter
from tads.corpus.ietf import IETFAdapter
from tads.corpus.threegpp import ThreeGPPAdapter
from tads.corpus.tier2 import build_tier2_adapters
from tads.corpus.w3c import W3CAdapter

_ADAPTERS: dict[str, CorpusAdapter] = {
    "ietf": IETFAdapter(),
    "etsi": ETSIAdapter(),
    "3gpp": ThreeGPPAdapter(),
    "w3c": W3CAdapter(),
    "ecma": EcmaAdapter(),
}
_ADAPTERS.update(build_tier2_adapters())

_ALIASES = {
    "rfc": "ietf",
    "internet-draft": "ietf",
    "i-d": "ietf",
    "gpp": "3gpp",
    "threegpp": "3gpp",
    "itu": "itu-t",
    "iso/iec": "iso",
    "isoiec": "iso",
    "ecma-international": "ecma",
    "ecmainternational": "ecma",
}


def get_adapter(corpus_id: str) -> CorpusAdapter:
    key = _ALIASES.get(corpus_id.lower().strip(), corpus_id.lower().strip())
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        known = ", ".join(sorted(_ADAPTERS))
        raise KeyError(f"Unknown corpus '{corpus_id}'. Known: {known}") from exc


def list_corpora() -> list[str]:
    return sorted(_ADAPTERS)


def list_corpus_profiles() -> list[dict[str, str]]:
    profiles = []
    for corpus_id in list_corpora():
        adapter = _ADAPTERS[corpus_id]
        profiles.append(
            {
                "corpus_id": adapter.corpus_id,
                "display_name": adapter.display_name,
                "tier": str(adapter.tier),
                "supports_remote_fetch": (
                    "true" if adapter.supports_remote_fetch else "false"
                ),
            }
        )
    return profiles


def detect_corpus(doc_id: str) -> str | None:
    """
    Best-effort corpus detection from a document identifier.

    Returns corpus_id or None if ambiguous.
    """
    raw = doc_id.strip()
    lower = raw.lower()

    if re.match(r"^(?:rfc)?\s*\d+$", lower) or lower.startswith("draft-"):
        return "ietf"
    if lower.startswith("etsi") or re.match(
        r"^(ts|tr|en|es|eg|gs|gr|sr)\s+\d", lower
    ):
        # Prefer ETSI when deliverable-type + spaced number (103 246), not 23.501
        if re.search(r"\b\d{3}\s+\d{3}\b", raw) or lower.startswith("etsi"):
            return "etsi"
        if re.match(r"^(en|es|eg|gs|gr|sr)\b", lower):
            return "etsi"
    if lower.startswith("3gpp") or re.match(r"^(ts|tr)\s*\d{1,2}\.\d{2,4}", lower):
        return "3gpp"
    if lower.startswith("ieee"):
        return "ieee"
    if lower.startswith("itu"):
        return "itu-t"
    if lower.startswith("nist") or lower.startswith("fips") or lower.startswith("sp 800"):
        return "nist"
    if re.match(r"^sp\s*800", lower):
        return "nist"
    if lower.startswith("iso"):
        return "iso"
    if lower.startswith("ecma") or re.match(r"^ecma-\d+", lower):
        return "ecma"
    if lower.startswith("w3c") or "/tr/" in lower:
        return "w3c"
    # Common W3C shortnames used in the eval suite / fetch plan
    if re.match(r"^(hr-time|html|dom|fetch|url|encoding|webidl)(-\d+)?$", lower):
        return "w3c"
    if lower.startswith("oasis") or "openformula" in lower:
        return "oasis"
    return None
