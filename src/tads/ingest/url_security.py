# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""SSRF-oriented URL validation for remote document ingest.

Application-level DNS/IP checks reduce SSRF risk for user-supplied HTTP(S)
URLs. They do not fully solve DNS rebinding between validation and connect;
hosted deployments should also enforce network-level egress controls, and may
need connection pinning later.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse


class UrlSecurityError(ValueError):
    """Raised when a remote URL fails SSRF / destination checks."""


def redact_url(url: str) -> str:
    """Return URL suitable for errors (strip userinfo; keep scheme/host/path)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "<unparseable-url>"
    hostname = parsed.hostname
    if not hostname:
        return urlunparse(
            (parsed.scheme, "", parsed.path or "", "", parsed.query, "")
        )
    if ":" in hostname:
        host = f"[{hostname}]"
    else:
        host = hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunparse(
        (parsed.scheme, host, parsed.path or "", "", parsed.query, "")
    )


def is_disallowed_ip(address: object) -> bool:
    """
    Return True if ``address`` must not be contacted by default.

    ``address`` may be an ``ipaddress`` object or a string/bytes IP literal.
    Non-global addresses are disallowed (loopback, private, link-local,
    multicast, unspecified, reserved, etc.).
    """
    if not isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        address = ipaddress.ip_address(address)
    # Prefer is_global, but some platforms treat multicast as global — reject
    # non-public categories explicitly for predictable SSRF behavior.
    if (
        address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
        or not address.is_global
    ):
        return True
    return False


def _is_localhost_hostname(hostname: str) -> bool:
    host = hostname.strip().rstrip(".").lower()
    return host == "localhost" or host.endswith(".localhost")


def resolve_and_validate_host(
    hostname: str,
    *,
    allow_private: bool = False,
) -> None:
    """
    Resolve ``hostname`` and reject disallowed destinations unless opted in.

    DNS failure always raises. If any resolved address is disallowed and
    ``allow_private`` is False, the host is rejected (no pick-one-safe-A).
    """
    host = hostname.strip().rstrip(".")
    if not host:
        raise UrlSecurityError("URL must contain a hostname")

    # Literal IP in the host field — validate without DNS.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not allow_private and is_disallowed_ip(literal):
            raise UrlSecurityError(
                f"Refusing to fetch non-public network destination: {literal}"
            )
        return

    if not allow_private and _is_localhost_hostname(host):
        raise UrlSecurityError(
            "Refusing to fetch non-public network destination: localhost"
        )

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlSecurityError(
            f"DNS resolution failed for hostname {host!r}: {exc}"
        ) from exc
    if not infos:
        raise UrlSecurityError(f"DNS resolution returned no addresses for {host!r}")

    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        # Skip duplicates from getaddrinfo.
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not allow_private and is_disallowed_ip(addr):
            raise UrlSecurityError(
                f"Hostname {host} resolves to non-public address {addr}"
            )


def validate_remote_url(url: str, *, allow_private: bool = False) -> None:
    """
    Validate that ``url`` is a safe http(s) destination for ingest fetch.

    Only ``http`` / ``https`` with a hostname are allowed. By default, the
    hostname must resolve exclusively to globally routable addresses.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise UrlSecurityError(f"Invalid URL: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise UrlSecurityError(
            f"Unsupported URL scheme {scheme!r} (only http/https allowed): "
            f"{redact_url(url)}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise UrlSecurityError(
            f"URL must contain a hostname: {redact_url(url)}"
        )

    resolve_and_validate_host(hostname, allow_private=allow_private)
