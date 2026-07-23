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
