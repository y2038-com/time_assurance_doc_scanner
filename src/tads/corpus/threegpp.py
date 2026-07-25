# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""3GPP specification corpus adapter (Tier 1)."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

# Examples: TS 23.501, 3GPP TS 33.501, TR 38.901 V18.0.0
_GPP_ID = re.compile(
    r"^(?:3GPP\s+)?"
    r"(?P<type>TS|TR|TSG|SP)\s*"
    r"(?P<num>\d{1,2}\.\d{2,4})"
    r"(?:\s+V?(?P<version>[\d.]+))?"
    r"$",
    re.IGNORECASE,
)


class ThreeGPPAdapter(CorpusAdapter):
    """Adapter for 3GPP Technical Specifications and Reports."""

    corpus_id = "3gpp"
    display_name = "3GPP"
    tier = 1
    supports_remote_fetch = False

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"3gpp", "gpp", "threegpp"}

    def normalize_id(self, raw_id: str) -> str:
        raw = re.sub(r"\s+", " ", raw_id.strip())
        match = _GPP_ID.match(raw)
        if not match:
            # Accept 23501 / 23.501 shorthand
            bare = re.match(r"^(\d{2})(\d{3})$", raw.replace(".", ""))
            if bare:
                return f"3GPP TS {bare.group(1)}.{bare.group(2)}"
            dotted = re.match(r"^(\d{1,2}\.\d{2,4})$", raw)
            if dotted:
                return f"3GPP TS {dotted.group(1)}"
            return raw if raw.upper().startswith("3GPP") else f"3GPP {raw}"
        dtype = match.group("type").upper()
        if dtype in {"TSG", "SP"}:
            dtype = "TS"
        num = match.group("num")
        version = match.group("version")
        base = f"3GPP {dtype} {num}"
        return f"{base} V{version}" if version else base

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = self.normalize_id(raw_id)
        series = _series_from_id(doc_id)
        portal = "https://www.3gpp.org/specifications-technologies/specifications-by-series"
        if series:
            portal = (
                f"https://www.3gpp.org/ftp/Specs/archive/{series}_series/"
            )
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=doc_id,
            source_uri=portal,
            media_type="text/plain",
            metadata={
                "kind": "3gpp-spec",
                "fetch": "local-file",
                "series": series or "",
            },
        )

    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        return build_parsed_document(
            text,
            ref,
            sections=section_plain_text_clauses(text),
            title=_extract_title(text, fallback=ref.doc_id),
        )

    def describe(self) -> dict[str, str]:
        base = super().describe()
        base.update(
            {
                "tier": "1",
                "structure": "Numbered clauses and annexes (3GPP drafting rules)",
                "clause_organization": "Clause x.y.z; Annex A/B; change history often last",
                "normative_language": "shall/should/may; stage-2/stage-3 conventions",
                "references": "Normative references to 3GPP/IETF/ITU specs",
                "versioning": "Spec number + version (e.g. V18.1.0) / release",
                "editorial_style": "3GPP skeleton; definitions/abbreviations/references front-matter",
                "fetch": "Remote auto-fetch not supported; provide extracted plain text locally",
            }
        )
        return base


def _series_from_id(doc_id: str) -> Optional[str]:
    match = re.search(r"\b(\d{1,2})\.\d{2,4}\b", doc_id)
    if not match:
        return None
    return match.group(1)


def _extract_title(text: str, *, fallback: str) -> Optional[str]:
    for line in text.splitlines()[:100]:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("title:"):
            return stripped.split(":", 1)[1].strip() or fallback
        if lower.startswith("technical specification") or lower.startswith(
            "technical report"
        ):
            continue
    lines = text.splitlines()
    for idx, line in enumerate(lines[:150]):
        if re.match(r"^(1|1\.1)\s+scope\b", line.strip(), re.IGNORECASE):
            for back in range(idx - 1, max(-1, idx - 12), -1):
                candidate = lines[back].strip()
                if len(candidate) > 12 and not candidate.lower().startswith("3gpp"):
                    return candidate
    return fallback
