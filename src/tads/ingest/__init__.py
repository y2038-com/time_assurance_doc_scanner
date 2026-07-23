"""Document ingest: fetch, archive extract, and convert to plain text."""

from tads.ingest.fetch import IngestError
from tads.ingest.pipeline import default_max_download_bytes, ingest_to_text
from tads.ingest.types import DEFAULT_MAX_DOWNLOAD_BYTES, IngestOptions, IngestResult

__all__ = [
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "IngestError",
    "IngestOptions",
    "IngestResult",
    "default_max_download_bytes",
    "ingest_to_text",
]
