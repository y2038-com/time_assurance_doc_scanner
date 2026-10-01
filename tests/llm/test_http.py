# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTTP helper tests (timeouts, allowlisted errors, exception-graph sanitation)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from tads.llm.http import (
    PROVIDER_ERROR_BODY_MAX_CHARS,
    StructuredOutputNotSupportedError,
    _is_connect_or_handshake_failure,
    default_timeout,
    diagnostic_endpoint_url,
    format_http_status_error,
    post_json,
    redact_secrets,
    redact_url,
    sanitize_provider_error_body,
)
from tads.llm.structured_output import (
    OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS,
    UNSUPPORTED_FEATURE_BODY_CHARS,
    classify_native_structured_output_error,
)
from tads.llm.providers.openai_provider import OpenAIProvider

BODY_CANARY = "PROVIDER_BODY_CANARY_xyz"
DOC_CANARY = "DOCUMENT_TEXT_CANARY_xyz"
TOKEN_CANARY = "sk-abcdefghijklmnopqrstuvwxyz012345"
HEADER_CANARY = "HDR_AUTH_CANARY_xyz"
QUERY_CANARY = "QUERY_CANARY_xyz"
USERINFO_CANARY = "USERINFO_CANARY_xyz"
CTRL_CANARY = "before\x00after"


def _walk_exceptions(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or current in seen:
            continue
        seen.append(current)
        stack.append(current.__cause__)
        stack.append(current.__context__)
        for value in getattr(current, "__dict__", {}).values():
            if isinstance(value, BaseException):
                stack.append(value)
    return seen


def _assert_clean_provider_error(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    for item in _walk_exceptions(exc):
        assert item.__cause__ is None
        assert item.__context__ is None
        for name, value in list(getattr(item, "__dict__", {}).items()):
            assert not isinstance(value, (httpx.Request, httpx.Response, httpx.HTTPError))
            if name in {"request", "response"}:
                assert value is None
        assert not isinstance(getattr(item, "request", None), httpx.Request)
        assert not isinstance(getattr(item, "response", None), httpx.Response)


def _assert_no_canaries(text: str) -> None:
    for canary in (
        BODY_CANARY,
        DOC_CANARY,
        TOKEN_CANARY,
        HEADER_CANARY,
        QUERY_CANARY,
        USERINFO_CANARY,
        "\x00",
    ):
        assert canary not in text


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        payload=None,
        text: str = "",
        json_error: Exception | None = None,
        url: str = "https://api.example/v1",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"Authorization": f"Bearer {HEADER_CANARY}"}
        self.request = httpx.Request(
            "POST",
            f"https://{USERINFO_CANARY}:pw@api.example/v1?token={QUERY_CANARY}",
        )
        self._payload = payload
        self._json_error = json_error
        self.url = url

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeClient:
    def __init__(self, responder, *args, **kwargs):
        _ = (args, kwargs)
        self._responder = responder

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        _ = (url, headers, json)
        return self._responder()


def test_diagnostic_endpoint_drops_userinfo_query_fragment():
    url = (
        f"https://{USERINFO_CANARY}:secret@generativelanguage.googleapis.com"
        f"/v1beta/models/x:generateContent?key={QUERY_CANARY}#{DOC_CANARY}"
    )
    safe = diagnostic_endpoint_url(url)
    _assert_no_canaries(safe)
    assert "?" not in safe
    assert "#" not in safe
    assert "@" not in safe
    assert "generativelanguage.googleapis.com" in safe
    assert redact_url(url) == safe


def test_redact_secrets_rewrites_embedded_urls_and_tokens():
    text = (
        f"failed to https://example.com/v1?key={QUERY_CANARY} "
        f"Authorization: Bearer {HEADER_CANARY} {TOKEN_CANARY} {CTRL_CANARY}"
    )
    out = redact_secrets(text)
    _assert_no_canaries(out)
    assert "[REDACTED]" in out
    assert "https://example.com/v1" in out


def test_default_connect_timeout_is_short(monkeypatch):
    monkeypatch.delenv("TADS_HTTP_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("TADS_HTTP_TIMEOUT", raising=False)
    timeout = default_timeout()
    assert timeout.connect == 10.0
    assert timeout.read == 600.0


def test_connect_timeout_detected():
    assert _is_connect_or_handshake_failure(httpx.ConnectTimeout("connect"))
    assert _is_connect_or_handshake_failure(
        httpx.ConnectError("Name or service not known")
    )
    assert not _is_connect_or_handshake_failure(httpx.ReadTimeout("read"))
    assert not _is_connect_or_handshake_failure(
        RuntimeError("_ssl.c:983: The handshake operation timed out")
    )


def test_connect_timeout_fails_without_full_retry_budget(monkeypatch, caplog):
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
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            f"https://example.com/v1?key={QUERY_CANARY}",
            headers={"Authorization": f"Bearer {HEADER_CANARY}"},
            payload={"prompt": DOC_CANARY},
            provider_id="openai",
        )
    assert calls["n"] == 1
    msg = str(exc_info.value)
    _assert_no_canaries(msg)
    _assert_clean_provider_error(exc_info.value)
    assert "1 attempt" in msg
    assert "connect timeout" in msg
    assert "openai" in msg.lower()
    assert QUERY_CANARY not in caplog.text
    assert HEADER_CANARY not in caplog.text
    assert DOC_CANARY not in caplog.text


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


def test_sanitize_redacts_bearer_and_api_key_and_sk():
    body = (
        f"Authorization: Bearer {HEADER_CANARY}\n"
        "api_key=supersecretvalue\n"
        f"token {TOKEN_CANARY}"
    )
    out = sanitize_provider_error_body(body)
    assert HEADER_CANARY not in out
    assert "supersecretvalue" not in out
    assert TOKEN_CANARY not in out
    assert "[REDACTED]" in out


def test_format_http_status_error_is_allowlisted():
    msg = format_http_status_error(
        400,
        endpoint="https://api.example/v1",
        retryable=False,
        provider_id="openai",
    )
    assert "HTTP 400" in msg
    assert "non-retryable" in msg
    assert "openai" in msg.lower()
    assert BODY_CANARY not in msg


def test_post_json_http_error_omits_bodies_and_headers(
    monkeypatch, caplog
):
    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(
                    400,
                    text=BODY_CANARY * 400,
                    payload={"error": {"message": BODY_CANARY}},
                ),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            f"https://api.openai.com/v1/chat/completions?key={QUERY_CANARY}",
            headers={"Authorization": f"Bearer {HEADER_CANARY}"},
            payload={"messages": [{"content": DOC_CANARY}]},
            provider_id="openai",
        )
    msg = str(exc_info.value)
    _assert_no_canaries(msg)
    _assert_clean_provider_error(exc_info.value)
    assert "HTTP 400" in msg
    assert "non-retryable" in msg
    assert "... [truncated]" not in msg
    assert BODY_CANARY not in caplog.text


