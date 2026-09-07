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
