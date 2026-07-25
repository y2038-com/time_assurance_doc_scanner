# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Parsed document representation shared across corpora."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Section:
    """A semantic section of a document (not an arbitrary token chunk)."""

    id: str
    title: str
    text: str
    level: int = 1
    start_char: int = 0
    end_char: int = 0
    parent_id: Optional[str] = None

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class ParsedDocument:
    corpus: str
    doc_id: str
    text: str
    title: Optional[str] = None
    source_uri: Optional[str] = None
    source_path: Optional[str] = None
    media_type: Optional[str] = None
    sections: list[Section] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def char_count(self) -> int:
        return len(self.text)
