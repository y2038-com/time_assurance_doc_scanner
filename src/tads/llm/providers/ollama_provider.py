"""Cloud Ollama provider stub (Phase 0 interface; live calls in Phase 1)."""

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


class OllamaProvider(LLMProvider):
    provider_id = "ollama"
    display_name = "Cloud Ollama"

    def is_configured(self) -> bool:
        # Cloud Ollama may use OLLAMA_API_KEY; local host alone is also ok later.
        return bool(os.getenv("OLLAMA_HOST") or os.getenv("OLLAMA_API_KEY"))

    def default_model(self) -> str:
        return os.getenv("TADS_MODEL", "llama3.1")

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
                "Ollama is not configured. Set OLLAMA_HOST and/or OLLAMA_API_KEY."
            )
        raise ProviderNotConfiguredError(
            "Ollama live completion is implemented in Phase 1. "
            f"Requested model={model or self.default_model()}, "
            f"messages={len(messages)}, max_output_tokens={max_output_tokens}."
        )
