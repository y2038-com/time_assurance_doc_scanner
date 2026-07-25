# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Corpus adapter interfaces."""

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import (
    detect_corpus,
    get_adapter,
    list_corpora,
    list_corpus_profiles,
)

__all__ = [
    "CorpusAdapter",
    "CorpusDocumentRef",
    "detect_corpus",
    "get_adapter",
    "list_corpora",
    "list_corpus_profiles",
]
