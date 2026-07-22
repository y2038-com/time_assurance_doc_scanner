"""LLM provider registry."""

from tads.llm.base import LLMProvider
from tads.llm.providers.anthropic_provider import AnthropicProvider
from tads.llm.providers.gemini_provider import GeminiProvider
from tads.llm.providers.mock_provider import MockProvider
from tads.llm.providers.ollama_provider import OllamaProvider
from tads.llm.providers.openai_provider import OpenAIProvider

_PROVIDERS: dict[str, LLMProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
    "ollama": OllamaProvider(),
    "mock": MockProvider(),
}


def get_provider(provider_id: str) -> LLMProvider:
    key = provider_id.lower().strip()
    aliases = {"google": "gemini", "cloud-ollama": "ollama"}
    key = aliases.get(key, key)
    try:
        return _PROVIDERS[key]
    except KeyError as exc:
        known = ", ".join(sorted(_PROVIDERS))
        raise KeyError(f"Unknown provider '{provider_id}'. Known: {known}") from exc


def list_providers() -> list[str]:
    return sorted(_PROVIDERS)


def register_provider(provider: LLMProvider) -> None:
    """Replace or add a provider instance (useful in tests)."""
    _PROVIDERS[provider.provider_id] = provider
