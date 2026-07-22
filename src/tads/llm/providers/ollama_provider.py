"""Ollama chat API provider (local or cloud)."""

from __future__ import annotations

import os
from typing import Optional, Sequence

from tads.llm.base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderNotConfiguredError,
)
from tads.llm.http import post_json
from tads.llm.providers import heuristic_token_count
from tads.schemas.cost import TokenUsage


class OllamaProvider(LLMProvider):
    provider_id = "ollama"
    display_name = "Ollama"

    def is_configured(self) -> bool:
        host = self._host().lower()
        if "ollama.com" in host:
            return bool(os.getenv("OLLAMA_API_KEY"))
        return True

    def default_model(self) -> str:
        return os.getenv("TADS_MODEL", "llama3.1")

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        _ = model
        # Cloud models often expose large windows; keep generous for planning.
        if "ollama.com" in self._host().lower():
            return 128_000
        return 128_000

    def _host(self) -> str:
        return os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "Ollama Cloud is not configured. Set OLLAMA_HOST=https://ollama.com "
                "and OLLAMA_API_KEY."
            )
        model_id = model or self.default_model()
        headers = {"Content-Type": "application/json"}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        data = post_json(
            f"{self._host()}/api/chat",
            headers=headers,
            payload={
                "model": model_id,
                "stream": False,
                "messages": [
                    {"role": m.role, "content": m.content} for m in messages
                ],
                "options": {"num_predict": max_output_tokens, "temperature": 0.2},
            },
        )
        try:
            content = (data.get("message") or {}).get("content") or ""
        except AttributeError as exc:
            raise RuntimeError(f"Unexpected Ollama response shape: {data}") from exc
        usage = TokenUsage(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)
