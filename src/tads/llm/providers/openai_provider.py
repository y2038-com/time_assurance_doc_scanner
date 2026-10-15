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
    ModelOutputTruncatedError,
    ProviderNotConfiguredError,
)
from tads.llm.env import default_model_id
from tads.llm.http import (
    StructuredOutputNotSupportedError,
    format_provider_output_rejected,
    format_shape_error,
    format_unexpected_output_type,
    post_json,
)
from tads.llm.openai_models import (
    DEFAULT_OPENAI_MODEL,
    InvalidReasoningEffortError,
    openai_model_policy,
    resolve_reasoning_effort,
)
from tads.llm.providers import heuristic_token_count
from tads.llm.structured_output import (
    official_openai_chat_completions_base,
    openai_response_format,
)
from tads.schemas.cost import TokenUsage

def build_openai_chat_payload(
    *,
    model_id: str,
    messages: Sequence[ChatMessage],
    max_output_tokens: int,
    reasoning_effort: Optional[str] = None,
    attach_structured_output: bool = False,
) -> dict[str, Any]:
    """Build a Chat Completions JSON body for ``model_id``.

    Validates reasoning effort before any HTTP call. Raises
    ``InvalidReasoningEffortError`` for unsupported values.
    """
    policy = openai_model_policy(model_id)
    effort = resolve_reasoning_effort(policy, reasoning_effort)
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        policy.completion_token_param: max_output_tokens,
    }
    if effort is not None:
        payload["reasoning_effort"] = effort
    send_temperature = policy.send_temperature_when_reasoning_none and (
        effort is None or effort == "none"
    )
    if send_temperature:
        payload["temperature"] = DEFAULT_LLM_TEMPERATURE
    if attach_structured_output:
        payload["response_format"] = openai_response_format()
    return payload


class OpenAIProvider(LLMProvider):
    provider_id = "openai"
    display_name = "OpenAI"

    def __init__(self) -> None:
        self._native_structured_output_disabled = False

    def is_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def default_model(self) -> str:
        return default_model_id() or DEFAULT_OPENAI_MODEL

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        return openai_model_policy(model or self.default_model()).context_window_tokens

    def supports_native_structured_output(self, *, model: Optional[str] = None) -> bool:
        """Endpoint-level capability for official Chat Completions.

        ``model`` is unused for capability today (Luna and GPT-4.1 Mini both
        support structured outputs on the official host). Custom
        ``OPENAI_BASE_URL`` stays on the prompt-only path. An instance that
        already observed an unsupported feature does not probe again.
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
        reasoning_effort: Optional[str] = None,
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
        native = self.supports_native_structured_output(model=model_id)
        try:
            payload = build_openai_chat_payload(
                model_id=model_id,
                messages=messages,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
                attach_structured_output=native,
            )
        except InvalidReasoningEffortError:
            raise
        data = self._post_json_with_native_fallback(
            url,
            headers=headers,
            payload=payload,
            native=native,
        )
        content = _extract_openai_message_text(data, display_name=self.display_name)
        usage = _token_usage_from_openai(
            data.get("usage"), display_name=self.display_name
        )
        return LLMResponse(content=content, usage=usage, model=model_id, raw=None)

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


def _extract_openai_message_text(data: dict[str, Any], *, display_name: str) -> str:
    """Return assistant message text, or raise a controlled shape/truncation error."""
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(format_shape_error(display_name))
    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError(format_shape_error(display_name))

    # Non-streaming Chat Completions: require an explicit terminal finish_reason.
    # Do not echo finish_reason values, content, or refusals in ordinary errors.
    finish_reason = first.get("finish_reason")
    if finish_reason == "length":
        raw = _message_content_for_raw(first.get("message"))
        raise ModelOutputTruncatedError(
            "Model output truncated: completion token limit reached",
            raw=raw,
        )
    if finish_reason == "content_filter":
        raise RuntimeError(format_provider_output_rejected(display_name))
    if finish_reason in ("tool_calls", "function_call"):
        raise RuntimeError(format_unexpected_output_type(display_name))
    if finish_reason != "stop":
        # Missing, null, or unrecognized — fail closed without leaking the value.
        raise RuntimeError(format_shape_error(display_name))

    message = first.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(format_shape_error(display_name))
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise RuntimeError(format_shape_error(display_name))
    content = message.get("content")
    if content is None or not isinstance(content, str):
        raise RuntimeError(format_shape_error(display_name))
    return content


def _message_content_for_raw(message: object) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _token_usage_from_openai(
    usage_raw: object, *, display_name: str
) -> TokenUsage:
    """Map Chat Completions usage; capture optional reasoning_tokens once."""
    if not isinstance(usage_raw, dict):
        return TokenUsage()
    try:
        input_tokens = _nonneg_int(usage_raw.get("prompt_tokens"), default=0)
        output_tokens = _nonneg_int(usage_raw.get("completion_tokens"), default=0)
    except ValueError:
        raise RuntimeError(format_shape_error(display_name)) from None

    reasoning_tokens: Optional[int] = None
    details = usage_raw.get("completion_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        try:
            reasoning_tokens = _nonneg_int(
                details.get("reasoning_tokens"), default=None
            )
        except ValueError:
            raise RuntimeError(format_shape_error(display_name)) from None

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _nonneg_int(value: object, *, default: Optional[int]) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid token count")
    if value < 0:
        raise ValueError("negative token count")
    return value
