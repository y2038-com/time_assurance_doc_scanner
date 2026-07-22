"""Corpus adapter interfaces."""

from tads.corpus.base import CorpusAdapter, CorpusDocumentRef
from tads.corpus.registry import get_adapter, list_corpora

__all__ = [
    "CorpusAdapter",
    "CorpusDocumentRef",
    "get_adapter",
    "list_corpora",
]
