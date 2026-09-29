# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for SSRF-oriented URL security and redirect-safe fetch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tads.ingest.fetch import IngestError, _download, fetch_url
from tads.ingest.url_security import (
    UrlSecurityError,
    is_disallowed_ip,
    redact_url,
    resolve_and_validate_host,
    validate_remote_url,
)


# --- url_security unit tests -------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "2001:4860:4860::8888",
    ],
)
def test_public_ips_allowed(ip: str):
    assert is_disallowed_ip(ip) is False


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.1.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.10.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fd12:3456:789a::1",
        "fe80::1",
        "::",
        "ff02::1",
    ],
)
def test_non_public_ips_disallowed(ip: str):
    assert is_disallowed_ip(ip) is True


def test_validate_public_ipv4_literal():
    validate_remote_url("https://8.8.8.8/doc.pdf")


def test_validate_public_ipv6_literal():
    validate_remote_url("https://[2001:4860:4860::8888]/doc.pdf")


def test_reject_file_scheme():
    with pytest.raises(UrlSecurityError, match="Unsupported URL scheme"):
        validate_remote_url("file:///etc/passwd")


def test_reject_missing_hostname():
    with pytest.raises(UrlSecurityError, match="hostname"):
        validate_remote_url("https:///nohost")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://LOCALHOST/x",
        "http://foo.localhost/x",
        "http://127.0.0.1/x",
        "http://10.1.2.3/x",
        "http://172.16.0.1/x",
        "http://192.168.0.1/x",
        "http://169.254.1.1/x",
        "http://[::1]/x",
        "http://[fc00::1]/x",
        "http://[fe80::1]/x",
    ],
)
def test_reject_private_literals_and_localhost(url: str):
    with pytest.raises(UrlSecurityError, match="non-public|localhost"):
        validate_remote_url(url)


def test_allow_private_permits_localhost_literal():
    validate_remote_url("http://127.0.0.1/doc", allow_private=True)
    validate_remote_url("http://localhost/doc", allow_private=True)


def test_hostname_resolves_public(monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "example.com"
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
        ]

    monkeypatch.setattr(
        "tads.ingest.url_security.socket.getaddrinfo", fake_getaddrinfo
    )
    validate_remote_url("https://example.com/a.txt")


def test_hostname_resolves_private_rejected(monkeypatch: pytest.MonkeyPatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, ("10.0.0.5", 0))]

    monkeypatch.setattr(
        "tads.ingest.url_security.socket.getaddrinfo", fake_getaddrinfo
    )
    with pytest.raises(UrlSecurityError, match="10.0.0.5"):
        validate_remote_url("https://internal.example/a.txt")


def test_hostname_mixed_public_and_private_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("192.168.1.50", 0)),
        ]

    monkeypatch.setattr(
        "tads.ingest.url_security.socket.getaddrinfo", fake_getaddrinfo
    )
    with pytest.raises(UrlSecurityError, match="192.168.1.50"):
        validate_remote_url("https://dual.example/a.txt")


def test_dns_failure_raises(monkeypatch: pytest.MonkeyPatch):
    import socket as socket_mod

    def boom(*args, **kwargs):
        raise socket_mod.gaierror("Name or service not known")

    monkeypatch.setattr("tads.ingest.url_security.socket.getaddrinfo", boom)
    with pytest.raises(UrlSecurityError, match="DNS resolution failed"):
        resolve_and_validate_host("no-such-host.invalid")


def test_dns_failure_still_fails_with_allow_private(
    monkeypatch: pytest.MonkeyPatch,
):
    import socket as socket_mod

    def boom(*args, **kwargs):
        raise socket_mod.gaierror("Name or service not known")

    monkeypatch.setattr("tads.ingest.url_security.socket.getaddrinfo", boom)
    with pytest.raises(UrlSecurityError, match="DNS resolution failed"):
        resolve_and_validate_host("no-such-host.invalid", allow_private=True)