def test_retryable_status_retries_then_fails(monkeypatch):
    calls = {"n": 0}

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            def responder():
                calls["n"] += 1
                return _FakeResponse(503, text=BODY_CANARY)

            super().__init__(responder, *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("tads.llm.http.time.sleep", lambda *_a, **_k: None)
    monkeypatch.setenv("TADS_HTTP_RETRIES", "3")
    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            "https://api.example/v1",
            headers={},
            payload={},
            provider_id="openai",
        )
    assert calls["n"] == 3
    msg = str(exc_info.value)
    assert "HTTP 503" in msg
    assert "retryable" in msg
    assert "3 attempt" in msg
    assert BODY_CANARY not in msg
    _assert_clean_provider_error(exc_info.value)


def test_non_retryable_status_does_not_retry(monkeypatch):
    calls = {"n": 0}

    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            def responder():
                calls["n"] += 1
                return _FakeResponse(401, text=BODY_CANARY)

            super().__init__(responder, *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            "https://api.example/v1",
            headers={},
            payload={},
            retries=5,
            provider_id="openai",
        )
    assert calls["n"] == 1
    msg = str(exc_info.value)
    assert "HTTP 401" in msg
    assert "non-retryable" in msg
    assert BODY_CANARY not in msg
    _assert_clean_provider_error(exc_info.value)


