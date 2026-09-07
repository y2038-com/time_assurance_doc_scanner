# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP helper tests (timeouts, connect fail-fast, secret redaction)."""

from __future__ import annotations

import httpx
import pytest

from tads.llm.http import (
    PROVIDER_ERROR_BODY_MAX_CHARS,
    _is_connect_or_handshake_failure,
    default_timeout,
    format_provider_http_error,
    post_json,
    redact_secrets,
    redact_url,
    sanitize_provider_error_body,
)
from tads.llm.providers.openai_provider import OpenAIProvider


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


def test_sanitize_preserves_short_json_error():
    body = '{"error":{"message":"invalid model","type":"invalid_request_error"}}'
    out = sanitize_provider_error_body(body)
    assert "invalid model" in out
    assert "... [truncated]" not in out


def test_sanitize_truncates_long_body():
    body = "x" * (PROVIDER_ERROR_BODY_MAX_CHARS + 500)
    out = sanitize_provider_error_body(body)
    assert out.endswith("... [truncated]")
    assert len(out) <= PROVIDER_ERROR_BODY_MAX_CHARS + len("... [truncated]")


def test_sanitize_empty_and_none():
    assert sanitize_provider_error_body(None) == "(empty response body)"
    assert sanitize_provider_error_body("") == "(empty response body)"
    assert sanitize_provider_error_body(b"") == "(empty response body)"


def test_sanitize_invalid_utf8_bytes():
    out = sanitize_provider_error_body(b"ok\xff\xfe more")
    assert "ok" in out
    assert "more" in out


def test_sanitize_redacts_bearer_and_api_key_and_sk():
    body = (
        "Authorization: Bearer abcdef1234567890\n"
        "api_key=supersecretvalue\n"
        "x-api-key: anothersecret\n"
        "token sk-abcdefghijklmnopqrstuvwxyz012345"
    )
    out = sanitize_provider_error_body(body)
    assert "abcdef1234567890" not in out
    assert "supersecretvalue" not in out
    assert "anothersecret" not in out
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "[REDACTED]" in out
    assert "Authorization: Bearer [REDACTED]" in out


def test_sanitize_preserves_ordinary_text():
    out = sanitize_provider_error_body("Model overloaded; retry later.")
    assert out == "Model overloaded; retry later."


def test_sanitize_normalizes_control_chars():
    out = sanitize_provider_error_body("a\x00b\n\n\nc")
    assert "\x00" not in out
    assert "a b" in out or "a b\n" in out


def test_format_http_error_prefers_extracted_message():
    msg = format_provider_http_error(
        400,
        {"error": {"message": "bad request with sk-abcdefghijklmnopqrstuv"}},
        safe_url="https://api.example/v1",
    )
    assert "HTTP 400" in msg
    assert "bad request" in msg
    assert "sk-abcdefghijklmnopqrstuv" not in msg
    assert "[REDACTED]" in msg


def test_post_json_http_error_is_sanitized(monkeypatch):
    class FakeResponse:
        status_code = 400
        text = (
            "Authorization: Bearer leakytokenvalue "
            + ("BODY" * 400)
        )
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError) as exc_info:
        post_json("https://api.openai.com/v1/chat/completions", headers={}, payload={})
    msg = str(exc_info.value)
    assert "HTTP 400" in msg
    assert "leakytokenvalue" not in msg
    assert "... [truncated]" in msg


def test_openai_unexpected_shape_sanitizes(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testkeynotreal000")

    def fake_post_json(url, *, headers, payload, timeout=None, retries=None):
        return {
            "id": "x",
            "error_echo": "Bearer shouldstayhiddenTOKEN",
            "blob": "y" * 2000,
        }

    monkeypatch.setattr(
        "tads.llm.providers.openai_provider.post_json", fake_post_json
    )
    provider = OpenAIProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.complete(
            [type("M", (), {"role": "user", "content": "hi"})()],
        )
    msg = str(exc_info.value)
    assert "Unexpected OpenAI response shape" in msg
    assert "shouldstayhiddenTOKEN" not in msg
    assert "... [truncated]" in msg