def test_redact_strips_userinfo():
    redacted = redact_url("https://user:secret@example.com/path?q=1")
    assert "secret" not in redacted
    assert "user" not in redacted
    assert "example.com" in redacted
    assert "/path" in redacted
    assert "?" not in redacted
    assert "q=1" not in redacted


# --- fetch redirect / validation integration ---------------------------------


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        url: str = "https://example.com/doc",
        body: bytes = b"",
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = httpx.URL(url)
        self._body = body

    def iter_bytes(self):
        if self._body:
            yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeClient:
    def __init__(self, responses: list[_FakeStreamResponse]):
        self._responses = list(responses)
        self.requests: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method: str, url: str):
        assert method == "GET"
        self.requests.append(url)
        if not self._responses:
            raise AssertionError(f"Unexpected request for {url}")
        return self._responses.pop(0)


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeClient:
        seen.clear()
        seen.update(kwargs)
        return client

    monkeypatch.setattr(
        "tads.ingest.fetch.httpx.Client",
        factory,
    )
    return seen


def _allow_hosts(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[str]]):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host not in mapping:
            raise OSError(f"unexpected host {host}")
        return [
            (None, None, None, None, (ip, 0)) for ip in mapping[host]
        ]

    monkeypatch.setattr(
        "tads.ingest.url_security.socket.getaddrinfo", fake_getaddrinfo
    )


def test_public_to_public_redirect(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=302,
                headers={"location": "https://cdn.example.com/doc.txt"},
                url="https://example.com/doc.txt",
            ),
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                url="https://cdn.example.com/doc.txt",
                body=b"hello public",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    data, final, ctype, _name = _download(
        "https://example.com/doc.txt",
        max_bytes=1000,
        timeout_seconds=5.0,
    )
    assert data == b"hello public"
    assert final == "https://cdn.example.com/doc.txt"
    assert ctype == "text/plain"
    assert client.requests == [
        "https://example.com/doc.txt",
        "https://cdn.example.com/doc.txt",
    ]


