"""OpenAI Chat Completions provider."""

from __future__ import annotations

import os
from typing import Optional, Sequence

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


class OpenAIProvider(LLMProvider):
    provider_id = "openai"
    display_name = "OpenAI"

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
        data = post_json(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": model_id,
                "messages": [
                    {"role": m.role, "content": m.content} for m in messages
                ],
                "max_tokens": max_output_tokens,
                "temperature": 0.2,
            },
        )
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected OpenAI response shape: {data}") from exc
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)
