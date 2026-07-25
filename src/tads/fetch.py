# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Fetch public documents when a corpus adapter supports remote retrieval."""

from __future__ import annotations

from pathlib import Path

import httpx

from tads.corpus.registry import get_adapter


class FetchNotSupportedError(RuntimeError):
    """Raised when a corpus cannot auto-download a document."""


def fetch_text(
    doc_id: str,
    *,
    corpus: str = "ietf",
    timeout: float = 60.0,
) -> tuple[str, str]:
    """
    Fetch document text for corpora that support remote retrieval.

    Returns (text, source_uri).
    """
    adapter = get_adapter(corpus)
    ref = adapter.resolve(doc_id)
    if not adapter.supports_remote_fetch:
        portal = (ref.metadata or {}).get("portal") or ref.source_uri or "(none)"
        raise FetchNotSupportedError(
            f"Corpus '{adapter.corpus_id}' does not support remote auto-fetch yet. "
            f"Download/extract plain text manually (portal: {portal}) and pass a local file "
            f"with --corpus {adapter.corpus_id}."
        )
    if not ref.source_uri:
        raise ValueError(f"No source URI for {doc_id} in corpus {corpus}")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(ref.source_uri)
        response.raise_for_status()
        return response.text, ref.source_uri


def fetch_to_path(
    doc_id: str,
    path: Path,
    *,
    corpus: str = "ietf",
) -> str:
    text, _uri = fetch_text(doc_id, corpus=corpus)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


# Back-compat aliases
def fetch_ietf_text(doc_id: str, *, timeout: float = 60.0) -> tuple[str, str]:
    return fetch_text(doc_id, corpus="ietf", timeout=timeout)
