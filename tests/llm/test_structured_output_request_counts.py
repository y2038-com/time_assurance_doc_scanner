# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP attempt counts for native structured output and fallback."""

from __future__ import annotations

import json

import httpx
import pytest

from tads.llm.base import ChatMessage
from tads.llm.providers.gemini_provider import GeminiProvider
from tads.llm.providers.openai_provider import OpenAIProvider
from tests.llm.test_http import _FakeClient, _FakeResponse

UNKNOWN_RESPONSE_FORMAT = json.dumps(
    {
        "error": {
            "message": "Unknown parameter: 'response_format'.",
            "param": "response_format",
            "code": "unknown_parameter",
        }
    }
)
INVALID_SCHEMA = json.dumps(
    {
        "error": {
            "code": "invalid_json_schema",
            "message": "Invalid schema for response_format['json_schema']",
            "param": "response_format",
        }
    }
)
KEYWORD_UNSUPPORTED = json.dumps(
    {
        "error": {
            "message": "The keyword minLength is not supported for json_schema",
            "param": "response_format",
        }
    }
)
UNRELATED_PARAM = json.dumps(
    {
        "error": {
            "message": "Unknown parameter: 'temperature' when using response_format",
            "param": "temperature",
        }
    }
)
INVALID_PROPERTY = json.dumps(
    {
        "error": {
            "message": "Invalid property at response_format.json_schema.schema",
            "param": "response_format",
        }
    }
)
MODEL_DOES_NOT_SUPPORT = json.dumps(
    {
        "error": {
            "message": "This model does not support response_format of type json_schema.",
            "param": "response_format",
        }
    }
)
GENERIC_400 = json.dumps({"error": {"message": "invalid model"}})
OK = {
    "choices": [{"message": {"content": '{"findings": []}'}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


def _complete(monkeypatch, responder, *, retries_env=None):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return responder(len(calls), json)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    if retries_env is not None:
        monkeypatch.setenv("TADS_HTTP_RETRIES", str(retries_env))
    provider = OpenAIProvider()
    result = provider.complete([ChatMessage(role="user", content="hi")])
    return calls, result, provider


def test_native_success_is_one_post(monkeypatch):
    def responder(_n, payload):
        assert "response_format" in payload
        return _FakeResponse(200, payload=OK)

    calls, result, _ = _complete(monkeypatch, responder)
    assert len(calls) == 1
    assert result.content == '{"findings": []}'


@pytest.mark.parametrize("body", [UNKNOWN_RESPONSE_FORMAT, MODEL_DOES_NOT_SUPPORT])
def test_explicit_unsupported_plus_fallback_is_two_posts(monkeypatch, body):
    def responder(n, payload):
        if "response_format" in payload:
            return _FakeResponse(400, text=body)
        return _FakeResponse(200, payload=OK)

    calls, result, provider = _complete(monkeypatch, responder)
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert result.content == '{"findings": []}'
    assert provider.supports_native_structured_output() is False


@pytest.mark.parametrize(
    "body",
    [INVALID_SCHEMA, GENERIC_400, KEYWORD_UNSUPPORTED, UNRELATED_PARAM, INVALID_PROPERTY],
)
def test_invalid_or_generic_400_is_one_post(monkeypatch, body):
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


def test_401_is_one_post(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(401, text=GENERIC_400)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 1
    assert "response_format" in calls[0]


def test_timeout_uses_existing_retries_without_schema_free_fallback(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            raise httpx.ReadTimeout("read")

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "3")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="read timeout"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 3
    assert all("response_format" in payload for payload in calls)


def test_429_retries_same_native_payload(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(429, text="rate limited")

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "3")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 3
    assert all("response_format" in payload for payload in calls)


def test_unsupported_then_fallback_429_retries_schema_free_only(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            if "response_format" in json:
                return _FakeResponse(400, text=UNKNOWN_RESPONSE_FORMAT)
            return _FakeResponse(429, text="rate limited")

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "3")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 4
    assert "response_format" in calls[0]
    assert all("response_format" not in payload for payload in calls[1:])


def test_malformed_200_does_not_fallback(monkeypatch):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(200, payload=["not", "a", "dict"])

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="unexpected response type"):
        OpenAIProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 1
    assert "response_format" in calls[0]


GEMINI_UNKNOWN_FIELD = json.dumps(
    {
        "error": {
            "code": 400,
            "message": (
                "Invalid JSON payload received. Unknown name "
                "\"responseSchema\" at 'generation_config': Cannot find field."
            ),
            "status": "INVALID_ARGUMENT",
        }
    }
)
GEMINI_MODEL_UNSUPPORTED = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "This model does not support responseSchema",
            "status": "INVALID_ARGUMENT",
        }
    }
)
GEMINI_UNRELATED_PARAM = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "Unknown parameter: 'temperature' when using responseSchema",
            "param": "temperature",
            "status": "INVALID_ARGUMENT",
        }
    }
)
GEMINI_KEYWORD = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "Keyword additionalProperties is not supported in responseSchema",
            "status": "INVALID_ARGUMENT",
        }
    }
)
GEMINI_NESTED = json.dumps(
    {
        "error": {
            "code": 400,
            "message": (
                "Unknown name \"additionalProperties\" at "
                "'generation_config.response_schema'"
            ),
            "status": "INVALID_ARGUMENT",
        }
    }
)
GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": '{"findings": []}'}]}}],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
}


def _gemini_complete(monkeypatch, responder):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return responder(len(calls), json)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "gem-test")
    provider = GeminiProvider()
    result = provider.complete([ChatMessage(role="user", content="hi")])
    return calls, result, provider


@pytest.mark.parametrize("body", [GEMINI_UNKNOWN_FIELD, GEMINI_MODEL_UNSUPPORTED])
def test_gemini_explicit_unsupported_plus_fallback_is_two_posts(monkeypatch, body):
    def responder(_n, payload):
        gen = payload.get("generationConfig") or {}
        if "responseSchema" in gen:
            return _FakeResponse(400, text=body)
        return _FakeResponse(200, payload=GEMINI_OK)

    calls, result, provider = _gemini_complete(monkeypatch, responder)
    assert len(calls) == 2
    assert "responseSchema" in calls[0]["generationConfig"]
    assert "responseSchema" not in calls[1]["generationConfig"]
    assert result.content == '{"findings": []}'
    assert provider.supports_native_structured_output() is False


@pytest.mark.parametrize("body", [GEMINI_UNRELATED_PARAM, GEMINI_KEYWORD, GEMINI_NESTED])
def test_gemini_misleading_or_keyword_400_is_one_post(monkeypatch, body):
    calls = []

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(lambda: None, *args, **kwargs)

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return _FakeResponse(400, text=body)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("GOOGLE_API_KEY", "gem-test")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        GeminiProvider().complete([ChatMessage(role="user", content="hi")])
    assert len(calls) == 1
    assert "responseSchema" in calls[0]["generationConfig"]
