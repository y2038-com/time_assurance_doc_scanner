"""Fetch public IETF plain-text documents."""

from __future__ import annotations

from pathlib import Path

import httpx

from tads.corpus.registry import get_adapter


def fetch_ietf_text(doc_id: str, *, timeout: float = 60.0) -> tuple[str, str]:
    """
    Fetch RFC/I-D plain text.

    Returns (text, source_uri).
    """
    adapter = get_adapter("ietf")
    ref = adapter.resolve(doc_id)
    if not ref.source_uri:
        raise ValueError(f"No source URI for {doc_id}")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(ref.source_uri)
        response.raise_for_status()
        return response.text, ref.source_uri


def fetch_to_path(doc_id: str, path: Path) -> str:
    text, _uri = fetch_ietf_text(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)