def test_unexpected_json_type_omits_payload(monkeypatch):
    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(200, payload=["not", "a", "dict", BODY_CANARY]),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(RuntimeError) as exc_info:
        post_json("https://api.example/v1", headers={}, payload={}, provider_id="openai")
    msg = str(exc_info.value)
    assert "unexpected response type" in msg.lower()
    assert BODY_CANARY not in msg
    _assert_clean_provider_error(exc_info.value)


def test_openai_unexpected_shape_does_not_reproduce_success_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testkeynotreal000")

    def fake_post_json(url, *, headers, payload, timeout=None, retries=None, provider_id=None, **kwargs):
        _ = (url, headers, payload, timeout, retries, provider_id, kwargs)
        return {
            "id": "x",
            "error_echo": f"Bearer {HEADER_CANARY}",
            "blob": BODY_CANARY * 50,
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
    _assert_no_canaries(msg)
    assert "... [truncated]" not in msg
    _assert_clean_provider_error(exc_info.value)


def _post_classified(monkeypatch, text: str, *, family: str, status: int = 400):
    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(status, text=text),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    return post_json(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {HEADER_CANARY}"},
        payload={"response_format": {"type": "json_schema"}},
        provider_id="openai",
        structured_output_family=family,
    )


def test_openai_unknown_response_format_is_unsupported(monkeypatch, caplog):
    body = json.dumps(
        {
            "error": {
                "message": f"Unknown parameter: 'response_format'. {BODY_CANARY}",
                "type": "invalid_request_error",
                "param": "response_format",
                "code": "unknown_parameter",
            }
        }
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(StructuredOutputNotSupportedError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    msg = str(exc_info.value)
    _assert_no_canaries(msg)
    _assert_clean_provider_error(exc_info.value)
    assert "native structured output unsupported" in msg
    assert BODY_CANARY not in caplog.text


def test_openai_model_does_not_support_json_schema(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "message": "This model does not support response_format of type json_schema.",
                "param": "response_format",
            }
        }
    )
    with pytest.raises(StructuredOutputNotSupportedError):
        _post_classified(monkeypatch, body, family="openai")


@pytest.mark.parametrize(
    "body",
    [
        json.dumps(
            {
                "error": {
                    "code": "invalid_json_schema",
                    "message": "Invalid schema for response_format['json_schema']",
                    "param": "response_format",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": "Invalid schema for response_format['json_schema']: minLength",
                    "param": "response_format",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": "temperature too large when using response_format",
                    "param": "temperature",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": (
                        "Unknown parameter: 'temperature' when using "
                        f"response_format. {BODY_CANARY}"
                    ),
                    "param": "temperature",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": (
                        f"The keyword minLength is not supported for json_schema. "
                        f"{BODY_CANARY}"
                    ),
                    "param": "response_format",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": "Unsupported keyword minLength in json_schema",
                    "param": "response_format",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": (
                        "Invalid property at response_format.json_schema.schema"
                    ),
                    "param": "response_format",
                }
            }
        ),
        json.dumps(
            {
                "error": {
                    "message": "invalid model",
                    "request": {
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {"name": "tads_findings"},
                        }
                    },
                }
            }
        ),
        json.dumps({"error": {"message": "invalid model"}}),
    ],
)
def test_openai_invalid_or_ambiguous_schema_errors_fail_closed(monkeypatch, body):
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    assert "HTTP 400" in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))


def test_openai_unrelated_param_mentioning_response_format_fails_closed(
    monkeypatch, caplog
):
    body = json.dumps(
        {
            "error": {
                "message": (
                    "Unknown parameter: 'temperature' when using "
                    f"response_format. {BODY_CANARY}"
                ),
                "param": "temperature",
            }
        }
    )
    caplog.set_level(logging.DEBUG)
    assert classify_native_structured_output_error(body, family="openai") is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))
    assert BODY_CANARY not in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        "The keyword {keyword} is not supported for json_schema",
        "Unsupported keyword {keyword} in json_schema",
        "Unknown keyword {keyword} in response_format.json_schema",
    ],
)
@pytest.mark.parametrize(
    "keyword",
    OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS + ("additionalProperties",),
)
def test_openai_schema_keyword_errors_fail_closed(monkeypatch, message, keyword):
    body = json.dumps(
        {
            "error": {
                "message": f"{message.format(keyword=keyword)}. {BODY_CANARY}",
                "param": "response_format",
            }
        }
    )
    assert classify_native_structured_output_error(body, family="openai") is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))


