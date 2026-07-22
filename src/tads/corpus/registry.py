"""Corpus adapter registry."""

from tads.corpus.base import CorpusAdapter
from tads.corpus.ietf import IETFAdapter

_ADAPTERS: dict[str, CorpusAdapter] = {
    "ietf": IETFAdapter(),
}


def get_adapter(corpus_id: str) -> CorpusAdapter:
    key = corpus_id.lower().strip()
    aliases = {"rfc": "ietf", "internet-draft": "ietf", "i-d": "ietf"}
    key = aliases.get(key, key)
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        known = ", ".join(sorted(_ADAPTERS))
        raise KeyError(f"Unknown corpus '{corpus_id}'. Known: {known}") from exc


def list_corpora() -> list[str]:
    return sorted(_ADAPTERS)
