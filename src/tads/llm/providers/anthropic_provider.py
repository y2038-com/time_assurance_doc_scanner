# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Anthropic Messages API provider."""

from __future__ import annotations

import os
from typing import Optional, Sequence

from tads.llm.base import (
    DEFAULT_LLM_TEMPERATURE,
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderNotConfiguredError,
)
from tads.llm.env import default_model_id
from tads.llm.http import post_json, sanitize_provider_error_body
from tads.llm.providers import heuristic_token_count
from tads.schemas.cost import TokenUsage


class AnthropicProvider(LLMProvider):
    provider_id = "anthropic"
    display_name = "Anthropic"

    def is_configured(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def default_model(self) -> str:
        return default_model_id() or "claude-sonnet-4-5"

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        _ = model
        return 200_000

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError(
                "Anthropic is not configured. Set ANTHROPIC_API_KEY."
            )
        model_id = model or self.default_model()
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in {"user", "assistant"}
        ]
        payload: dict = {
            "model": model_id,
            "max_tokens": max_output_tokens,
            "temperature": DEFAULT_LLM_TEMPERATURE,
            "messages": chat_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        data = post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload=payload,
        )
        try:
            blocks = data.get("content") or []
            content = "".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            )
        except (TypeError, AttributeError) as exc:
            raise RuntimeError(
                "Unexpected Anthropic response shape: "
                f"{sanitize_provider_error_body(data)}"
            ) from exc
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_raw.get("input_tokens") or 0),
            output_tokens=int(usage_raw.get("output_tokens") or 0),
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=data)
