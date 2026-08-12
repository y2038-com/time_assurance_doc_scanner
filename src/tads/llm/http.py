# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared HTTP helpers for LLM providers."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

# Query / fragment params that must never appear in error text.
_SECRET_QUERY_KEYS = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "access_token",
        "token",
        "auth",
        "authorization",
    }
)
_BEARER = re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def redact_url(url: str) -> str:
    """Strip API keys and similar secrets from a URL for safe logging/errors."""
    from urllib.parse import quote

    parts = urlsplit(url)
    if not parts.query:
        return url
    pieces: list[str] = []
    changed = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            pieces.append(f"{quote(key)}=***")
            changed = True
        else:
            pieces.append(f"{quote(key)}={quote(value)}")
    if not changed:
        return url
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(pieces), parts.fragment)
    )


def redact_secrets(text: str) -> str:
    """Best-effort scrub of keys embedded in exception / URL strings."""
    scrubbed = _BEARER.sub(r"\1***", text)
    # Redact any http(s) URL that may carry ?key=
    def _sub(match: re.Match[str]) -> str:
        return redact_url(match.group(0))

    return re.sub(r"https?://\S+", _sub, scrubbed)


def default_timeout() -> httpx.Timeout:
    """
    Timeouts for LLM HTTP calls.

    Read/total stay generous for large prompts. Connect/TLS fails fast so a
    blocked VPN/WSL path does not burn minutes on retries.

    Override with:
      TADS_HTTP_TIMEOUT            total/read timeout seconds (default 600)
      TADS_HTTP_CONNECT_TIMEOUT    connect/TLS handshake seconds (default 10)
      TADS_HTTP_WRITE_TIMEOUT      request body write seconds (default 120)
    """
    total = _float_env("TADS_HTTP_TIMEOUT", 600.0)
    connect = _float_env("TADS_HTTP_CONNECT_TIMEOUT", 10.0)
    write = _float_env("TADS_HTTP_WRITE_TIMEOUT", 120.0)
    return httpx.Timeout(total, connect=connect, read=total, write=write, pool=connect)


def _is_connect_or_handshake_failure(exc: BaseException) -> bool:
    """True when further retries are unlikely to help (VPN/DNS/TLS blocked)."""
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError)):
        return True
    # Some OpenSSL handshake timeouts surface as a generic TimeoutException.
    if isinstance(exc, httpx.TimeoutException) and not isinstance(
        exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    ):
        msg = str(exc).lower()
        if "handshake" in msg or "connect" in msg:
            return True
    msg = str(exc).lower()
    return "handshake operation timed out" in msg or "connect timeout" in msg


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: Optional[httpx.Timeout] = None,
    retries: Optional[int] = None,
) -> dict[str, Any]:
    """
    POST JSON with retries on transient network / TLS failures.

    Retries: TADS_HTTP_RETRIES (default 3) for read/status blips.
    Connect/TLS handshake failures fail after one attempt (override with
    TADS_HTTP_CONNECT_RETRIES, default 1).
    """
    attempts = _int_env("TADS_HTTP_RETRIES", 3) if retries is None else retries
    attempts = max(1, attempts)
    connect_attempts = max(1, _int_env("TADS_HTTP_CONNECT_RETRIES", 1))
    last_error: Optional[BaseException] = None
    timeout = timeout or default_timeout()
    safe_url = redact_url(url)

    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
            try:
                data = response.json()
            except Exception:
                data = {"raw_text": response.text}
            if response.status_code >= 400:
                detail = data if isinstance(data, dict) else {"body": data}
                # Retry rate limits / gateway blips
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                raise RuntimeError(
                    f"LLM HTTP {response.status_code} for {safe_url}: {detail}"
                )
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected LLM response type from {safe_url}")
            return data
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if _is_connect_or_handshake_failure(exc):
                if attempt >= connect_attempts:
                    break
            elif attempt >= attempts:
                break
            sleep_s = min(2 ** (attempt - 1), 8)
            time.sleep(sleep_s)

    assert last_error is not None
    detail = redact_secrets(str(last_error))
    hint = (
        "If this is a TLS/handshake/connect timeout, check network/VPN/WSL "
        "connectivity to the provider, or adjust TADS_HTTP_CONNECT_TIMEOUT "
        f"(default 10s; connect retries default {connect_attempts})."
    )
    raise RuntimeError(
        f"LLM request failed after {attempt} attempt(s) to {safe_url}: {detail}. {hint}"
    ) from last_error