def test_redirect_to_private_rejected(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=302,
                headers={"location": "https://127.0.0.1/secret"},
                url="https://example.com/doc.txt",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Refused redirect destination"):
        _download(
            "https://example.com/doc.txt",
            max_bytes=1000,
            timeout_seconds=5.0,
        )


def test_too_many_redirects(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    responses = [
        _FakeStreamResponse(
            status_code=302,
            headers={"location": f"https://example.com/hop{i}"},
            url=f"https://example.com/hop{i - 1}" if i else "https://example.com/start",
        )
        for i in range(6)
    ]
    client = _FakeClient(responses)
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="maximum of 5 redirects"):
        _download(
            "https://example.com/start",
            max_bytes=1000,
            timeout_seconds=5.0,
        )


def test_redirect_missing_location(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=302,
                headers={},
                url="https://example.com/doc.txt",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Location"):
        _download(
            "https://example.com/doc.txt",
            max_bytes=1000,
            timeout_seconds=5.0,
        )


def test_allow_private_url_fetch_localhost(monkeypatch: pytest.MonkeyPatch):
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                url="http://127.0.0.1/local.txt",
                body=b"local doc",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    data, _final, _ctype, _name = _download(
        "http://127.0.0.1/local.txt",
        max_bytes=1000,
        timeout_seconds=5.0,
        allow_private_url=True,
    )
    assert data == b"local doc"


def test_fetch_url_blocks_private_before_http(monkeypatch: pytest.MonkeyPatch):
    # Ensure we never construct a client if validation fails first.
    def boom_client(**kwargs: Any):
        raise AssertionError("httpx.Client should not be called")

    monkeypatch.setattr("tads.ingest.fetch.httpx.Client", boom_client)
    with pytest.raises(IngestError, match="non-public|127.0.0.1"):
        fetch_url("http://127.0.0.1/x", max_bytes=100)


# --- display/persistence sanitizer and leakage --------------------------------


CANARY = "CANARY_SECRET_tads_9f3a7c2e"


def _assert_no_canary(*parts: object) -> None:
    blob = "\n".join(str(p) for p in parts)
    assert CANARY not in blob


def _exception_surfaces(exc: BaseException) -> str:
    import traceback

    parts = [str(exc), repr(exc), "".join(traceback.format_exception(exc))]
    if exc.__cause__ is not None:
        parts.extend([str(exc.__cause__), repr(exc.__cause__)])
    if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
        parts.extend([str(exc.__context__), repr(exc.__context__)])
    return "\n".join(parts)


def _raise_and_surfaces(fn):
    try:
        fn()
    except Exception as exc:
        return exc, _exception_surfaces(exc)
    raise AssertionError("expected an exception")


class _BoomClient:
    def __init__(self, exc: Exception):
        self.requests: list[str] = []
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method: str, url: str):
        self.requests.append(url)
        raise self._exc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            f"https://user:password@example.com:8443/docs/spec.pdf?token={CANARY}&ordinary=value#section",
            "https://example.com:8443/docs/spec.pdf",
        ),
        (
            f"https://example.com/docs/spec.pdf?ordinary=value&other=1",
            "https://example.com/docs/spec.pdf",
        ),
        (
            f"https://example.com/a?token={CANARY}",
            "https://example.com/a",
        ),
        (
            f"https://example.com/a?key={CANARY}",
            "https://example.com/a",
        ),
        (
            f"https://example.com/a?api_key={CANARY}",
            "https://example.com/a",
        ),
        (
            f"https://example.com/a?TOKEN={CANARY}&Api_Key=x",
            "https://example.com/a",
        ),
        (
            f"https://example.com/a?token={CANARY}&token=again",
            "https://example.com/a",
        ),
        (
            "https://example.com/a?=blankname&empty=",
            "https://example.com/a",
        ),
        (
            f"https://example.com/a?token=%73%65%63%72%65%74{CANARY}",
            "https://example.com/a",
        ),
        (
            "https://example.com/obj"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Credential={CANARY}"
            f"&X-Amz-Signature={CANARY}"
            f"&X-Amz-Security-Token={CANARY}",
            "https://example.com/obj",
        ),
        (
            f"https://example.com/blob?sv=2024-11-04&ss=b&srt=sco&sp=r&se=2099-01-01T00:00:00Z&sig={CANARY}",
            "https://example.com/blob",
        ),
        (
            f"https://example.com/a?zzq9f3a7c={CANARY}",
            "https://example.com/a",
        ),
        (
            "https://example.com:8443/docs/spec.pdf",
            "https://example.com:8443/docs/spec.pdf",
        ),
        (
            f"https://[2001:db8::1]:8443/docs/spec.pdf?token={CANARY}",
            "https://[2001:db8::1]:8443/docs/spec.pdf",
        ),
        (
            "https://example.com/docs/spec%20name.pdf",
            "https://example.com/docs/spec%20name.pdf",
        ),
        (
            "https://example.com/docs/spec.pdf",
            "https://example.com/docs/spec.pdf",
        ),
    ],
)
def test_redact_url_drops_secrets_and_preserves_locator(raw: str, expected: str):
    safe = redact_url(raw)
    assert safe == expected
    assert CANARY not in safe
    assert redact_url(safe) == safe


def test_redact_url_hostless_and_malformed_drop_query():
    hostless = f"https:///file.pdf?token={CANARY}#frag"
    safe = redact_url(hostless)
    assert CANARY not in safe
    assert "?" not in safe
    assert "#" not in safe

    unusual = f"https://user:password@/file.pdf?token={CANARY}#frag"
    safe2 = redact_url(unusual)
    assert CANARY not in safe2
    assert "password" not in safe2
    assert "?" not in safe2
    assert "#" not in safe2

    assert redact_url("") == "<unparseable-url>"
    assert CANARY not in redact_url(f"not a url?token={CANARY}")


def test_request_keeps_query_provenance_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
):
    request_url = (
        f"https://example.com/docs/spec.pdf?token={CANARY}&ordinary=value"
    )
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                url=request_url,
                body=b"hello signed",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    result = fetch_url(request_url, max_bytes=1000)
    assert client.requests == [request_url]
    assert result.url == "https://example.com/docs/spec.pdf"
    assert result.retrieved_uri is None
    _assert_no_canary(result.url, result.retrieved_uri, *result.notes)
    assert CANARY not in result.url
    for note in result.notes:
        assert CANARY not in note


