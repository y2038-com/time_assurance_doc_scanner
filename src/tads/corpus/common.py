# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for corpus adapters."""

from __future__ import annotations

from typing import Callable, Optional

from tads.corpus.base import CorpusDocumentRef
from tads.parsing.document import ParsedDocument, Section


def build_parsed_document(
    text: str,
    ref: CorpusDocumentRef,
    *,
    sections: list[Section],
    title: Optional[str],
    extra_metadata: Optional[dict[str, str]] = None,
) -> ParsedDocument:
    metadata = dict(ref.metadata)
    if extra_metadata:
        metadata.update(extra_metadata)
    return ParsedDocument(
        corpus=ref.corpus,
        doc_id=ref.doc_id,
        title=title,
        source_uri=ref.source_uri,
        source_path=ref.source_path,
        media_type=ref.media_type or "text/plain",
        text=text,
        sections=sections,
        metadata=metadata,
    )


TitleExtractor = Callable[[str], Optional[str]]
Sectionizer = Callable[[str], list[Section]]
