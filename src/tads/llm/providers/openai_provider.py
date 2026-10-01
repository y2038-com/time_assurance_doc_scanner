# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""OpenAI Chat Completions provider."""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from tads.llm.base import (
    DEFAULT_LLM_TEMPERATURE,
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderNotConfiguredError,
)
from tads.llm.env import default_model_id
from tads.llm.http import (
    StructuredOutputNotSupportedError,
    format_shape_error,
    post_json,
)
from tads.llm.providers import heuristic_token_count
from tads.llm.structured_output import (
    official_openai_chat_completions_base,
    openai_response_format,
)
from tads.schemas.cost import TokenUsage


class OpenAIProvider(LLMProvider):
    provider_id = "openai"
    display_name = "OpenAI"

    def __init__(self) -> None:
        self._native_structured_output_disabled = False

    def is_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def default_model(self) -> str:
        return default_model_id() or "gpt-4.1-mini"

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        model_id = (model or self.default_model()).lower()
        if "mini" in model_id:
            return 1_000_000
        return 1_000_000

    def supports_native_structured_output(self, *, model: Optional[str] = None) -> bool:
        """Endpoint-level capability for official Chat Completions.

        ``model`` is unused today. Custom ``OPENAI_BASE_URL`` stays on the
        prompt-only path. An instance that already observed an unsupported
        feature does not probe again.
        """
        _ = model
        if self._native_structured_output_disabled:
            return False
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return official_openai_chat_completions_base(base)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError(
                "OpenAI is not configured. Set OPENAI_API_KEY."
            )
        model_id = model or self.default_model()
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_output_tokens,
            "temperature": DEFAULT_LLM_TEMPERATURE,
        }
        native = self.supports_native_structured_output(model=model_id)
        if native:
            payload["response_format"] = openai_response_format()
        data = self._post_json_with_native_fallback(
            url,
            headers=headers,
            payload=payload,
            native=native,
        )
        shape_error = False
        content = ""
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            shape_error = True
        if shape_error:
            raise RuntimeError(format_shape_error(self.display_name))
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)

    def _post_json_with_native_fallback(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        native: bool,
    ) -> dict[str, Any]:
        try:
            return post_json(
                url,
                headers=headers,
                payload=payload,
                provider_id=self.provider_id,
                structured_output_family="openai" if native else None,
            )
        except StructuredOutputNotSupportedError:
            if not native:
                raise
            self._native_structured_output_disabled = True
            fallback = {
                key: value
                for key, value in payload.items()
                if key != "response_format"
            }
            return post_json(
                url,
                headers=headers,
                payload=fallback,
                provider_id=self.provider_id,
            )