def test_allowed_redirect_request_is_complete_display_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
):
    start = "https://example.com/doc.txt"
    dest = f"https://cdn.example.com/doc.txt?token={CANARY}"
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=302,
                headers={"location": dest},
                url=start,
            ),
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                url=dest,
                body=b"hello public",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, dest]
    assert result.url == "https://example.com/doc.txt"
    assert result.retrieved_uri == "https://cdn.example.com/doc.txt"
    _assert_no_canary(result.url, result.retrieved_uri, *result.notes)


def test_rejected_redirect_error_and_traceback_hide_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    dest = f"https://127.0.0.1/secret?token={CANARY}"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=302,
                headers={"location": dest},
                url="https://example.com/doc.txt",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    exc, surfaces = _raise_and_surfaces(
        lambda: fetch_url("https://example.com/doc.txt", max_bytes=1000)
    )
    assert isinstance(exc, IngestError)
    assert CANARY not in _exception_surfaces(exc)
    assert CANARY not in surfaces


def test_timeout_error_hides_request_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    request_url = f"https://example.com/docs/spec.pdf?token={CANARY}"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    boom = _BoomClient(httpx.TimeoutException(f"timed out requesting {request_url}"))
    _patch_client(monkeypatch, boom)
    with caplog.at_level("DEBUG"):
        exc, surfaces = _raise_and_surfaces(
            lambda: fetch_url(request_url, max_bytes=1000)
        )
    assert isinstance(exc, IngestError)
    assert boom.requests == [request_url]
    assert "TimeoutException" in str(exc)
    assert "https://example.com/docs/spec.pdf" in str(exc)
    assert CANARY not in _exception_surfaces(exc)
    assert CANARY not in surfaces
    assert CANARY not in caplog.text
    assert exc.__cause__ is None
    assert exc.__context__ is None


def test_http_error_hides_request_and_final_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    request_url = f"https://example.com/docs/spec.pdf?token={CANARY}"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=404,
                headers={"content-type": "text/plain"},
                url=request_url,
                body=b"missing",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    exc, surfaces = _raise_and_surfaces(
        lambda: fetch_url(request_url, max_bytes=1000)
    )
    assert isinstance(exc, IngestError)
    assert "HTTP 404" in str(exc)
    assert CANARY not in _exception_surfaces(exc)
    assert CANARY not in surfaces


def test_html_rejection_hides_secret(monkeypatch: pytest.MonkeyPatch):
    request_url = f"https://example.com/doc?token={CANARY}"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/html"},
                url=request_url,
                body=b"<html><body>login</body></html>",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    exc, surfaces = _raise_and_surfaces(
        lambda: fetch_url(request_url, max_bytes=1000)
    )
    assert isinstance(exc, IngestError)
    assert "HTML" in str(exc)
    assert CANARY not in _exception_surfaces(exc)
    assert CANARY not in surfaces


def test_ingest_and_report_do_not_leak_query(
    monkeypatch: pytest.MonkeyPatch,
):
    from tads.export import report_to_json, report_to_markdown
    from tads.ingest import IngestOptions, ingest_to_text
    from tads.pipeline import run_scan

    request_url = f"https://example.com/spec.txt?token={CANARY}&ordinary=value"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=200,
                headers={"content-type": "text/plain"},
                url=request_url,
                body=b"1 Intro\n\nTimers may wrap in 2038.\n",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    ingested = ingest_to_text(request_url, options=IngestOptions(max_download_bytes=1000))
    assert client.requests == [request_url]
    assert ingested.source == "https://example.com/spec.txt"
    _assert_no_canary(ingested.source, *ingested.notes)

    assert ingested.source_uri == "https://example.com/spec.txt"
    assert ingested.retrieved_uri is None
    assert ingested.source_path is None
    report = run_scan(
        ingested.text,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
        retrieved_uri=ingested.retrieved_uri,
    )
    dumped = report_to_json(report)
    markdown = report_to_markdown(report)
    _assert_no_canary(
        dumped,
        markdown,
        report.document.source_path or "",
        report.document.source_uri or "",
        report.document.retrieved_uri or "",
    )
    assert report.schema_version == "0.2.0"
    assert report.document.source_uri == "https://example.com/spec.txt"
    assert report.document.retrieved_uri is None
    assert report.document.source_path is None
    assert '"retrieved_uri"' not in dumped
    assert "`https://example.com/spec.txt`" in markdown
    assert "**Source URI:**" in markdown
    assert "**Retrieved URI:**" not in markdown
    assert "**Source path:**" not in markdown


