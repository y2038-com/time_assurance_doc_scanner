"""OpenAI provider stub (Phase 0 interface; live calls in Phase 1)."""

from __future__ import annotations

import os
from typing import Optional, Sequence

from tads.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderNotConfiguredError,
)
from tads.llm.providers import heuristic_token_count


class OpenAIProvider(LLMProvider):
    provider_id = "openai"
    display_name = "OpenAI"

    def is_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def default_model(self) -> str:
        return os.getenv("TADS_MODEL", "gpt-4.1-mini")

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "OpenAI is not configured. Set OPENAI_API_KEY."
            )
        raise ProviderNotConfiguredError(
            "OpenAI live completion is implemented in Phase 1. "
            f"Requested model={model or self.default_model()}, "
            f"messages={len(messages)}, max_output_tokens={max_output_tokens}."
        )
