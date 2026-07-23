"""ETSI deliverable corpus adapter (Tier 1)."""

from __future__ import annotations

import re
from typing import Optional

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.common import build_parsed_document
from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument

# Examples: ETSI TS 103 246-1, EN 302 637-2, ES 201 873-1, GS NFV-SOL 001
_ETSI_ID = re.compile(
    r"^(?:ETSI\s+)?"
    r"(?P<type>TS|TR|EN|ES|EG|GS|GR|SR)\s+"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)


class ETSIAdapter(CorpusAdapter):
    """Adapter for ETSI technical specifications and related deliverables."""

    corpus_id = "etsi"
    display_name = "ETSI"
    tier = 1
    supports_remote_fetch = False

    def matches(self, ref: CorpusDocumentRef) -> bool:
        return ref.corpus.lower() in {"etsi"}

    def normalize_id(self, raw_id: str) -> str:
        raw = re.sub(r"\s+", " ", raw_id.strip())
        match = _ETSI_ID.match(raw)
        if match:
            return f"ETSI {match.group('type').upper()} {match.group('body').strip()}"
        parts = raw.split(" ", 1)
        if len(parts) == 2 and parts[0].upper() in {
            "TS",
            "TR",
            "EN",
            "ES",
            "EG",
            "GS",
            "GR",
            "SR",
        }:
            return f"ETSI {parts[0].upper()} {parts[1]}"
        if raw.upper().startswith("ETSI "):
            return raw
        return f"ETSI {raw}"

    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        doc_id = self.normalize_id(raw_id)
        # ETSI download URLs are version-specific PDFs; users supply local text.
        return CorpusDocumentRef(
            corpus=self.corpus_id,
            doc_id=doc_id,
            source_uri="https://www.etsi.org/standards-search",
            media_type="text/plain",
            metadata={
                "kind": "etsi-deliverable",
                "fetch": "local-file",
                "portal": "https://www.etsi.org/standards-search",
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
                "structure": "Numbered clauses and annexes (ETSI drafting rules)",
                "clause_organization": "Clause x.y.z; Annex A/B (normative/informative)",
                "normative_language": "shall/should/may (ETSI/ISO style); occasional RFC 2119",
                "references": "Normative references and bibliography sections",
                "versioning": "Deliverable type + number + version/date on cover",
                "editorial_style": "ETSI deliverable template; scope/references/definitions front-matter",
                "fetch": "Remote auto-fetch not supported; provide extracted plain text locally",
            }
        )
        return base


def _extract_title(text: str, *, fallback: str) -> Optional[str]:
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("title:"):
            return stripped.split(":", 1)[1].strip() or fallback
        if lower.startswith("etsi ts") or lower.startswith("etsi tr"):
            continue
    # Often the title is a prominent line before "Scope"
    lines = text.splitlines()
    for idx, line in enumerate(lines[:120]):
        if line.strip().lower() in {"scope", "1 scope", "1. scope"}:
            for back in range(idx - 1, max(-1, idx - 10), -1):
                candidate = lines[back].strip()
                if len(candidate) > 15 and not candidate.lower().startswith("etsi"):
                    return candidate
    return fallback
