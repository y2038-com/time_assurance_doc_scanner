"""Google Gemini generateContent provider."""

from __future__ import annotations

import os
from typing import Optional, Sequence
from urllib.parse import urlencode

from tads.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderNotConfiguredError,
)
from tads.llm.env import default_model_id
from tads.llm.http import post_json
from tads.llm.providers import heuristic_token_count
from tads.schemas.cost import TokenUsage


class GeminiProvider(LLMProvider):
    provider_id = "gemini"
    display_name = "Google Gemini"

    def is_configured(self) -> bool:
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

    def default_model(self) -> str:
        return default_model_id() or "gemini-2.5-flash"

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        _ = model
        return 1_000_000

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
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens,
            },
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
        data = post_json(url, headers={"Content-Type": "application/json"}, payload=payload)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc
        usage_raw = data.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("promptTokenCount") or 0),
            output_tokens=int(usage_raw.get("candidatesTokenCount") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)
