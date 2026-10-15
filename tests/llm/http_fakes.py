# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared HTTP fakes for llm provider tests (same-directory import only).

Loaded as ``http_fakes`` when pytest prepends ``tests/llm`` for modules in
this directory. Does not require repository-root ``pythonpath``.
"""

from __future__ import annotations

import json

import httpx

BODY_CANARY = "PROVIDER_BODY_CANARY_xyz"
DOC_CANARY = "DOCUMENT_TEXT_CANARY_xyz"
TOKEN_CANARY = "sk-abcdefghijklmnopqrstuvwxyz012345"
HEADER_CANARY = "HDR_AUTH_CANARY_xyz"
QUERY_CANARY = "QUERY_CANARY_xyz"
USERINFO_CANARY = "USERINFO_CANARY_xyz"
CTRL_CANARY = "before\x00after"


def walk_exceptions(exc: BaseException) -> list[BaseException]:
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


def assert_clean_provider_error(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    for item in walk_exceptions(exc):
        assert item.__cause__ is None
        assert item.__context__ is None
        for name, value in list(getattr(item, "__dict__", {}).items()):
            assert not isinstance(value, (httpx.Request, httpx.Response, httpx.HTTPError))
            if name in {"request", "response"}:
                assert value is None
        assert not isinstance(getattr(item, "request", None), httpx.Request)
        assert not isinstance(getattr(item, "response", None), httpx.Response)


def assert_no_canaries(text: str) -> None:
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


class FakeResponse:
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
        self._text = text
        self.headers = {"Authorization": f"Bearer {HEADER_CANARY}"}
        self.request = httpx.Request(
            "POST",
            f"https://{USERINFO_CANARY}:pw@api.example/v1?token={QUERY_CANARY}",
        )
        self._payload = payload
        self._json_error = json_error
        self.url = url

    @property
    def text(self) -> str:
        if self._text:
            return self._text
        if self._payload is not None:
            return json.dumps(self._payload)
        return ""

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeClient:
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


# Underscore aliases match prior test_http private names used by callers.
_FakeResponse = FakeResponse
_FakeClient = FakeClient
_assert_clean_provider_error = assert_clean_provider_error
_assert_no_canaries = assert_no_canaries
_walk_exceptions = walk_exceptions
