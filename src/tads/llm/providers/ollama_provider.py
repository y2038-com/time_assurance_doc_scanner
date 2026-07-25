# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

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
from tads.llm.env import default_model_id
from tads.llm.http import post_json
from tads.llm.providers import heuristic_token_count
from tads.schemas.cost import TokenUsage

# Cloud-first: free-tier friendly default when OLLAMA_HOST is unset.
_DEFAULT_CLOUD_HOST = "https://ollama.com"
_DEFAULT_LOCAL_HOST = "http://127.0.0.1:11434"
# Free-tier accessible on Ollama Cloud (larger/:cloud models often need a subscription).
_DEFAULT_CLOUD_MODEL = "gpt-oss:120b"
_DEFAULT_LOCAL_MODEL = "llama3.1"


class OllamaProvider(LLMProvider):
    provider_id = "ollama"
    display_name = "Ollama"

    def is_configured(self) -> bool:
        if self._is_cloud():
            return bool(os.getenv("OLLAMA_API_KEY"))
        return True

    def default_model(self) -> str:
        configured = default_model_id()
        if configured:
            return configured
        if self._is_cloud():
            return _DEFAULT_CLOUD_MODEL
        return _DEFAULT_LOCAL_MODEL

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        model_id = (model or self.default_model()).lower()
        if "deepseek-v4" in model_id or "1m" in model_id:
            return 1_000_000
        if "gpt-oss" in model_id or "nemotron" in model_id:
            return 128_000
        if self._is_cloud():
            return 128_000
        return 128_000

    def _host(self) -> str:
        raw = (os.getenv("OLLAMA_HOST") or "").strip()
        if not raw:
            return _DEFAULT_CLOUD_HOST
        return raw.rstrip("/")

    def _is_cloud(self) -> bool:
        return "ollama.com" in self._host().lower()

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                "Ollama Cloud is not configured. Set OLLAMA_API_KEY "
                f"(default host is {_DEFAULT_CLOUD_HOST}). "
                f"For a local daemon use OLLAMA_HOST={_DEFAULT_LOCAL_HOST}."
            )
        model_id = model or self.default_model()
        headers = {"Content-Type": "application/json"}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
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
        except RuntimeError as exc:
            message = str(exc)
            if "404" in message and "not found" in message.lower():
                hint = (
                    f"Model {model_id!r} was not found at {self._host()}. "
                    "For Ollama Cloud leave OLLAMA_HOST unset (defaults to "
                    f"{_DEFAULT_CLOUD_HOST}) and use a cloud model "
                    f"(default {_DEFAULT_CLOUD_MODEL!r}). "
                    f"For local Ollama: ollama pull {model_id} "
                    f"and OLLAMA_HOST={_DEFAULT_LOCAL_HOST}."
                )
                raise RuntimeError(f"{message}\n{hint}") from exc
            raise
        try:
            content = (data.get("message") or {}).get("content") or ""
        except AttributeError as exc:
            raise RuntimeError(f"Unexpected Ollama response shape: {data}") from exc
        usage = TokenUsage(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)
