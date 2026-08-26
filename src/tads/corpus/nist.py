# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""NIST SP/FIPS adapter (Tier 2, curated remote fetch)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

_PORTAL = "https://csrc.nist.gov/publications"

# Pin nvlpubs.nist.gov final PDFs — require part/rev when the series is ambiguous.
@dataclass(frozen=True)
class _NistEntry:
    doc_id: str
    source_uri: str
    media_type: str
    title: str
    version_note: str
    scan_hint: str = ""


_CATALOG: dict[str, _NistEntry] = {
    "SP-800-57pt1r5": _NistEntry(
        doc_id="SP-800-57pt1r5",
        source_uri=(
            "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/"
            "NIST.SP.800-57pt1r5.pdf"
        ),
        media_type="application/pdf",
        title=(
            "NIST SP 800-57 Part 1 Rev. 5 — Recommendation for Key Management: "
            "Part 1 – General"
        ),
        version_note=(
            "Pinned to NIST SP 800-57 Part 1 Revision 5 final PDF "
            "(https://nvlpubs.nist.gov/nistpubs/SpecialPublications/"
            "NIST.SP.800-57pt1r5.pdf). "
            "Part/revision required — bare 'SP 800-57' is rejected. "
            "Use tads convert <url> for another revision or draft."
        ),
        scan_hint=(
            "Bench relevance: cryptographic key/certificate lifetimes and "
            "cryptoperiod guidance."
        ),
    ),
    "SP-800-90Ar1": _NistEntry(
        doc_id="SP-800-90Ar1",
        source_uri=(
            "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/"
            "NIST.SP.800-90Ar1.pdf"
        ),
        media_type="application/pdf",
        title=(
            "NIST SP 800-90A Rev. 1 — Recommendation for Random Number "
            "Generation Using Deterministic Random Bit Generators"
        ),
        version_note=(
            "Pinned to NIST SP 800-90A Revision 1 final PDF "
            "(https://nvlpubs.nist.gov/nistpubs/SpecialPublications/"
            "NIST.SP.800-90Ar1.pdf)."
        ),
    ),
    "FIPS-140-3": _NistEntry(
        doc_id="FIPS-140-3",
        source_uri="https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.140-3.pdf",
        media_type="application/pdf",
        title="FIPS 140-3 — Security Requirements for Cryptographic Modules",
        version_note=(
            "Pinned to FIPS 140-3 final PDF "
            "(https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.140-3.pdf)."
        ),
    ),
}

_ALIASES: dict[str, str] = {
    # SP 800-57 Part 1 Rev. 5
    "sp-800-57pt1r5": "SP-800-57pt1r5",
    "sp800-57pt1r5": "SP-800-57pt1r5",
    "nist.sp.800-57pt1r5": "SP-800-57pt1r5",
    "nist sp 800-57 part 1 rev. 5": "SP-800-57pt1r5",
    "nist sp 800-57 part 1 rev 5": "SP-800-57pt1r5",
    "sp 800-57 part 1 rev. 5": "SP-800-57pt1r5",
    "sp 800-57 part 1 rev 5": "SP-800-57pt1r5",
    "sp 800-57 pt 1 r5": "SP-800-57pt1r5",
    "sp 800-57 pt1 r5": "SP-800-57pt1r5",
    "800-57pt1r5": "SP-800-57pt1r5",
    # SP 800-90A Rev. 1
    "sp-800-90ar1": "SP-800-90Ar1",
    "sp800-90ar1": "SP-800-90Ar1",
    "nist.sp.800-90ar1": "SP-800-90Ar1",
    "sp 800-90a rev. 1": "SP-800-90Ar1",
    "sp 800-90a rev 1": "SP-800-90Ar1",
    "sp 800-90a r1": "SP-800-90Ar1",
    # FIPS 140-3
    "fips-140-3": "FIPS-140-3",
    "fips 140-3": "FIPS-140-3",
    "fips140-3": "FIPS-140-3",
    "nist.fips.140-3": "FIPS-140-3",
}

# Ambiguous series ids that need part and/or revision before fetch.
_AMBIGUOUS = re.compile(
    r"^(?:nist\s+)?(?:sp|special\s+publication)\s*800[-\s]*57(?:\s*part)?$",
    re.IGNORECASE,
)
_AMBIGUOUS_COMPACT = re.compile(
    r"^(?:nist[.\s]*)?sp[.\-]?800[.\-]?57$",
    re.IGNORECASE,
)

_DIRECT_URL = re.compile(r"^https?://", re.IGNORECASE)


