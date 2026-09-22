# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for SSRF-oriented URL security and redirect-safe fetch."""

from __future__ import annotations

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


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient):
    monkeypatch.setattr(
        "tads.ingest.fetch.httpx.Client",
        lambda **kwargs: client,
    )


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
                headers={"location": "http://127.0.0.1/secret"},
                url="https://example.com/doc.txt",
            ),
        ]
    )
    _patch_client(monkeypatch, client)
    with pytest.raises(IngestError, match="non-public|127.0.0.1"):
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
    _assert_no_canary(result.url, *result.notes)
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
    _assert_no_canary(result.url, *result.notes)


def test_rejected_redirect_error_and_traceback_hide_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    dest = f"http://127.0.0.1/secret?token={CANARY}"
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
    from tads.export import report_to_markdown
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
    assert ingested.source_path is None
    report = run_scan(
        ingested.text,
        doc_id="RFC9999",
        provider="mock",
        enforce_budget=False,
        source_uri=ingested.source_uri,
        source_path=ingested.source_path,
    )
    dumped = report.model_dump_json()
    markdown = report_to_markdown(report)
    _assert_no_canary(
        dumped,
        markdown,
        report.document.source_path or "",
        report.document.source_uri or "",
    )
    assert report.document.source_uri == "https://example.com/spec.txt"
    assert report.document.source_path is None
    assert "**Source URI:** https://example.com/spec.txt" in markdown
    assert "**Source path:**" not in markdown
