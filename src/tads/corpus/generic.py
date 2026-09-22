# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Neutral analysis profile for unidentified local or remote documents."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.document import ParsedDocument
from tads.parsing.sections import section_plain_text_rfc

_TITLE_FIELD = re.compile(r"^title:\s*(.+)$", re.IGNORECASE)
_MARKDOWN_H1 = re.compile(r"^#\s+(.+?)\s*$")


class GenericAdapter(CorpusAdapter):
    """Preserve caller identity. Never synthesize a retrieval URL."""

    corpus_id = "generic"
    display_name = "Generic document"
    tier = 2
    supports_remote_fetch = False

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() == self.corpus_id

    def normalize_id(self, raw_id: str) -> str:
        return raw_id.strip()

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=self.normalize_id(raw_id),
            source_uri=None,
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        return build_parsed_document(
            text,
            ref,
            sections=section_plain_text_rfc(text),
            title=extract_generic_title(text),
        )

    def describe(self) -> dict[str, str]:
        base = super().describe()
        base.update(
            {
                "structure": (
                    "Numbered headings when present; otherwise a single body section"
                ),
                "clause_organization": "No SDO-specific clause or annex conventions",
                "normative_language": "No assumed RFC 2119 or SDO keyword profile",
                "references": "Not specialized",
                "versioning": "Caller-supplied document id is preserved",
                "editorial_style": "Neutral; no IETF or SDO boilerplate assumed",
                "fetch": "Remote corpus fetch is not supported",
                "title_extraction": (
                    "Only an explicit Title: field or a Markdown level-one heading"
                ),
            }
        )
        return base


def extract_generic_title(text: str) -> Optional[str]:
    """Return a title only from explicit Title: or Markdown `#` headings."""
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if not stripped:
            continue
        field = _TITLE_FIELD.match(stripped)
        if field:
            value = field.group(1).strip()
            return value or None
        heading = _MARKDOWN_H1.match(stripped)
        if heading:
            value = heading.group(1).strip()
            return value or None
    return None
