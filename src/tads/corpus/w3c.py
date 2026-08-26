# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""W3C Technical Report corpus adapter (Tier 2, remote fetch)."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

# https://www.w3.org/TR/<shortname>/ or …/TR/<shortname>/Overview.html
_TR_URL = re.compile(
    r"^https?://(?:www\.)?w3\.org/TR/(?P<short>[^/\s?#]+)(?:/.*)?$",
    re.IGNORECASE,
)
_SHORTNAME = re.compile(r"^[a-z][a-z0-9.-]*$", re.IGNORECASE)

# Friendly aliases → TR shortname (latest under /TR/<shortname>/).
_ALIASES: dict[str, str] = {
    "hr-time": "hr-time-3",
    "hr-time-3": "hr-time-3",
    "high-resolution-time": "hr-time-3",
    "high resolution time": "hr-time-3",
    "hrt": "hr-time-3",
    "html": "html",
    "dom": "dom",
    "encoding": "encoding",
    "url": "url",
    "fetch": "fetch",
    "webidl": "webidl",
}

_PORTAL = "https://www.w3.org/TR/"


class W3CAdapter(CorpusAdapter):
    """
    W3C TR adapter with remote fetch.

    Resolves shortnames to the latest TR URL ``https://www.w3.org/TR/<shortname>/``.
    Full TR URLs are accepted and normalized to that form.
    """

    corpus_id = "w3c"
    display_name = "W3C"
    tier = 2
    supports_remote_fetch = True

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"w3c"}

    def normalize_id(self, raw_id: str) -> str:
        short = extract_w3c_shortname(raw_id)
        return short

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        short = extract_w3c_shortname(raw_id)
        uri = w3c_tr_latest_uri(short)
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=short,
            source_uri=uri,
            media_type="text/html",
            metadata={
                "kind": "w3c-tr",
                "fetch": "remote",
                "shortname": short,
                "version_policy": "latest",
                "version_note": (
                    f"Using latest W3C TR URL for shortname '{short}' "
                    f"({uri}). Pass a dated TR URL if you need a specific snapshot."
                ),
                "portal": _PORTAL,
            },
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        title = _extract_title(text) or ref.doc_id
        return build_parsed_document(
            text,
            ref,
            sections=section_plain_text_clauses(text),
            title=title,
        )

    def describe(self) -> dict[str, str]:
        base = super().describe()
        base.update(
            {
                "tier": "2",
                "structure": "W3C TR sections with status and conformance prose",
                "clause_organization": "Numbered sections; status / SotD front matter",
                "normative_language": "MUST/SHOULD/MAY (RFC 2119) common in W3C",
                "references": "Normative/informative references; Rec/CR/WD maturity",
                "versioning": "Shortname + latest /TR/<shortname>/ (or dated TR URL)",
                "editorial_style": "W3C pubrules document structure",
                "fetch": (
                    "Remote auto-fetch from www.w3.org/TR/<shortname>/ "
                    "(HTML → plain text); defaults to latest TR"
                ),
                "portal": _PORTAL,
                "status": "fetch-enabled",
            }
        )
        return base


def extract_w3c_shortname(raw_id: str) -> str:
    """
    Normalize user input to a W3C TR shortname.

    Accepts ``hr-time-3``, ``W3C hr-time-3``, or a full ``/TR/…`` URL.
    """
    raw = re.sub(r"\s+", " ", raw_id.strip())
    if not raw:
        raise ValueError("Empty W3C document id")

    url_match = _TR_URL.match(raw)
    if url_match:
        short = url_match.group("short").strip().lower()
        return _apply_alias(short)

    lower = raw.lower()
    if lower.startswith("w3c "):
        lower = lower[4:].strip()
    if lower.startswith("tr/"):
        lower = lower[3:].strip()
    # Drop trailing slash-only noise
    lower = lower.strip("/")

    if lower in _ALIASES:
        return _ALIASES[lower]

    # Spaced titles → hyphenated lookup
    spaced = lower.replace(" ", "-")
    if spaced in _ALIASES:
        return _ALIASES[spaced]

    if _SHORTNAME.match(lower):
        return lower.lower()

    raise ValueError(
        f"Unrecognized W3C TR id {raw_id!r}. "
        f"Use a shortname (e.g. hr-time-3) or a https://www.w3.org/TR/<shortname>/ URL."
    )


def w3c_tr_latest_uri(shortname: str) -> str:
    short = shortname.strip().strip("/")
    if not _SHORTNAME.match(short):
        raise ValueError(f"Invalid W3C TR shortname: {shortname!r}")
    return f"https://www.w3.org/TR/{short.lower()}/"


def _apply_alias(short: str) -> str:
    key = short.lower()
    return _ALIASES.get(key, key)


def _extract_title(text: str) -> Optional[str]:
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip() or None
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if 12 <= len(stripped) <= 160 and any(c.isalpha() for c in stripped):
            if not stripped.lower().startswith(("http://", "https://", "copyright", "w3c")):
                return stripped
    # Fallback: first heading-like line
    for line in text.splitlines()[:30]:
        stripped = line.strip()
        if stripped and not stripped.startswith("<"):
            return stripped[:160]
    return None
