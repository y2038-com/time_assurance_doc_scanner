# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Provider-agnostic LLM interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence

from tads.schemas.cost import TokenUsage


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a provider is selected but credentials/SDK are missing."""


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    raw: Optional[dict] = None


class LLMProvider(ABC):
    """Uniform interface for cloud Ollama, OpenAI, Anthropic, Gemini, etc."""

    provider_id: str
    display_name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """True if enough env/config exists to attempt a call."""

    @abstractmethod
    def default_model(self) -> str:
        """Default model id for this provider."""

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """Provider-specific or heuristic token estimate."""

    @abstractmethod
    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        """Run a chat completion. Phase 0 providers may stub this."""

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        """Best-known context window; override per provider/model."""
        _ = model
        return 128_000
