# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared HTTP helpers for LLM providers."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

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

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


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


def diagnostic_endpoint_url(url: str) -> str:
    """Return scheme/host/path for provider diagnostic messages.

    Drops userinfo, the entire query string, and the fragment. This is the
    provider-endpoint sanitizer, distinct from document provenance even when
    the drop rules match.
    """
    if not isinstance(url, str) or not url:
        return "<unparseable-url>"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except Exception:
        return "<unparseable-url>"
    if not hostname:
        host = ""
    elif ":" in hostname:
        host = f"[{hostname}]"
    else:
        host = hostname
    if port is not None:
        host = f"{host}:{port}"
    try:
        safe = urlunsplit((parsed.scheme, host, parsed.path or "", "", ""))
    except Exception:
        return "<unparseable-url>"
    if "?" in safe or "#" in safe:
        return "<unparseable-url>"
    return safe


def redact_url(url: str) -> str:
    """Backward-compatible alias for ``diagnostic_endpoint_url``."""
    return diagnostic_endpoint_url(url)


def redact_secrets(text: str) -> str:
    """Best-effort scrub of keys embedded in controlled diagnostic strings."""
    scrubbed = _AUTH_HEADER.sub(r"\1[REDACTED]", text)
    scrubbed = _BEARER.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _API_KEY_ASSIGN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _SK_TOKEN.sub("[REDACTED]", scrubbed)
    scrubbed = _CTRL.sub(" ", scrubbed)

    def _sub(match: re.Match[str]) -> str:
        return diagnostic_endpoint_url(match.group(0))

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
    if isinstance(exc, httpx.TimeoutException) and not isinstance(
        exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    ):
        return True
    return False


def _network_category(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection failure"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        return "network error"
    return "network error"


def _provider_label(provider_id: str | None) -> str:
    name = (provider_id or "").strip()
    if not name:
        return "LLM"
    return redact_secrets(name)


def format_http_status_error(
    status_code: int,
    *,
    endpoint: str,
    retryable: bool,
    provider_id: str | None = None,
    attempts: int | None = None,
) -> str:
    """Allowlisted HTTP failure text. No provider body or third-party strings."""
    kind = "retryable" if retryable else "non-retryable"
    who = _provider_label(provider_id)
    text = f"{who} HTTP {status_code} for {endpoint} ({kind})"
    if attempts is not None:
        text += f" after {attempts} attempt(s)"
    return text


def format_request_failure(
    *,
    category: str,
    endpoint: str,
    attempts: int,
    provider_id: str | None = None,
    retryable: bool = True,
) -> str:
    who = _provider_label(provider_id)
    kind = "retryable" if retryable else "non-retryable"
    return (
        f"{who} request failed after {attempts} attempt(s) to {endpoint}: "
        f"{category} ({kind})"
    )


def format_unexpected_response_error(
    *,
    endpoint: str,
    provider_id: str | None = None,
) -> str:
    who = _provider_label(provider_id)
    return f"{who} unexpected response type for {endpoint}"


def format_shape_error(provider_id: str) -> str:
    who = _provider_label(provider_id)
    return f"Unexpected {who} response shape"


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: Optional[httpx.Timeout] = None,
    retries: Optional[int] = None,
    provider_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    POST JSON with retries on transient network / TLS failures.

    Retries: TADS_HTTP_RETRIES (default 3) for read/status blips.
    Connect/TLS handshake failures fail after one attempt (override with
    TADS_HTTP_CONNECT_RETRIES, default 1).

    Ordinary errors include only allowlisted classification fields. Provider
    bodies, headers, and httpx request/response objects are not retained.
    """
    attempts = _int_env("TADS_HTTP_RETRIES", 3) if retries is None else retries
    attempts = max(1, attempts)
    connect_attempts = max(1, _int_env("TADS_HTTP_CONNECT_RETRIES", 1))
    timeout = timeout or default_timeout()
    endpoint = diagnostic_endpoint_url(url)

    pending: RuntimeError | None = None
    last_category: str | None = None
    last_retryable = True

    for attempt in range(1, attempts + 1):
        connect_fail = False
        category: str | None = None
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                status = int(response.status_code)
                if status >= 400:
                    if status in _RETRYABLE_STATUS:
                        category = f"HTTP {status}"
                        last_category = category
                        last_retryable = True
                    else:
                        pending = RuntimeError(
                            format_http_status_error(
                                status,
                                endpoint=endpoint,
                                retryable=False,
                                provider_id=provider_id,
                            )
                        )
                else:
                    try:
                        data = response.json()
                    except Exception:
                        pending = RuntimeError(
                            format_unexpected_response_error(
                                endpoint=endpoint, provider_id=provider_id
                            )
                        )
                        data = None
                    if pending is None and not isinstance(data, dict):
                        pending = RuntimeError(
                            format_unexpected_response_error(
                                endpoint=endpoint, provider_id=provider_id
                            )
                        )
                    elif pending is None:
                        return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            category = _network_category(exc)
            connect_fail = _is_connect_or_handshake_failure(exc)
            last_category = category
            last_retryable = True

        if pending is not None:
            break
        if category is None:
            break
        if connect_fail:
            if attempt >= connect_attempts:
                break
        elif attempt >= attempts:
            break
        time.sleep(min(2 ** (attempt - 1), 8))

    if pending is not None:
        raise pending
    raise RuntimeError(
        format_request_failure(
            category=last_category or "error",
            endpoint=endpoint,
            attempts=attempt,
            provider_id=provider_id,
            retryable=last_retryable,
        )
    )


# Retained for tests of token redaction on controlled strings. Ordinary
# provider errors do not emit provider bodies.
PROVIDER_ERROR_BODY_MAX_CHARS = 1000


def sanitize_provider_error_body(
    body: str | bytes | Any | None,
    *,
    max_chars: int = PROVIDER_ERROR_BODY_MAX_CHARS,
) -> str:
    """Redact and bound a string. Not used to publish provider bodies."""
    try:
        if body is None:
            text = ""
        elif isinstance(body, bytes):
            text = body.decode("utf-8", errors="replace")
        elif isinstance(body, str):
            text = body
        else:
            try:
                text = json.dumps(body, default=str, ensure_ascii=False)
            except Exception:
                text = "unreadable"
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
