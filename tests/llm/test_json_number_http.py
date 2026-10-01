# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Provider envelope and fallback-classifier numeric-policy tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from tads.llm.base import ChatMessage
from tads.llm.http import StructuredOutputNotSupportedError, post_json
from tads.llm.providers.gemini_provider import GeminiProvider
from tads.llm.providers.openai_provider import OpenAIProvider
from tads.llm.structured_output import classify_native_structured_output_error
from tests.llm.test_http import (
    BODY_CANARY,
    _FakeClient,
    _FakeResponse,
    _assert_clean_provider_error,
    _assert_no_canaries,
)

CPYTHON_LIMIT = "4300 digits"


def _digits(n: int) -> str:
    return "1" * n


def _openai_ok(*, extra: str = "") -> str:
    usage = extra or '"prompt_tokens": 1, "completion_tokens": 1'
    return (
        '{"choices":[{"message":{"content":"{\\"findings\\": []}"}}],'
        f'"usage":{{{usage}}}}}'
    )


def _gemini_ok(*, extra: str = "") -> str:
    usage = extra or '"promptTokenCount": 1, "candidatesTokenCount": 1'
    return (
        '{"candidates":[{"content":{"parts":[{"text":"{\\"findings\\": []}"}]}}],'
        f'"usageMetadata":{{{usage}}}}}'
    )


def test_openai_envelope_huge_usage_is_unexpected_response(monkeypatch, caplog):
    body = _openai_ok(extra='"prompt_tokens": ' + _digits(7365))
    caplog.set_level(logging.DEBUG)

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: _FakeResponse(200, text=body), *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    msg = str(exc_info.value)
    assert "unexpected response type" in msg.lower()
    assert CPYTHON_LIMIT not in msg
    assert _digits(40) not in msg
    assert BODY_CANARY not in msg
    _assert_clean_provider_error(exc_info.value)
    assert _digits(20) not in caplog.text


def test_gemini_envelope_huge_usage_is_unexpected_response(monkeypatch):
    body = _gemini_ok(extra='"promptTokenCount": ' + _digits(41))

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: _FakeResponse(200, text=body), *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("GOOGLE_API_KEY", "gem-test")
    with pytest.raises(RuntimeError) as exc_info:
        GeminiProvider().complete([ChatMessage(role="user", content="hi")])
    assert "unexpected response type" in str(exc_info.value).lower()
    _assert_clean_provider_error(exc_info.value)
    assert _digits(41) not in str(exc_info.value)


def test_envelope_numeric_failure_is_one_post_no_retry(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(200, text=_openai_ok(extra='"prompt_tokens": ' + _digits(41)))

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "3")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="unexpected response type"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 1


def test_error_body_huge_integer_does_not_fallback(monkeypatch):
    body = (
        '{"error":{"message":"Unknown parameter: \'response_format\'.",'
        '"param":"response_format","n":'
        + _digits(4300)
        + "}}"
    )
    assert classify_native_structured_output_error(body, family="openai") is False

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: _FakeResponse(400, text=body), *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    assert "HTTP 400" in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)


def test_error_body_nan_does_not_fallback(monkeypatch):
    body = json.dumps({"error": {"message": "Unknown parameter: 'response_format'", "param": "response_format"}})
    body = body[:-1] + ', "code": NaN}'
    assert classify_native_structured_output_error(body, family="openai") is False
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(400, text=body)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 1
    assert "response_format" in calls[0]


def test_post_json_huge_envelope_omits_body(monkeypatch, caplog):
    body = '{"ok": true, "n": ' + _digits(7365) + ", \"echo\": \"" + BODY_CANARY + '"}'
    caplog.set_level(logging.DEBUG)

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: _FakeResponse(200, text=body), *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            "https://api.openai.com/v1/chat/completions",
            headers={},
            payload={"model": "x"},
            provider_id="openai",
        )
    msg = str(exc_info.value)
    assert "unexpected response type" in msg.lower()
    _assert_no_canaries(msg)
    _assert_clean_provider_error(exc_info.value)
    assert BODY_CANARY not in caplog.text
    assert CPYTHON_LIMIT not in msg


def test_envelope_failure_cli_leaves_stale_raw_and_omits_reports(
    tmp_path: Path, monkeypatch
):
    from typer.testing import CliRunner

    from tads.cli import app
    from tads.llm.providers.mock_provider import MockProvider
    from tads.llm.registry import register_provider

    stale = tmp_path / "out.raw.txt"
    stale.write_text("STALE_RAW_KEEP", encoding="utf-8")
    before = stale.read_bytes()
    before_mtime = stale.stat().st_mtime_ns
    src = tmp_path / "doc.txt"
    src.write_text("Timestamps are 32-bit seconds.\n", encoding="utf-8")

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(
                    200, text=_openai_ok(extra='"prompt_tokens": ' + _digits(41))
                ),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    register_provider(OpenAIProvider())
    try:
        result = CliRunner().invoke(
            app,
            [
                "scan",
                str(src),
                "--doc-id",
                "RFC868",
                "--provider",
                "openai",
                "--yes",
                "--overwrite",
                "--save-raw-on-error",
                "-o",
                str(tmp_path / "out"),
            ],
        )
    finally:
        register_provider(MockProvider())
    assert result.exit_code != 0
    assert "unexpected response type" in result.output.lower()
    assert CPYTHON_LIMIT not in result.output
    assert stale.read_bytes() == before
    assert stale.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "out.json").exists()
    assert not (tmp_path / "out.md").exists()
    leftovers = list(tmp_path.glob(".tads-raw-*"))
    assert leftovers == []


def test_envelope_failure_does_not_touch_stale_raw(
    tmp_path: Path, monkeypatch
):
    stale = tmp_path / "out.raw.txt"
    stale.write_text("STALE_RAW_KEEP", encoding="utf-8")
    before = stale.stat().st_mtime_ns
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(200, text=_openai_ok(extra='"prompt_tokens": ' + _digits(41))),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="unexpected response type"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert stale.read_text(encoding="utf-8") == "STALE_RAW_KEEP"
    assert stale.stat().st_mtime_ns == before
    assert list(tmp_path.glob(".tads-raw-*")) == []
    assert not json_path.exists()
    assert not md_path.exists()
