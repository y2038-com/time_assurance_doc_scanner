# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Provider capability for native structured output."""

from __future__ import annotations

from tads.llm.providers.anthropic_provider import AnthropicProvider
from tads.llm.providers.gemini_provider import GeminiProvider
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.providers.ollama_provider import OllamaProvider
from tads.llm.providers.openai_provider import OpenAIProvider
from tads.llm.registry import get_provider, list_providers, register_provider
from tads.llm.structured_output import official_openai_chat_completions_base


def test_official_openai_and_gemini_advertise_native_structured_output(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    register_provider(OpenAIProvider())
    register_provider(GeminiProvider())
    assert OpenAIProvider().supports_native_structured_output() is True
    assert OpenAIProvider().supports_native_structured_output(model="any-name") is True
    assert GeminiProvider().supports_native_structured_output() is True
    assert get_provider("openai").supports_native_structured_output() is True
    assert get_provider("gemini").supports_native_structured_output() is True
    assert get_provider("google").supports_native_structured_output() is True


def test_custom_openai_base_url_stays_on_prompt_only_path(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    assert official_openai_chat_completions_base("https://openrouter.ai/api/v1") is False
    assert OpenAIProvider().supports_native_structured_output() is False
    assert official_openai_chat_completions_base("https://api.openai.com/v1") is True
    assert official_openai_chat_completions_base("") is True


def test_ollama_anthropic_and_mock_do_not_enable_native_structured_output():
    assert OllamaProvider().supports_native_structured_output() is False
    assert AnthropicProvider().supports_native_structured_output() is False
    assert MockProvider().supports_native_structured_output() is False
    for provider_id in ("ollama", "anthropic", "mock"):
        assert get_provider(provider_id).supports_native_structured_output() is False


def test_registry_providers_do_not_accidentally_inherit_openai_support(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    register_provider(OpenAIProvider())
    register_provider(GeminiProvider())
    supported = {
        pid
        for pid in list_providers()
        if get_provider(pid).supports_native_structured_output()
    }
    assert supported == {"gemini", "openai"}
