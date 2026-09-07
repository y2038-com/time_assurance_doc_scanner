# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared HTTP helpers for LLM providers."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

# Bound provider error bodies before they appear in exceptions / logs.
PROVIDER_ERROR_BODY_MAX_CHARS = 1000

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
_AUTH_HEADER = re.compile(
    r"(Authorization\s*:\s*Bearer\s+)(\S+)", re.IGNORECASE
)
_API_KEY_ASSIGN = re.compile(
    r"((?:api[_-]?key|x-api-key|access[_-]?token)\s*[:=]\s*)([^\s,\"'}]+)",
    re.IGNORECASE,
)
_SK_TOKEN = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
    """Best-effort scrub of keys embedded in exception / URL / body strings."""
    scrubbed = _AUTH_HEADER.sub(r"\1[REDACTED]", text)
    scrubbed = _BEARER.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _API_KEY_ASSIGN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _SK_TOKEN.sub("[REDACTED]", scrubbed)

    def _sub(match: re.Match[str]) -> str:
        return redact_url(match.group(0))

    return re.sub(r"https?://\S+", _sub, scrubbed)


def _coerce_error_text(body: str | bytes | Any | None) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, default=str, ensure_ascii=False)
    except Exception:
        return str(body)


def sanitize_provider_error_body(
    body: str | bytes | Any | None,
    *,
    max_chars: int = PROVIDER_ERROR_BODY_MAX_CHARS,
) -> str:
    """
    Bound and redact a provider error payload for safe exception / log text.

    Never raises solely because ``body`` is malformed or oversized.
    """
    try:
        text = _coerce_error_text(body)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _CTRL.sub(" ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return "(empty response body)"
        text = redact_secrets(text)
        limit = max(1, int(max_chars))
        if len(text) > limit:
            return text[:limit].rstrip() + "... [truncated]"
        return text
    except Exception:
        return "(unreadable response body)"


def extract_provider_error_message(data: Any) -> str | None:
    """Return a shallow provider error message field when present."""
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("status")
        if msg:
            return str(msg)
    if isinstance(err, str) and err.strip():
        return err
    msg = data.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg
    raw = data.get("raw_text")
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def format_provider_http_error(
    status_code: int,
    data: Any,
    *,
    safe_url: str,
) -> str:
    """Build a bounded HTTP error message for ``post_json`` failures."""
    extracted = extract_provider_error_message(data)
    if extracted is not None:
        snippet = sanitize_provider_error_body(extracted)
    else:
        snippet = sanitize_provider_error_body(data)
    return f"LLM HTTP {status_code} for {safe_url}: {snippet}"


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
                # Retry rate limits / gateway blips
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                raise RuntimeError(
                    format_provider_http_error(
                        response.status_code, data, safe_url=safe_url
                    )
                )
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Unexpected LLM response type from {safe_url}: "
                    f"{sanitize_provider_error_body(data)}"
                )
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
    detail = sanitize_provider_error_body(str(last_error))
    hint = (
        "If this is a TLS/handshake/connect timeout, check network/VPN/WSL "
        "connectivity to the provider, or adjust TADS_HTTP_CONNECT_TIMEOUT "
        f"(default 10s; connect retries default {connect_attempts})."
    )
    raise RuntimeError(
        f"LLM request failed after {attempt} attempt(s) to {safe_url}: {detail}. {hint}"
    ) from last_error
