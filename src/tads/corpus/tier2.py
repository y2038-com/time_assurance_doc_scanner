# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Tier-2 corpus adapter stubs (structure-aware, local-file first)."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument


class Tier2StubAdapter(CorpusAdapter):
    """
    Generic Tier-2 adapter.

    Provides clause sectionization and corpus metadata for prompts. Remote
    fetch is intentionally unsupported until each SDO's distribution model
    is wired individually.
    """

    tier = 2
    supports_remote_fetch = False

    def __init__(
        self,
        *,
        corpus_id: str,
        display_name: str,
        aliases: Optional[set[str]] = None,
        structure: str,
        normative_language: str,
        references: str,
        versioning: str,
        editorial_style: str,
        portal: str,
    ) -> None:
        self.corpus_id = corpus_id
        self.display_name = display_name
        self._aliases = {corpus_id} | (aliases or set())
        self._structure = structure
        self._normative_language = normative_language
        self._references = references
        self._versioning = versioning
        self._editorial_style = editorial_style
        self._portal = portal

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in self._aliases

    def normalize_id(self, raw_id: str) -> str:
        return re.sub(r"\s+", " ", raw_id.strip())

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = self.normalize_id(raw_id)
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=doc_id,
            source_uri=self._portal,
            media_type="text/plain",
            metadata={"kind": f"{self.corpus_id}-document", "fetch": "local-file"},
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        title = _extract_generic_title(text) or ref.doc_id
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
                "structure": self._structure,
                "clause_organization": "Numbered clauses/sections and annexes when present",
                "normative_language": self._normative_language,
                "references": self._references,
                "versioning": self._versioning,
                "editorial_style": self._editorial_style,
                "fetch": "Remote auto-fetch not supported; provide extracted plain text locally",
                "portal": self._portal,
                "status": "stub",
            }
        )
        return base


def build_tier2_adapters() -> dict[str, CorpusAdapter]:
    specs = [
        Tier2StubAdapter(
            corpus_id="itu-t",
            display_name="ITU-T",
            aliases={"itu", "itu-t"},
            structure="ITU-T Recommendations with clauses and appendices",
            normative_language="shall/should/may (ITU style)",
            references="Normative references to ITU/ISO/IETF documents",
            versioning="Recommendation letter.number + approval date/edition",
            editorial_style="ITU-T recommendation template",
            portal="https://www.itu.int/ITU-T/recommendations/",
        ),
        Tier2StubAdapter(
            corpus_id="ieee",
            display_name="IEEE",
            structure="IEEE standards with clauses, annexes, and bibliography",
            normative_language="shall/should/may; IEEE Standards Style Manual",
            references="Normative and informative references",
            versioning="Std number + year (and amendments/corrigenda)",
            editorial_style="IEEE SA standards template",
            portal="https://standards.ieee.org/",
        ),
        Tier2StubAdapter(
            corpus_id="oasis",
            display_name="OASIS",
            structure="OASIS standards with numbered sections and appendices",
            normative_language="MUST/SHOULD/MAY or shall/should depending on TC",
            references="Normative references section",
            versioning="Work product name + version + stage (CS/COS/OS)",
            editorial_style="OASIS committee specification template",
            portal="https://www.oasis-open.org/standards/",
        ),
        Tier2StubAdapter(
            corpus_id="nist",
            display_name="NIST",
            structure="NIST SP/FIPS sections; guides often less rigid than SDOs",
            normative_language="Mixed; FIPS more normative than SP guidance",
            references="References / bibliography varies by series",
            versioning="Series number + revision (e.g. SP 800-57 Part 1 Rev. 5)",
            editorial_style="NIST publication template by series",
            portal="https://csrc.nist.gov/publications",
        ),
        Tier2StubAdapter(
            corpus_id="iso",
            display_name="ISO/IEC",
            aliases={"iso", "iso/iec", "isoiec"},
            structure="ISO/IEC International Standards with clauses and annexes",
            normative_language="shall/should/may (ISO/IEC Directives)",
            references="Normative references and bibliography",
            versioning="Standard number + year (+ parts/editions)",
            editorial_style="ISO/IEC Directives Part 2 structure",
            portal="https://www.iso.org/standards.html",
        ),
        Tier2StubAdapter(
            corpus_id="ecma",
            display_name="ECMA International",
            aliases={"ecma", "ecma-international", "ecmainternational"},
            structure=(
                "ECMA Standards with numbered clauses, annexes, and (for language "
                "specs) algorithms; often dual-published with ISO/IEC"
            ),
            normative_language="shall/should/may; language specs also use algorithmic prose",
            references="Normative references; frequent cross-links to ISO/IEC and IETF",
            versioning="ECMA-NNN (+ edition); language editions may use yearly names",
            editorial_style="ECMA Standard template; ECMA-262/JS uses HTML/algorithm style",
            portal="https://www.ecma-international.org/publications-and-standards/standards/",
        ),
    ]
    return {a.corpus_id: a for a in specs}


def _extract_generic_title(text: str) -> Optional[str]:
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip() or None
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if 20 <= len(stripped) <= 160 and any(c.isalpha() for c in stripped):
            if not stripped.lower().startswith(("http://", "https://", "copyright")):
                return stripped
    return None
