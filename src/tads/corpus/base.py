"""Base corpus adapter abstraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from tads.parsing.document import ParsedDocument


@dataclass(frozen=True)
class CorpusDocumentRef:
    """Reference to a document within a corpus."""

    corpus: str
    doc_id: str
    source_uri: Optional[str] = None
    source_path: Optional[str] = None
    media_type: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)


class CorpusAdapter(ABC):
    """Describes how to identify, fetch, and structure documents for a corpus."""

    corpus_id: str
    display_name: str

    @abstractmethod
    def matches(self, ref: CorpusDocumentRef) -> bool:
        """Return True if this adapter should handle the reference."""

    @abstractmethod
    def normalize_id(self, raw_id: str) -> str:
        """Normalize user input (e.g. 'rfc5905' → 'RFC5905')."""

    @abstractmethod
    def resolve(self, raw_id: str) -> CorpusDocumentRef:
        """Build a document reference from a corpus-specific identifier."""

    @abstractmethod
    def parse(self, text: str, ref: CorpusDocumentRef) -> ParsedDocument:
        """Parse raw text into a structured document with semantic sections."""

    def describe(self) -> dict[str, str]:
        """Human-readable corpus conventions for prompts and docs."""
        return {
            "corpus_id": self.corpus_id,
            "display_name": self.display_name,
        }