def _plain_ok(url: str, body: bytes = b"hello public") -> _FakeStreamResponse:
    return _FakeStreamResponse(
        status_code=200,
        headers={"content-type": "text/plain"},
        url=url,
        body=body,
    )


def _redirect(from_url: str, location: str, status: int = 302) -> _FakeStreamResponse:
    return _FakeStreamResponse(
        status_code=status,
        headers={"location": location},
        url=from_url,
    )


def test_client_disables_automatic_redirects(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient([_plain_ok("https://example.com/doc.txt")])
    seen = _patch_client(monkeypatch, client)
    fetch_url("https://example.com/doc.txt", max_bytes=1000)
    assert seen.get("follow_redirects") is False


def test_no_redirect_omits_retrieved_uri(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient([_plain_ok("https://example.com/doc.txt")])
    _patch_client(monkeypatch, client)
    result = fetch_url("https://example.com/doc.txt", max_bytes=1000)
    assert client.requests == ["https://example.com/doc.txt"]
    assert result.url == "https://example.com/doc.txt"
    assert result.retrieved_uri is None


def test_query_only_redirect_omits_retrieved_uri(monkeypatch: pytest.MonkeyPatch):
    start = "https://example.com/doc.txt"
    dest = f"https://example.com/doc.txt?token={CANARY}"
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, dest]
    assert result.url == start
    assert result.retrieved_uri is None
    _assert_no_canary(result.url, result.retrieved_uri, *result.notes)


def test_multiple_public_redirects_record_final(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    start = "https://example.com/a"
    mid = "https://example.com/b"
    final = "https://cdn.example.com/c"
    client = _FakeClient(
        [
            _redirect(start, mid, status=301),
            _redirect(mid, final, status=308),
            _plain_ok(final),
        ]
    )
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, mid, final]
    assert result.url == start
    assert result.retrieved_uri == final


def test_relative_and_scheme_relative_redirects(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    start = "https://example.com/dir/doc.txt"
    client = _FakeClient(
        [
            _redirect(start, "/other.txt"),
            _plain_ok("https://example.com/other.txt"),
        ]
    )
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, "https://example.com/other.txt"]
    assert result.retrieved_uri == "https://example.com/other.txt"

    start2 = "https://example.com/doc.txt"
    client2 = _FakeClient(
        [
            _redirect(start2, "//cdn.example.com/doc.txt"),
            _plain_ok("https://cdn.example.com/doc.txt"),
        ]
    )
    _patch_client(monkeypatch, client2)
    result2 = fetch_url(start2, max_bytes=1000)
    assert client2.requests == [start2, "https://cdn.example.com/doc.txt"]
    assert result2.retrieved_uri == "https://cdn.example.com/doc.txt"


def test_cross_host_port_redirect(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    start = "https://example.com/doc.txt"
    final = "https://cdn.example.com:8443/doc.txt"
    client = _FakeClient([_redirect(start, final), _plain_ok(final)])
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, final]
    assert result.url == start
    assert result.retrieved_uri == final


def test_http_to_https_redirect_allowed(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "http://example.com/doc.txt"
    final = "https://example.com/doc.txt"
    client = _FakeClient([_redirect(start, final, status=307), _plain_ok(final)])
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000)
    assert client.requests == [start, final]
    assert result.url == start
    assert result.retrieved_uri == final


def test_https_to_http_rejected_before_next_get(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    start = "https://example.com/doc.txt"
    dest = "http://cdn.example.com/doc.txt"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="HTTPS to HTTP"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_http_https_http_rejected_at_downgrade(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    http_url = "http://example.com/doc.txt"
    https_url = "https://example.com/doc.txt"
    down = "http://cdn.example.com/doc.txt"
    client = _FakeClient(
        [
            _redirect(http_url, https_url),
            _redirect(https_url, down),
            _plain_ok(down),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="HTTPS to HTTP"):
        fetch_url(http_url, max_bytes=1000)
    assert client.requests == [http_url, https_url]


def test_https_downgrade_not_bypassed_by_allow_private(
    monkeypatch: pytest.MonkeyPatch,
):
    start = "https://127.0.0.1/doc.txt"
    dest = "http://127.0.0.1/doc.txt"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="HTTPS to HTTP"):
        fetch_url(start, max_bytes=1000, allow_private_url=True)
    assert client.requests == [start]


def test_redirect_to_private_ipv6_rejected(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient(
        [_redirect(start, "https://[::1]/secret"), _plain_ok("https://[::1]/secret")]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Refused redirect destination"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_redirect_to_private_ipv4_rejected(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    dest = "https://10.0.0.5/secret"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Refused redirect destination"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_http_to_http_private_ipv4_rejected(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "http://example.com/doc.txt"
    dest = "http://192.168.1.50/secret"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Refused redirect destination"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_redirect_hostname_resolves_private(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "internal.example": ["10.0.0.5"],
        },
    )
    start = "https://example.com/doc.txt"
    dest = "https://internal.example/secret"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="Refused redirect destination"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_allow_private_url_allows_private_redirect(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    dest = "https://127.0.0.1/local.txt"
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000, allow_private_url=True)
    assert client.requests == [start, dest]
    assert result.retrieved_uri == dest


def test_malformed_blank_and_unsupported_location(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"

    blank = _FakeClient([_redirect(start, "   ")])
    _patch_client(monkeypatch, blank)
    with pytest.raises(IngestError, match="Location"):
        fetch_url(start, max_bytes=1000)
    assert blank.requests == [start]

    file_loc = _FakeClient(
        [_redirect(start, "file:///etc/passwd"), _plain_ok("https://example.com/x")]
    )
    _patch_client(monkeypatch, file_loc)
    exc, surfaces = _raise_and_surfaces(lambda: fetch_url(start, max_bytes=1000))
    assert isinstance(exc, IngestError)
    assert file_loc.requests == [start]
    assert "passwd" not in str(exc)
    assert "passwd" not in surfaces
    assert "file:" not in str(exc).lower()

    malformed = _FakeClient([_redirect(start, "https://[::")])
    _patch_client(monkeypatch, malformed)
    exc, surfaces = _raise_and_surfaces(lambda: fetch_url(start, max_bytes=1000))
    assert isinstance(exc, IngestError)
    assert malformed.requests == [start]
    assert "not a usable URL" in str(exc)
    assert "[::" not in str(exc)
    _assert_no_canary(surfaces)

    hostless = _FakeClient([_redirect(start, "https://@/")])
    _patch_client(monkeypatch, hostless)
    exc, surfaces = _raise_and_surfaces(lambda: fetch_url(start, max_bytes=1000))
    assert isinstance(exc, IngestError)
    assert hostless.requests == [start]
    assert "Refused redirect destination" in str(exc)
    _assert_no_canary(surfaces)


def test_redirect_loop_hits_limit(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    a = "https://example.com/a"
    b = "https://example.com/b"
    responses = []
    for i in range(6):
        src, dst = (a, b) if i % 2 == 0 else (b, a)
        responses.append(_redirect(src, dst))
    client = _FakeClient(responses)
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="maximum of 5 redirects"):
        fetch_url(a, max_bytes=1000)
    assert len(client.requests) == 6


def test_http_304_is_not_followed(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=304,
                headers={"location": "https://example.com/other.txt"},
                url=start,
            ),
            _plain_ok("https://example.com/other.txt"),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="not a supported redirect"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_http_300_is_not_followed(monkeypatch: pytest.MonkeyPatch):
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient(
        [
            _FakeStreamResponse(
                status_code=300,
                headers={"location": "https://example.com/other.txt"},
                url=start,
            ),
            _plain_ok("https://example.com/other.txt"),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="not a supported redirect"):
        fetch_url(start, max_bytes=1000)
    assert client.requests == [start]


def test_redirect_secrets_stay_on_wire_not_in_artifacts(
    monkeypatch: pytest.MonkeyPatch,
):
    from tads.export import report_to_json, report_to_markdown
    from tads.ingest import IngestOptions, ingest_to_text
    from tads.pipeline import run_scan

    start = f"https://user:{CANARY}@example.com/start?token={CANARY}#{CANARY}"
    dest = f"https://cdn.example.com/final?token={CANARY}#frag"
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient([_redirect(start, dest), _plain_ok(dest)])
    _patch_client(monkeypatch, client)
    ingested = ingest_to_text(start, options=IngestOptions(max_download_bytes=1000))
    assert client.requests == [start, dest]
    assert ingested.source_uri == "https://example.com/start"
    assert ingested.retrieved_uri == "https://cdn.example.com/final"
    _assert_no_canary(
        ingested.source,
        ingested.source_uri,
        ingested.retrieved_uri,
        *ingested.notes,
    )
    report = run_scan(
        ingested.text,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        retrieved_uri=ingested.retrieved_uri,
    )
    dumped = report_to_json(report)
    markdown = report_to_markdown(report)
    _assert_no_canary(dumped, markdown)
    assert report.document.source_uri == "https://example.com/start"
    assert report.document.retrieved_uri == "https://cdn.example.com/final"
    assert "**Retrieved URI:**" in markdown
    assert "`https://cdn.example.com/final`" in markdown
    assert "](" not in markdown.split("**Retrieved URI:**", 1)[1].split("\n", 1)[0]


def test_saved_remote_text_keeps_path_and_retrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from tads.ingest import IngestOptions, ingest_to_text

    start = "https://example.com/spec.txt"
    final = "https://cdn.example.com/spec.txt"
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient(
        [_redirect(start, final), _plain_ok(final, body=b"1 Intro\n\nBody.\n")]
    )
    _patch_client(monkeypatch, client)
    saved = tmp_path / "saved.txt"
    result = ingest_to_text(
        start,
        options=IngestOptions(save_text_path=str(saved), max_download_bytes=1000),
    )
    assert result.source_uri == start
    assert result.retrieved_uri == final
    assert result.source_path == str(saved)
    assert saved.read_text(encoding="utf-8")


def test_adapter_catalog_url_stays_source_uri(monkeypatch: pytest.MonkeyPatch):
    start = "https://www.w3.org/TR/hr-time-3/"
    final = "https://www.w3.org/TR/2023/REC-hr-time-3-20231219/"
    _allow_hosts(monkeypatch, {"www.w3.org": ["128.30.52.100"]})
    client = _FakeClient([_redirect(start, final, status=303), _plain_ok(final)])
    _patch_client(monkeypatch, client)
    result = fetch_url(start, max_bytes=1000, allow_html=True)
    assert result.url == start
    assert result.retrieved_uri == final


def test_scan_skips_provider_after_redirect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from typer.testing import CliRunner

    from tads.cli import app

    calls: list[str] = []

    def boom_provider(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr("tads.pipeline.get_provider", boom_provider)
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient([_redirect(start, "https://127.0.0.1/secret")])
    _patch_client(monkeypatch, client)
    out = tmp_path / "out" / "RFC9999"
    result = CliRunner().invoke(
        app,
        [
            "scan",
            start,
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert calls == []
    assert not (tmp_path / "out" / "RFC9999.json").exists()


def test_existing_output_unchanged_after_redirect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from typer.testing import CliRunner

    from tads.cli import app

    dest = tmp_path / "keep.txt"
    dest.write_text("KEEP-ME", encoding="utf-8")
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient([_redirect(start, "https://127.0.0.1/secret")])
    _patch_client(monkeypatch, client)
    result = CliRunner().invoke(
        app,
        ["convert", start, "--output", str(dest), "--overwrite"],
    )
    assert result.exit_code != 0
    assert dest.read_text(encoding="utf-8") == "KEEP-ME"


def test_existing_scan_reports_unchanged_after_redirect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from typer.testing import CliRunner

    from tads.cli import app

    out_prefix = tmp_path / "out" / "RFC9999"
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.parent.mkdir(parents=True)
    json_path.write_bytes(b"KEEP-JSON")
    md_path.write_bytes(b"KEEP-MD")
    _allow_hosts(monkeypatch, {"example.com": ["93.184.216.34"]})
    start = "https://example.com/doc.txt"
    client = _FakeClient([_redirect(start, "https://127.0.0.1/secret")])
    _patch_client(monkeypatch, client)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            start,
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--yes",
            "--overwrite",
            "--output",
            str(out_prefix),
        ],
    )
    assert result.exit_code != 0
    assert json_path.read_bytes() == b"KEEP-JSON"
    assert md_path.read_bytes() == b"KEEP-MD"


def test_schema_020_and_010_render_compat(monkeypatch: pytest.MonkeyPatch):
    from tads.export import report_to_json, report_to_markdown
    from tads.ingest import IngestOptions, ingest_to_text
    from tads.pipeline import run_scan
    from tads.schemas.report import Report

    start = "https://example.com/spec.txt"
    final = "https://cdn.example.com/spec.txt"
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient(
        [
            _redirect(start, final),
            _plain_ok(final, body=b"1 Intro\n\nTimers may wrap in 2038.\n"),
        ]
    )
    _patch_client(monkeypatch, client)
    ingested = ingest_to_text(start, options=IngestOptions(max_download_bytes=1000))
    report = run_scan(
        ingested.text,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        retrieved_uri=ingested.retrieved_uri,
    )
    dumped = json.loads(report_to_json(report))
    assert dumped["schema_version"] == "0.2.0"
    assert dumped["document"]["source_uri"] == start
    assert dumped["document"]["retrieved_uri"] == final
    md = report_to_markdown(report)
    assert "**Retrieved URI:**" in md

    old = """
    {
      "schema_version": "0.1.0",
      "document": {"corpus": "ietf", "doc_id": "RFC9999", "title": "Old"},
      "run": {"scanner_version": "0.4.0"},
      "findings": []
    }
    """
    loaded = Report.model_validate_json(old)
    assert loaded.schema_version == "0.1.0"
    assert loaded.document.retrieved_uri is None
    old_md = report_to_markdown(loaded)
    assert "**Retrieved URI:**" not in old_md
    old_json = json.loads(report_to_json(loaded))
    assert old_json["schema_version"] == "0.1.0"
    assert "retrieved_uri" not in old_json["document"]


def test_scan_markdown_matches_render_with_retrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from tads.export import load_report_json, report_to_json, report_to_markdown, write_report_json, write_report_markdown
    from tads.ingest import IngestOptions, ingest_to_text
    from tads.pipeline import run_scan

    start = "https://example.com/spec.txt"
    final = "https://cdn.example.com/spec.txt"
    _allow_hosts(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        },
    )
    client = _FakeClient(
        [
            _redirect(start, final),
            _plain_ok(final, body=b"1 Intro\n\nTimers may wrap in 2038.\n"),
        ]
    )
    _patch_client(monkeypatch, client)
    ingested = ingest_to_text(start, options=IngestOptions(max_download_bytes=1000))
    report = run_scan(
        ingested.text,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        retrieved_uri=ingested.retrieved_uri,
    )
    md1 = report_to_markdown(report)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    write_report_json(report, json_path)
    write_report_markdown(load_report_json(json_path), md_path)
    assert md_path.read_text(encoding="utf-8") == md1
    assert md1 == report_to_markdown(load_report_json(json_path))
    payload = json_path.read_text(encoding="utf-8")
    assert "retrieved_uri" in payload
    _assert_no_canary(md1, payload)
