# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP fetch helpers for ingest."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import httpx

from tads.ingest.detect import detect_media_type
from tads.ingest.url_security import UrlSecurityError, redact_url, validate_remote_url
from tads.ingest.urls import rfc_editor_text_fallback, rewrite_document_url

# Browser-like UA reduces naive bot blocks; still respect robots/ToS of hosts.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; tads-ingest/0.4; "
        "+https://github.com/y2038-com/time_assurance_doc_scanner)"
    ),
    "Accept": "application/pdf,text/plain,*/*",
}

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


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
    retrieved_uri: str | None = None


def fetch_url(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 120.0,
    allow_html: bool = False,
    allow_private_url: bool = False,
) -> FetchedBytes:
    """Download URL contents with a hard size cap."""
    notes: list[str] = []
    fetch_url_resolved, rewrite_note = rewrite_document_url(url)
    if rewrite_note:
        notes.append(
            "Rewrote IETF URL to public RFC Editor text: "
            f"{redact_url(fetch_url_resolved)}"
        )

    try:
        result = _download(
            fetch_url_resolved,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allow_html=allow_html,
            allow_private_url=allow_private_url,
        )
    except IngestError as exc:
        fallback = rfc_editor_text_fallback(fetch_url_resolved)
        if fallback is None or "HTTP 404" not in str(exc):
            raise
        notes.append(
            "RFC Editor PDF not found; falling back to plain text: "
            f"{redact_url(fallback)}"
        )
        result = _download(
            fallback,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allow_html=allow_html,
            allow_private_url=allow_private_url,
        )
        fetch_url_resolved = fallback

    data, final_url, content_type, filename = result
    media_type = detect_media_type(
        name=filename, content_type=content_type, data=data
    )
    if media_type == "html" and not allow_html:
        raise IngestError(
            "URL returned HTML, not a document payload: "
            f"{redact_url(fetch_url_resolved)} "
            f"(content-type={content_type!r}). "
            "For RFCs prefer https://www.rfc-editor.org/rfc/rfcNNNN.txt "
            "(pass a .html URL or enable HTML conversion for intentional HTML docs)."
        )
    if fetch_url_resolved != url:
        notes.append(
            f"Fetched {len(data)} bytes from {redact_url(fetch_url_resolved)}."
        )
    source_uri = redact_url(fetch_url_resolved)
    retrieved_uri = _retrieved_if_distinct(source_uri, final_url)
    return FetchedBytes(
        data=data,
        url=source_uri,
        filename=filename,
        content_type=content_type,
        media_type=media_type,
        notes=notes,
        retrieved_uri=retrieved_uri,
    )


def _retrieved_if_distinct(source_uri: str, final_url: str) -> str | None:
    retrieved = redact_url(final_url)
    if retrieved == source_uri:
        return None
    return retrieved


def _is_https_downgrade(from_url: str, to_url: str) -> bool:
    from_scheme = (urlparse(from_url).scheme or "").lower()
    to_scheme = (urlparse(to_url).scheme or "").lower()
    return from_scheme == "https" and to_scheme == "http"


def _download(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    allow_html: bool = False,
    allow_private_url: bool = False,
) -> tuple[bytes, str, str | None, str]:
    accept = (
        "text/html,application/xhtml+xml,application/pdf,text/plain,*/*"
        if allow_html
        else "application/pdf,text/plain,*/*"
    )
    headers = {**_DEFAULT_HEADERS, "Accept": accept}
    current = url
    redirects = 0
    transport_kind: str | None = None
    try:
        try:
            validate_remote_url(current, allow_private=allow_private_url)
        except UrlSecurityError as exc:
            raise IngestError(str(exc)) from None

        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers=headers,
        ) as client:
            while True:
                with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        current = _next_redirect_url(
                            response,
                            start_url=url,
                            current_url=current,
                            redirects=redirects,
                            allow_private_url=allow_private_url,
                        )
                        redirects += 1
                        continue
                    if 300 <= response.status_code < 400:
                        raise IngestError(
                            f"HTTP {response.status_code} fetching "
                            f"{redact_url(url)} is not a supported redirect."
                        )

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
                    filename = _filename_from_response(current, response)
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise IngestError(
                                f"Download exceeds max size ({max_bytes} bytes): "
                                f"{redact_url(url)}"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), final_url, content_type, filename
    except IngestError:
        raise
    except httpx.HTTPError as exc:
        # Record only the exception type. Raise outside this handler so the
        # httpx object (which often embeds the complete URL) is not retained
        # as __context__ or __cause__.
        transport_kind = type(exc).__name__
    if transport_kind is not None:
        raise IngestError(
            f"Failed to download {redact_url(url)}: {transport_kind}"
        )
    raise IngestError(f"Failed to download {redact_url(url)}")


def _next_redirect_url(
    response: httpx.Response,
    *,
    start_url: str,
    current_url: str,
    redirects: int,
    allow_private_url: bool,
) -> str:
    if redirects >= _MAX_REDIRECTS:
        raise IngestError(
            f"Exceeded maximum of {_MAX_REDIRECTS} redirects "
            f"fetching {redact_url(start_url)}"
        )
    location = response.headers.get("location")
    if not location or not str(location).strip():
        raise IngestError(
            "Redirect response missing Location header "
            f"for {redact_url(current_url)}"
        )
    next_url = None
    try:
        next_url = urljoin(str(response.url), str(location).strip())
    except Exception:
        next_url = None
    if not next_url:
        raise IngestError(
            "Redirect Location is not a usable URL while fetching "
            f"{redact_url(start_url)}"
        )
    if _is_https_downgrade(str(response.url), next_url) or _is_https_downgrade(
        current_url, next_url
    ):
        raise IngestError(
            "Refusing HTTPS to HTTP redirect while fetching "
            f"{redact_url(start_url)}"
        )
    destination_ok = True
    try:
        validate_remote_url(next_url, allow_private=allow_private_url)
    except UrlSecurityError:
        destination_ok = False
    if not destination_ok:
        raise IngestError(
            "Refused redirect destination while fetching "
            f"{redact_url(start_url)}"
        )
    return next_url


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
    safe_fetched = redact_url(fetched)
    safe_final = redact_url(final)
    detail = f"HTTP {status} fetching {safe_fetched}"
    if safe_final != safe_fetched:
        detail += f" (final URL: {safe_final})"
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
