"""Anthropic provider stub (Phase 0 interface; live calls in Phase 1)."""

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


class AnthropicProvider(LLMProvider):
    provider_id = "anthropic"
    display_name = "Anthropic"

    def is_configured(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def default_model(self) -> str:
        return os.getenv("TADS_MODEL", "claude-sonnet-4-5")

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
                "Anthropic is not configured. Set ANTHROPIC_API_KEY."
            )
        raise ProviderNotConfiguredError(
            "Anthropic live completion is implemented in Phase 1. "
            f"Requested model={model or self.default_model()}, "
            f"messages={len(messages)}, max_output_tokens={max_output_tokens}."
        )
