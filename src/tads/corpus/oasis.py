# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""OASIS standards adapter (Tier 2, curated remote fetch)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

_PORTAL = "https://www.oasis-open.org/standards/"

# Pin OASIS Standard (OS) stage PDFs — stages (CS/COS/OS) change; keep URLs explicit.
@dataclass(frozen=True)
class _OasisEntry:
    doc_id: str
    source_uri: str
    media_type: str
    title: str
    version_note: str
    scan_hint: str = ""


_CATALOG: dict[str, _OasisEntry] = {
    "OpenFormula": _OasisEntry(
        doc_id="OpenFormula",
        source_uri=(
            "https://docs.oasis-open.org/office/OpenDocument/v1.4/os/part4-formula/"
            "OpenDocument-v1.4-os-part4-formula.pdf"
        ),
        media_type="application/pdf",
        title=(
            "OpenDocument v1.4 Part 4: Recalculated Formula (OpenFormula) Format "
            "(OASIS Standard)"
        ),
        version_note=(
            "Pinned to OpenDocument v1.4 Part 4 OpenFormula OASIS Standard PDF "
            "(https://docs.oasis-open.org/office/OpenDocument/v1.4/os/part4-formula/"
            "OpenDocument-v1.4-os-part4-formula.pdf). "
            "Use OpenFormula-1.3 for the v1.3 OS PDF, or tads convert <url> for "
            "another stage."
        ),
        scan_hint=(
            "Bench relevance: date/time serials, epoch assumptions, and "
            "interoperability warnings in formula semantics."
        ),
    ),
    "OpenFormula-1.4": _OasisEntry(
        doc_id="OpenFormula-1.4",
        source_uri=(
            "https://docs.oasis-open.org/office/OpenDocument/v1.4/os/part4-formula/"
            "OpenDocument-v1.4-os-part4-formula.pdf"
        ),
        media_type="application/pdf",
        title=(
            "OpenDocument v1.4 Part 4: Recalculated Formula (OpenFormula) Format "
            "(OASIS Standard)"
        ),
        version_note=(
            "Pinned to OpenDocument v1.4 Part 4 OpenFormula OASIS Standard PDF."
        ),
        scan_hint=(
            "Bench relevance: date/time serials, epoch assumptions, and "
            "interoperability warnings in formula semantics."
        ),
    ),
    "OpenFormula-1.3": _OasisEntry(
        doc_id="OpenFormula-1.3",
        source_uri=(
            "https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part4-formula/"
            "OpenDocument-v1.3-os-part4-formula.pdf"
        ),
        media_type="application/pdf",
        title=(
            "OpenDocument v1.3 Part 4: Recalculated Formula (OpenFormula) Format "
            "(OASIS Standard)"
        ),
        version_note=(
            "Pinned to OpenDocument v1.3 Part 4 OpenFormula OASIS Standard PDF "
            "(https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part4-formula/"
            "OpenDocument-v1.3-os-part4-formula.pdf)."
        ),
        scan_hint=(
            "Bench relevance: date/time serials, epoch assumptions, and "
            "interoperability warnings in formula semantics."
        ),
    ),
}

_ALIASES: dict[str, str] = {
    "openformula": "OpenFormula",
    "open-formula": "OpenFormula",
    "open formula": "OpenFormula",
    "odf-openformula": "OpenFormula",
    "odf-formula": "OpenFormula",
    "opendocument-part4-formula": "OpenFormula",
    "opendocument-v1.4-part4-formula": "OpenFormula-1.4",
    "openformula-1.4": "OpenFormula-1.4",
    "openformula-1.3": "OpenFormula-1.3",
    "openformula 1.4": "OpenFormula-1.4",
    "openformula 1.3": "OpenFormula-1.3",
}

_DIRECT_URL = re.compile(r"^https?://", re.IGNORECASE)


class OasisAdapter(CorpusAdapter):
    """
    OASIS adapter with curated remote fetch.

    v1 resolves OpenFormula (ODF Part 4) OS-stage PDFs. Unknown work products
    fail loudly with the supported-id list.
    """

    corpus_id = "oasis"
    display_name = "OASIS"
    tier = 2
    supports_remote_fetch = True

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"oasis", "oasis-open"}

    def normalize_id(self, raw_id: str) -> str:
        return resolve_oasis_doc_id(raw_id)

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = resolve_oasis_doc_id(raw_id)
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
                    "kind": "oasis-standard",
                    "fetch": "remote",
                    "version_policy": "user-url",
                    "version_note": f"Using caller-supplied URL: {uri}",
                    "portal": _PORTAL,
                },
            )

        entry = _CATALOG[doc_id]
        meta = {
            "kind": "oasis-standard",
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
                    "OASIS standards with numbered sections and appendices"
                ),
                "clause_organization": (
                    "Numbered clauses/sections and annexes when present"
                ),
                "normative_language": (
                    "MUST/SHOULD/MAY or shall/should depending on TC"
                ),
                "references": "Normative references section",
                "versioning": (
                    "Work product name + version + stage (CS/COS/OS); "
                    "fetch catalog pins OS-stage URLs"
                ),
                "editorial_style": "OASIS committee specification template",
                "fetch": (
                    f"Curated remote fetch for: {known}. "
                    "PDF/HTML converted to plain text via ingest."
                ),
                "portal": _PORTAL,
                "status": "fetch-enabled",
            }
        )
        return base


def resolve_oasis_doc_id(raw_id: str) -> str:
    """Normalize to a catalog key such as ``OpenFormula``."""
    raw = re.sub(r"\s+", " ", raw_id.strip())
    if not raw:
        raise ValueError("Empty OASIS document id")

    if _DIRECT_URL.match(raw):
        lower = raw.lower()
        if "v1.3" in lower and "part4-formula" in lower:
            return "OpenFormula-1.3"
        if "part4-formula" in lower or "openformula" in lower:
            return "OpenFormula-1.4" if "v1.4" in lower else "OpenFormula"
        return "OASIS-URL"

    lower = raw.lower()
    if lower.startswith("oasis "):
        lower = lower[6:].strip()

    if lower in _ALIASES:
        return _ALIASES[lower]

    # Exact catalog keys (preserve OpenFormula casing).
    for key in _CATALOG:
        if key.lower() == lower:
            return key

    known = ", ".join(sorted(_CATALOG))
    raise ValueError(
        f"Unrecognized OASIS id {raw_id!r}. "
        f"Supported: {known} (aliases: openformula, OpenFormula-1.3). "
        f"Stages change — catalog pins OASIS Standard PDFs; use "
        f"`tads convert <url>` for another stage or work product."
    )


def curated_oasis_ids() -> list[str]:
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