def test_openai_invalid_property_at_schema_path_fails_closed(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "message": (
                    "Invalid property at response_format.json_schema.schema. "
                    f"{BODY_CANARY}"
                ),
                "param": "response_format",
            }
        }
    )
    assert classify_native_structured_output_error(body, family="openai") is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    assert type(exc_info.value) is RuntimeError
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))


def test_gemini_unknown_response_schema_field_is_unsupported(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "message": (
                    "Invalid JSON payload received. Unknown name "
                    '"responseSchema" at \'generation_config\': Cannot find field.'
                ),
                "status": "INVALID_ARGUMENT",
            }
        }
    )
    with pytest.raises(StructuredOutputNotSupportedError):
        _post_classified(monkeypatch, body, family="gemini")


@pytest.mark.parametrize(
    "message",
    [
        "Invalid JSON payload received. Unknown name \"additionalProperties\" "
        "at 'generation_config.response_schema': Cannot find field.",
        "Keyword additionalProperties is not supported in responseSchema",
        "The keyword minLength is not supported for json_schema",
        "Invalid schema at responseSchema",
        "INVALID_ARGUMENT involving responseSchema",
        "generic invalid argument",
    ],
)
def test_gemini_invalid_or_ambiguous_schema_errors_fail_closed(monkeypatch, message):
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "message": f"{message} {BODY_CANARY}",
                "status": "INVALID_ARGUMENT",
            }
        }
    )
    assert classify_native_structured_output_error(body, family="gemini") is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="gemini")
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))


def test_gemini_unrelated_param_mentioning_response_schema_fails_closed(
    monkeypatch, caplog
):
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "message": (
                    "Unknown parameter: 'temperature' when using "
                    f"responseSchema. {BODY_CANARY}"
                ),
                "param": "temperature",
                "status": "INVALID_ARGUMENT",
            }
        }
    )
    caplog.set_level(logging.DEBUG)
    assert classify_native_structured_output_error(body, family="gemini") is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="gemini")
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)
    _assert_clean_provider_error(exc_info.value)
    _assert_no_canaries(str(exc_info.value))
    assert BODY_CANARY not in caplog.text


def test_classification_phrase_beyond_bound_does_not_fallback(monkeypatch):
    phrase = (
        '{"error":{"message":"Unknown parameter: \'response_format\'.",'
        '"param":"response_format"}}'
    )
    body = (" " * (UNSUPPORTED_FEATURE_BODY_CHARS + 8)) + phrase
    assert classify_native_structured_output_error(
        body[:UNSUPPORTED_FEATURE_BODY_CHARS], family="openai"
    ) is False
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai")
    assert type(exc_info.value) is RuntimeError


def test_http_400_schema_tokens_ignored_when_not_requested(monkeypatch):
    class Client(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(
                lambda: _FakeResponse(
                    400,
                    text=json.dumps(
                        {
                            "error": {
                                "message": "Unknown parameter: 'response_format'.",
                                "param": "response_format",
                            }
                        }
                    ),
                ),
                *args,
                **kwargs,
            )

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(RuntimeError) as exc_info:
        post_json(
            "https://api.openai.com/v1/chat/completions",
            headers={},
            payload={"model": "x"},
            provider_id="openai",
        )
    assert type(exc_info.value) is RuntimeError
    assert "native structured output unsupported" not in str(exc_info.value)


def test_http_401_does_not_classify_as_unsupported_schema(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "message": "Unknown parameter: 'response_format'.",
                "param": "response_format",
            }
        }
    )
    with pytest.raises(RuntimeError) as exc_info:
        _post_classified(monkeypatch, body, family="openai", status=401)
    assert type(exc_info.value) is RuntimeError
    assert "HTTP 401" in str(exc_info.value)
    assert "native structured output unsupported" not in str(exc_info.value)
