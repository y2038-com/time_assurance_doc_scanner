# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP fetch helpers for ingest."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx

from tads.ingest.detect import detect_media_type
from tads.ingest.urls import rfc_editor_text_fallback, rewrite_document_url

# Browser-like UA reduces naive bot blocks; still respect robots/ToS of hosts.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; tads-ingest/0.4; "
        "+https://github.com/johnlange2/time_assurance_doc_scanner)"
    ),
    "Accept": "application/pdf,text/plain,*/*",
}


class IngestError(RuntimeError):
    """Raised for fetch/convert failures."""


@dataclass
class FetchedBytes:
    data: bytes
    url: str
    filename: str
    content_type: str | None
    media_type: str
    notes: list[str]


def fetch_url(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 120.0,
    allow_html: bool = False,
) -> FetchedBytes:
    """Download URL contents with a hard size cap."""
    notes: list[str] = []
    fetch_url_resolved, rewrite_note = rewrite_document_url(url)
    if rewrite_note:
        notes.append(rewrite_note)

    try:
        result = _download(
            fetch_url_resolved,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allow_html=allow_html,
        )
    except IngestError as exc:
        fallback = rfc_editor_text_fallback(fetch_url_resolved)
        if fallback is None or "HTTP 404" not in str(exc):
            raise
        notes.append(
            f"RFC Editor PDF not found; falling back to plain text: {fallback}"
        )
        result = _download(
            fallback,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allow_html=allow_html,
        )
        fetch_url_resolved = fallback

    data, final_url, content_type, filename = result
    media_type = detect_media_type(
        name=filename, content_type=content_type, data=data
    )
    if media_type == "html" and not allow_html:
        raise IngestError(
            f"URL returned HTML, not a document payload: {fetch_url_resolved} "
            f"(content-type={content_type!r}). "
            "For RFCs prefer https://www.rfc-editor.org/rfc/rfcNNNN.txt "
            "(pass a .html URL or enable HTML conversion for intentional HTML docs)."
        )
    if fetch_url_resolved != url:
        notes.append(f"Fetched {len(data)} bytes from {fetch_url_resolved}.")
    return FetchedBytes(
        data=data,
        url=fetch_url_resolved,
        filename=filename,
        content_type=content_type,
        media_type=media_type,
        notes=notes,
    )


def _download(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    allow_html: bool = False,
) -> tuple[bytes, str, str | None, str]:
    accept = (
        "text/html,application/xhtml+xml,application/pdf,text/plain,*/*"
        if allow_html
        else "application/pdf,text/plain,*/*"
    )
    headers = {**_DEFAULT_HEADERS, "Accept": accept}
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            with client.stream("GET", url) as response:
                final_url = str(response.url)
                if response.status_code >= 400:
                    raise IngestError(
                        _http_error_message(
                            fetched=url,
                            final=final_url,
                            status=response.status_code,
                        )
                    )
                content_type = response.headers.get("content-type")
                filename = _filename_from_response(url, response)
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise IngestError(
                            f"Download exceeds max size ({max_bytes} bytes): {url}"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), final_url, content_type, filename
    except IngestError:
        raise
    except httpx.HTTPError as exc:
        raise IngestError(f"Failed to download {url}: {exc}") from exc


def _http_error_message(
    *,
    fetched: str,
    final: str,
    status: int,
) -> str:
    hint = ""
    if status in {401, 403} or "login" in final.lower():
        hint = (
            " The host redirected to a login/forbidden page. "
            "For IETF RFCs use https://www.rfc-editor.org/rfc/rfcNNNN.txt"
        )
    detail = f"HTTP {status} fetching {fetched}"
    if final != fetched:
        detail += f" (final URL: {final})"
    return detail + "." + hint


def _filename_from_response(url: str, response: httpx.Response) -> str:
    cd = response.headers.get("content-disposition") or ""
    if "filename=" in cd:
        part = cd.split("filename=", 1)[1].strip().strip('"').strip("'")
        if part:
            return unquote(part)
    path = urlparse(str(response.url)).path
    name = unquote(path.rsplit("/", 1)[-1]) if path else ""
    return name or "download.bin"
