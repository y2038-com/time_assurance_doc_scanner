# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""IETF RFC / Internet-Draft corpus adapter (MVP priority)."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.sections import section_plain_text_rfc


_RFC_ID = re.compile(r"^(?:RFC)?\s*(\d+)$", re.IGNORECASE)
_DRAFT_ID = re.compile(r"^(?:draft-)?(.+)$", re.IGNORECASE)


class IETFAdapter(CorpusAdapter):
    """Adapter for IETF RFCs and Internet-Drafts."""

    corpus_id = "ietf"
    display_name = "IETF RFC / Internet-Draft"
    tier = 1
    supports_remote_fetch = True

    RFC_TEXT_URI = "https://www.rfc-editor.org/rfc/rfc{number}.txt"
    DRAFT_TEXT_URI = "https://www.ietf.org/archive/id/{name}.txt"

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"ietf", "rfc", "internet-draft", "i-d"}

    def normalize_id(self, raw_id: str) -> str:
        raw = raw_id.strip()
        rfc = _RFC_ID.match(raw)
        if rfc:
            return f"RFC{int(rfc.group(1))}"
        lower = raw.lower()
        if lower.startswith("draft-"):
            return lower
        if "." in lower or lower.startswith("draft"):
            return lower if lower.startswith("draft-") else f"draft-{lower}"
        return raw

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = self.normalize_id(raw_id)
        if doc_id.upper().startswith("RFC"):
            number = int(doc_id[3:])
            return CorpusDocumentRef(
                corpus=self.corpus_id,
                doc_id=f"RFC{number}",
                source_uri=self.RFC_TEXT_URI.format(number=number),
                media_type="text/plain",
                metadata={"kind": "rfc", "number": str(number)},
            )
        name = doc_id if doc_id.startswith("draft-") else f"draft-{doc_id}"
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=name,
            source_uri=self.DRAFT_TEXT_URI.format(name=name),
            media_type="text/plain",
            metadata={"kind": "internet-draft"},
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        sections = section_plain_text_rfc(text)
        title = _extract_title(text)
        return ParsedDocument(
            corpus=ref.corpus,
            doc_id=ref.doc_id,
            title=title,
            source_uri=ref.source_uri,
            source_path=ref.source_path,
            media_type=ref.media_type or "text/plain",
            text=text,
            sections=sections,
            metadata=dict(ref.metadata),
        )

    def describe(self) -> dict[str, str]:
        base = super().describe()
        base.update(
            {
                "structure": "RFC / I-D sections with numeric headings",
                "clause_organization": "Numbered sections (1, 1.1, …); appendices",
                "normative_language": "MUST/SHOULD/MAY (RFC 2119/8174)",
                "references": "Normative and Informative reference sections",
                "versioning": "RFC numbers immutable; I-Ds revise by name/rev",
                "editorial_style": "RFC Editor / Internet-Draft boilerplate and style",
                "fetch": "Plain-text auto-fetch from rfc-editor.org / ietf.org",
            }
        )
        return base


def _extract_title(text: str) -> Optional[str]:
    lines = text.splitlines()
    for line in lines[:120]:
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip() or None

    # Prefer the non-empty line immediately before Abstract / Status of This Memo.
    for idx, line in enumerate(lines[:200]):
        marker = line.strip().lower()
        if marker in {"abstract", "status of this memo", "status of memo"}:
            for back in range(idx - 1, max(-1, idx - 8), -1):
                candidate = lines[back].strip()
                if _looks_like_rfc_title(candidate):
                    return candidate

    # Fallback: first plausible title-like line after the RFC header block.
    for line in lines[:80]:
        stripped = line.strip()
        if _looks_like_rfc_title(stripped):
            return stripped
    return None


def _looks_like_rfc_title(line: str) -> bool:
    if len(line) < 12 or len(line) > 200:
        return False
    lower = line.lower()
    banned_prefixes = (
        "rfc ",
        "internet-draft",
        "internet engineering task force",
        "request for comments",
        "category:",
        "issn:",
        "updates:",
        "obsoletes:",
        "network working group",
    )
    if lower.startswith(banned_prefixes):
        return False
    if ":" in line and line.split(":", 1)[0].isupper():
        return False
    return any(c.isalpha() for c in line)
