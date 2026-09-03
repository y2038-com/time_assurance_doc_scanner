# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""ECMA International standards adapter (Tier 2, curated remote fetch)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

_PORTAL = (
    "https://www.ecma-international.org/publications-and-standards/standards/"
)

# ECMA-404 / ECMA-262 public permalinks (curated; grow as the bench expands).
@dataclass(frozen=True)
class _EcmaEntry:
    doc_id: str
    source_uri: str
    media_type: str
    title: str
    version_note: str
    scan_hint: str = ""


_CATALOG: dict[str, _EcmaEntry] = {
    "ECMA-404": _EcmaEntry(
        doc_id="ECMA-404",
        source_uri=(
            "https://ecma-international.org/wp-content/uploads/ECMA-404.pdf"
        ),
        media_type="application/pdf",
        title="The JSON Data Interchange Syntax (2nd edition, December 2017)",
        version_note=(
            "Pinned to ECMA-404 2nd edition PDF "
            "(https://ecma-international.org/wp-content/uploads/ECMA-404.pdf)."
        ),
        scan_hint="Intentional negative control for time-assurance scope.",
    ),
    "ECMA-262": _EcmaEntry(
        doc_id="ECMA-262",
        source_uri=(
            "https://ecma-international.org/wp-content/uploads/"
            "ECMA-262_17th_edition_june_2026.pdf"
        ),
        media_type="application/pdf",
        title="ECMAScript Language Specification (17th edition / June 2026)",
        version_note=(
            "Pinned to ECMA-262 17th edition PDF "
            "(https://ecma-international.org/wp-content/uploads/"
            "ECMA-262_17th_edition_june_2026.pdf). "
            "For living HTML drafts use tads convert https://tc39.es/ecma262/ "
            "— and prefer --max-sections / token caps on scan."
        ),
        scan_hint="Large PDF; use analysis caps for routine scans.",
    ),
}

_ALIASES: dict[str, str] = {
    "ecma-404": "ECMA-404",
    "ecma404": "ECMA-404",
    "404": "ECMA-404",
    "json": "ECMA-404",
    "ecma-262": "ECMA-262",
    "ecma262": "ECMA-262",
    "262": "ECMA-262",
    "ecmascript": "ECMA-262",
    "es2026": "ECMA-262",
}

_ECMA_NUM = re.compile(
    r"^(?:ecma[-\s]?)?(?P<num>\d{3,4})$",
    re.IGNORECASE,
)
_DIRECT_URL = re.compile(r"^https?://", re.IGNORECASE)


class EcmaAdapter(CorpusAdapter):
    """
    ECMA International adapter with curated remote fetch.

    v1 resolves a small catalog (ECMA-404, ECMA-262). Unknown numbers fail
    loudly with a list of supported ids.
    """

    corpus_id = "ecma"
    display_name = "ECMA International"
    tier = 2
    supports_remote_fetch = True

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {
            "ecma",
            "ecma-international",
            "ecmainternational",
        }

    def normalize_id(self, raw_id: str) -> str:
        return resolve_ecma_doc_id(raw_id)

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = resolve_ecma_doc_id(raw_id)
        # Pass-through: user supplied a direct URL (escape hatch).
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
                    "kind": "ecma-standard",
                    "fetch": "remote",
                    "version_policy": "user-url",
                    "version_note": f"Using caller-supplied URL: {uri}",
                    "portal": _PORTAL,
                },
            )

        entry = _CATALOG[doc_id]
        meta = {
            "kind": "ecma-standard",
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
                    "ECMA Standards with numbered clauses, annexes, and (for language "
                    "specs) algorithms; often dual-published with ISO/IEC"
                ),
                "clause_organization": "Numbered clauses/sections and annexes when present",
                "normative_language": (
                    "shall/should/may; language specs also use algorithmic prose"
                ),
                "references": (
                    "Normative references; frequent cross-links to ISO/IEC and IETF"
                ),
                "versioning": "ECMA-NNN (+ curated edition URL in fetch catalog)",
                "editorial_style": (
                    "ECMA Standard template; ECMA-262/JS uses HTML/algorithm style"
                ),
                "fetch": (
                    f"Curated remote fetch for: {known}. "
                    "PDF/HTML converted to plain text via ingest."
                ),
                "portal": _PORTAL,
                "status": "fetch-enabled",
            }
        )
        return base


def resolve_ecma_doc_id(raw_id: str) -> str:
    """Normalize to a catalog key such as ``ECMA-404``."""
    raw = re.sub(r"\s+", " ", raw_id.strip())
    if not raw:
        raise ValueError("Empty ECMA document id")

    if _DIRECT_URL.match(raw):
        # Prefer matching known hosted files; else keep a stable label.
        lower = raw.lower()
        if "ecma-404" in lower or lower.rstrip("/").endswith("/ecma-404.pdf"):
            return "ECMA-404"
        if (
            "262.ecma-international.org" in lower
            or "ecma-262" in lower
            or "ecma262" in lower
        ):
            return "ECMA-262"
        return "ECMA-URL"

    lower = raw.lower()
    if lower.startswith("ecma "):
        lower = lower[5:].strip()

    if lower in _ALIASES:
        return _ALIASES[lower]

    match = _ECMA_NUM.match(lower.replace(" ", ""))
    if match:
        key = f"ECMA-{int(match.group('num'))}"
        if key in _CATALOG:
            return key
        known = ", ".join(sorted(_CATALOG))
        raise ValueError(
            f"ECMA standard {key} is not in the fetch catalog yet. "
            f"Supported: {known}. "
            f"Use `tads convert <url>` with a direct PDF/HTML link, or extend "
            f"the curated map in tads.corpus.ecma."
        )

    # ECMA-404 style already normalized
    upper = raw.upper().replace(" ", "")
    if upper.startswith("ECMA-") and upper in _CATALOG:
        return upper
    if upper in {k.replace("-", "") for k in _CATALOG}:
        for k in _CATALOG:
            if k.replace("-", "") == upper:
                return k

    known = ", ".join(sorted(_CATALOG))
    raise ValueError(
        f"Unrecognized ECMA id {raw_id!r}. "
        f"Use one of: {known} (or aliases like json, ecmascript)."
    )


def curated_ecma_ids() -> list[str]:
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
