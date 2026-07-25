# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared HTTP helpers for LLM providers."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx


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


def default_timeout() -> httpx.Timeout:
    """
    Timeouts for LLM HTTP calls.

    Defaults are intentionally generous for cloud models and large prompts.
    Override with:
      TADS_HTTP_TIMEOUT       total/read timeout seconds (default 600)
      TADS_HTTP_CONNECT_TIMEOUT  connect/TLS handshake seconds (default 60)
      TADS_HTTP_WRITE_TIMEOUT    request body write seconds (default 120)
    """
    total = _float_env("TADS_HTTP_TIMEOUT", 600.0)
    connect = _float_env("TADS_HTTP_CONNECT_TIMEOUT", 60.0)
    write = _float_env("TADS_HTTP_WRITE_TIMEOUT", 120.0)
    return httpx.Timeout(total, connect=connect, read=total, write=write, pool=connect)


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

    Retries: TADS_HTTP_RETRIES (default 3).
    """
    attempts = _int_env("TADS_HTTP_RETRIES", 3) if retries is None else retries
    attempts = max(1, attempts)
    last_error: Optional[BaseException] = None
    timeout = timeout or default_timeout()

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
                    f"LLM HTTP {response.status_code} for {url}: {detail}"
                )
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected LLM response type from {url}")
            return data
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            sleep_s = min(2 ** (attempt - 1), 8)
            time.sleep(sleep_s)

    assert last_error is not None
    raise RuntimeError(
        f"LLM request failed after {attempts} attempt(s) to {url}: {last_error}. "
        "If this is a TLS/handshake timeout, check network/VPN/WSL connectivity "
        "to the provider, or raise TADS_HTTP_CONNECT_TIMEOUT / TADS_HTTP_TIMEOUT."
    ) from last_error