class NistAdapter(CorpusAdapter):
    """
    NIST adapter with curated remote fetch.

    v1 resolves a small nvlpubs catalog (SP 800-57 Part 1 Rev. 5 smoke target).
    Ambiguous series ids without part/revision fail loudly.
    """

    corpus_id = "nist"
    display_name = "NIST"
    tier = 2
    supports_remote_fetch = True

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"nist"}

    def normalize_id(self, raw_id: str) -> str:
        return resolve_nist_doc_id(raw_id)

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = resolve_nist_doc_id(raw_id)
        if _DIRECT_URL.match(raw_id.strip()):
            uri = raw_id.strip()
            media = (
                "application/pdf"
                if uri.lower().endswith(".pdf")
                else "text/html"
            )
            return CorpusDocumentRef(
                corpus=self.corpus_id,
                doc_id=doc_id,
                source_uri=uri,
                media_type=media,
                metadata={
                    "kind": "nist-publication",
                    "fetch": "remote",
                    "version_policy": "user-url",
                    "version_note": f"Using caller-supplied URL: {uri}",
                    "portal": _PORTAL,
                },
            )

        entry = _CATALOG[doc_id]
        meta = {
            "kind": "nist-publication",
            "fetch": "remote",
            "version_policy": "curated",
            "version_note": entry.version_note,
            "portal": _PORTAL,
            "title_hint": entry.title,
        }
        if entry.scan_hint:
            meta["scan_hint"] = entry.scan_hint
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=entry.doc_id,
            source_uri=entry.source_uri,
            media_type=entry.media_type,
            metadata=meta,
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        title = (
            (ref.metadata or {}).get("title_hint")
            or _extract_title(text)
            or ref.doc_id
        )
        return build_parsed_document(
            text,
            ref,
            sections=section_plain_text_clauses(text),
            title=title,
        )

    def describe(self) -> dict[str, str]:
        known = ", ".join(sorted(_CATALOG))
        base = super().describe()
        base.update(
            {
                "tier": "2",
                "structure": (
                    "NIST SP/FIPS sections; guides often less rigid than SDOs"
                ),
                "clause_organization": (
                    "Numbered clauses/sections and annexes when present"
                ),
                "normative_language": (
                    "Mixed; FIPS more normative than SP guidance"
                ),
                "references": "References / bibliography varies by series",
                "versioning": (
                    "Series number + revision (e.g. SP 800-57 Part 1 Rev. 5); "
                    "fetch catalog pins final nvlpubs PDFs"
                ),
                "editorial_style": "NIST publication template by series",
                "fetch": (
                    f"Curated remote fetch for: {known}. "
                    "PDF converted to plain text via ingest."
                ),
                "portal": _PORTAL,
                "status": "fetch-enabled",
            }
        )
        return base


def resolve_nist_doc_id(raw_id: str) -> str:
    """Normalize to a catalog key such as ``SP-800-57pt1r5``."""
    raw = re.sub(r"\s+", " ", raw_id.strip())
    if not raw:
        raise ValueError("Empty NIST document id")

    if _DIRECT_URL.match(raw):
        lower = raw.lower()
        if "800-57pt1r5" in lower:
            return "SP-800-57pt1r5"
        if "800-90ar1" in lower:
            return "SP-800-90Ar1"
        if "fips.140-3" in lower or "fips-140-3" in lower or "fips140-3" in lower:
            return "FIPS-140-3"
        return "NIST-URL"

    lower = raw.lower().strip()
    if lower.startswith("nist "):
        lower = lower[5:].strip()

    # Compact punctuation variants → spaced form for alias lookup.
    compact = re.sub(r"[.\u2013]", "-", lower)
    compact = re.sub(r"\s+", " ", compact)

    if lower in _ALIASES:
        return _ALIASES[lower]
    if compact in _ALIASES:
        return _ALIASES[compact]

    for key in _CATALOG:
        if key.lower() == lower or key.lower() == compact:
            return key

    # Ambiguous series: require part + revision.
    bare = re.sub(r"[\s.\-_/]+", "", lower)
    if _AMBIGUOUS.match(lower) or _AMBIGUOUS_COMPACT.match(compact) or bare in {
        "sp80057",
        "nistsp80057",
        "80057",
    }:
        known = ", ".join(sorted(_CATALOG))
        raise ValueError(
            "NIST id 'SP 800-57' is ambiguous without part and revision. "
            "Use e.g. 'SP 800-57 Part 1 Rev. 5' or 'SP-800-57pt1r5'. "
            f"Supported catalog ids: {known}. "
            "Or use `tads convert <url>` with a direct nvlpubs PDF link."
        )

    known = ", ".join(sorted(_CATALOG))
    raise ValueError(
        f"Unrecognized NIST id {raw_id!r}. "
        f"Supported: {known} "
        f"(aliases like 'SP 800-57 Part 1 Rev. 5', 'FIPS 140-3'). "
        f"Use `tads convert <url>` for other publications."
    )


def curated_nist_ids() -> list[str]:
    return sorted(_CATALOG)


def _extract_title(text: str) -> Optional[str]:
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip() or None
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if 12 <= len(stripped) <= 160 and any(c.isalpha() for c in stripped):
            if not stripped.lower().startswith(
                ("http://", "https://", "copyright", "©")
            ):
                return stripped
    return None
