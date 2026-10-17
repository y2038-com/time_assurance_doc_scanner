# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Provider-agnostic LLM interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence

from tads.errors import PublicScanError
from tads.schemas.cost import TokenUsage


class ProviderNotConfiguredError(PublicScanError, RuntimeError):
    """Raised when a provider is selected but credentials/SDK are missing."""


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


# Temperature TADS sends on provider completions. Recorded in RunMetadata.
# Low temperature improves determinism but does not guarantee identical repeats.
DEFAULT_LLM_TEMPERATURE = 0.2


def reject_unsupported_reasoning_effort(reasoning_effort: Optional[str]) -> None:
    """Non-reasoning providers reject an explicit reasoning_effort before HTTP."""
    if reasoning_effort is None:
        return
    token = str(reasoning_effort).strip()
    if not token:
        from tads.llm.openai_models import InvalidReasoningEffortError

        raise InvalidReasoningEffortError("reasoning_effort must be nonempty")
    from tads.llm.openai_models import InvalidReasoningEffortError

    raise InvalidReasoningEffortError(
        "reasoning_effort is only supported for OpenAI models that advertise it"
    )


class ModelOutputTruncatedError(PublicScanError, RuntimeError):
    """Model hit the completion-token limit (finish_reason=length).

    ``raw`` is for opt-in raw-on-error persistence only. Never include it in
    ``str(exc)``, logs, or progress output. Message text is public-CLI safe.
    """

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


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
        reasoning_effort: Optional[str] = None,
    ) -> LLMResponse:
        """Run a chat completion.

        ``reasoning_effort`` is OpenAI-specific (e.g. GPT-6 Luna). Other
        providers ignore a ``None`` value and reject a nonempty value.
        """

    def supports_native_structured_output(self, *, model: Optional[str] = None) -> bool:
        """True when this provider attaches a native JSON schema on ``complete``.

        Default is false. Call sites must use this method rather than inferring
        from ``provider_id``. Capability is endpoint-level where implemented
        (official OpenAI Chat Completions; Gemini ``generateContent``). ``model``
        is reserved and unused. Native schema is a reliability aid, not a
        security boundary; local parsing remains authoritative.
        """
        _ = model
        return False

    def context_window_tokens(self, model: Optional[str] = None) -> int:
        """Best-known context window; override per provider/model."""
        _ = model
        return 128_000
