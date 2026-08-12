# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP helper tests (timeouts, connect fail-fast, secret redaction)."""

from __future__ import annotations

import httpx
import pytest

from tads.llm.http import (
    _is_connect_or_handshake_failure,
    default_timeout,
    post_json,
    redact_secrets,
    redact_url,
)


def test_redact_url_strips_api_key_query():
    url = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=SECRET"
    assert "SECRET" not in redact_url(url)
    assert "key=***" in redact_url(url)


def test_redact_secrets_in_exception_text():
    text = (
        "failed to https://example.com/v1?key=SECRET123: handshake timed out"
    )
    out = redact_secrets(text)
    assert "SECRET123" not in out
    assert "key=***" in out


def test_default_connect_timeout_is_short(monkeypatch):
    monkeypatch.delenv("TADS_HTTP_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("TADS_HTTP_TIMEOUT", raising=False)
    timeout = default_timeout()
    assert timeout.connect == 10.0


def test_connect_timeout_detected():
    assert _is_connect_or_handshake_failure(httpx.ConnectTimeout("connect"))
    assert _is_connect_or_handshake_failure(
        httpx.ConnectError("Name or service not known")
    )
    assert _is_connect_or_handshake_failure(
        RuntimeError("_ssl.c:983: The handshake operation timed out")
    )
    assert not _is_connect_or_handshake_failure(httpx.ReadTimeout("read"))


def test_connect_timeout_fails_without_full_retry_budget(monkeypatch):
    calls = {"n": 0}

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls["n"] += 1
            raise httpx.ConnectTimeout("connect timed out")

    monkeypatch.setattr(httpx, "Client", BoomClient)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "5")
    monkeypatch.setenv("TADS_HTTP_CONNECT_RETRIES", "1")

    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            "https://example.com/v1?key=SHOULD_NOT_LEAK",
            headers={},
            payload={},
        )
    assert calls["n"] == 1
    msg = str(exc_info.value)
    assert "SHOULD_NOT_LEAK" not in msg
    assert "key=***" in msg
    assert "1 attempt" in msg
