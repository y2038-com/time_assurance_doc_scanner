# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Google Gemini generateContent provider."""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence
from urllib.parse import urlencode

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
from tads.llm.structured_output import gemini_response_schema
from tads.schemas.cost import TokenUsage


class GeminiProvider(LLMProvider):
    provider_id = "gemini"
    display_name = "Google Gemini"

    def __init__(self) -> None:
        self._native_structured_output_disabled = False

    def is_configured(self) -> bool:
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

    def default_model(self) -> str:
        return default_model_id() or "gemini-3.6-flash"

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        _ = model
        return 1_000_000

    def supports_native_structured_output(self, *, model: Optional[str] = None) -> bool:
        """Endpoint-level capability for Gemini ``generateContent``.

        ``model`` is unused today. An instance that already observed an
        unsupported feature does not probe again.
        """
        _ = model
        return not self._native_structured_output_disabled

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError(
                "Gemini is not configured. Set GOOGLE_API_KEY or GEMINI_API_KEY."
            )
        model_id = model or self.default_model()
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = []
        for message in messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})
        generation_config: dict[str, Any] = {
            "temperature": DEFAULT_LLM_TEMPERATURE,
            "maxOutputTokens": max_output_tokens,
        }
        native = self.supports_native_structured_output(model=model_id)
        if native:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = gemini_response_schema()
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        query = urlencode({"key": api_key})
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?{query}"
        )
        data = self._post_json_with_generation_fallback(
            url,
            headers={"Content-Type": "application/json"},
            payload=payload,
            native=native,
        )
        shape_error = False
        content = ""
        try:
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            shape_error = True
        if shape_error:
            raise RuntimeError(format_shape_error(self.display_name))
        usage_raw = data.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("promptTokenCount") or 0),
            output_tokens=int(usage_raw.get("candidatesTokenCount") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)

    def _post_json_with_generation_fallback(
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
                structured_output_family="gemini" if native else None,
            )
        except StructuredOutputNotSupportedError:
            if not native:
                raise
            self._native_structured_output_disabled = True
            generation = dict(payload.get("generationConfig") or {})
            generation.pop("responseMimeType", None)
            generation.pop("responseSchema", None)
            fallback = dict(payload)
            fallback["generationConfig"] = generation
            return post_json(
                url,
                headers=headers,
                payload=fallback,
                provider_id=self.provider_id,
            )
