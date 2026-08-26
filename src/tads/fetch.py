# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Fetch public documents when a corpus adapter supports remote retrieval."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from tads.corpus.base import CorpusDocumentRef
from tads.corpus.registry import get_adapter
from tads.ingest import IngestError, IngestOptions, ingest_to_text
from tads.ingest.pipeline import default_max_download_bytes


class FetchNotSupportedError(RuntimeError):
    """Raised when a corpus cannot auto-download a document."""


class FetchResolveError(RuntimeError):
    """Raised when resolve() does not yield a direct document URL."""


# Paths that look like SDO portals/search hubs, not documents.
_PORTAL_PATH_MARKERS = (
    "standards-search",
    "standards.html",
    "/publications",
    "specifications-by-series",
    "publications-and-standards/standards",
)


def looks_like_direct_document_uri(uri: str) -> bool:
    """
    Return True if URI plausibly points at a document payload.

    Accepts file extensions, W3C /TR/<shortname>/ pages, and other deep paths.
    Rejects bare hosts and known search/portal endpoints.
    """
    parsed = urlparse(uri.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    path = parsed.path or "/"
    lower = path.lower().rstrip("/")
    if lower in {"", "/"}:
        return False
    for marker in _PORTAL_PATH_MARKERS:
        if marker in lower:
            return False
    # Extension strongly indicates a document.
    name = lower.rsplit("/", 1)[-1]
    if "." in name and not name.startswith("."):
        ext = name.rsplit(".", 1)[-1]
        if ext in {
            "txt",
            "text",
            "md",
            "pdf",
            "html",
            "htm",
            "docx",
            "zip",
            "tgz",
            "gz",
        }:
            return True
    # W3C TR shortname pages (often directory URLs without extension).
    if "/tr/" in lower and lower.count("/") >= 2:
        return True
    # Deep path with at least one meaningful segment beyond root.
    segments = [s for s in lower.split("/") if s]
    return len(segments) >= 2


def assert_direct_document_uri(uri: str) -> None:
    if not looks_like_direct_document_uri(uri):
        raise FetchResolveError(
            f"Resolved URI looks like a portal/search page, not a document: {uri}. "
            "Use a direct document URL with `tads convert`, or provide a local file."
        )


def _ref_allows_html(ref: CorpusDocumentRef) -> bool:
    media = (ref.media_type or "").lower()
    if "html" in media:
        return True
    uri = (ref.source_uri or "").lower()
    return "/tr/" in uri or uri.rstrip("/").endswith((".html", ".htm"))


def fetch_text(
    doc_id: str,
    *,
    corpus: str = "ietf",
    timeout: float = 60.0,
    max_download_bytes: int | None = None,
) -> tuple[str, str]:
    """
    Fetch document text for corpora that support remote retrieval.

    Downloads via the ingest pipeline (plain text, PDF, HTML, zip→member).
    Returns (text, source_uri).
    """
    adapter = get_adapter(corpus)
    try:
        ref = adapter.resolve(doc_id)
    except ValueError as exc:
        raise FetchResolveError(str(exc)) from exc
    if not adapter.supports_remote_fetch:
        portal = (ref.metadata or {}).get("portal") or ref.source_uri or "(none)"
        raise FetchNotSupportedError(
            f"Corpus '{adapter.corpus_id}' does not support remote auto-fetch yet. "
            f"Download/extract plain text manually (portal: {portal}) and pass a local file "
            f"with --corpus {adapter.corpus_id}."
        )
    if not ref.source_uri:
        raise FetchResolveError(f"No source URI for {doc_id} in corpus {corpus}")
    assert_direct_document_uri(ref.source_uri)

    max_bytes = max_download_bytes or default_max_download_bytes()
    try:
        result = ingest_to_text(
            ref.source_uri,
            options=IngestOptions(
                max_download_bytes=max_bytes,
                timeout_seconds=timeout,
                allow_html=_ref_allows_html(ref),
            ),
        )
    except IngestError as exc:
        raise FetchResolveError(
            f"Failed to fetch/convert {ref.source_uri}: {exc}"
        ) from exc
    return result.text, result.source


def fetch_to_path(
    doc_id: str,
    path: Path,
    *,
    corpus: str = "ietf",
    timeout: float = 60.0,
    max_download_bytes: int | None = None,
) -> str:
    """
    Fetch a corpus document and write UTF-8 plain text to ``path``.

    Non-text sources (PDF/HTML/zip) are converted via ingest before writing.
    """
    adapter = get_adapter(corpus)
    try:
        ref = adapter.resolve(doc_id)
    except ValueError as exc:
        raise FetchResolveError(str(exc)) from exc
    if not adapter.supports_remote_fetch:
        portal = (ref.metadata or {}).get("portal") or ref.source_uri or "(none)"
        raise FetchNotSupportedError(
            f"Corpus '{adapter.corpus_id}' does not support remote auto-fetch yet. "
            f"Download/extract plain text manually (portal: {portal}) and pass a local file "
            f"with --corpus {adapter.corpus_id}."
        )
    if not ref.source_uri:
        raise FetchResolveError(f"No source URI for {doc_id} in corpus {corpus}")
    assert_direct_document_uri(ref.source_uri)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max_download_bytes or default_max_download_bytes()
    try:
        result = ingest_to_text(
            ref.source_uri,
            options=IngestOptions(
                max_download_bytes=max_bytes,
                timeout_seconds=timeout,
                allow_html=_ref_allows_html(ref),
                save_text_path=str(path),
            ),
        )
    except IngestError as exc:
        raise FetchResolveError(
            f"Failed to fetch/convert {ref.source_uri}: {exc}"
        ) from exc
    # ingest may rewrite the save path if given a directory; prefer explicit path.
    if result.saved_text_path and Path(result.saved_text_path) != path:
        path.write_text(result.text, encoding="utf-8")
    elif not path.exists():
        path.write_text(result.text, encoding="utf-8")
    return str(path)


# Back-compat aliases
def fetch_ietf_text(doc_id: str, *, timeout: float = 60.0) -> tuple[str, str]:
    return fetch_text(doc_id, corpus="ietf", timeout=timeout)
