# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""OpenAI model request policy (Chat Completions).

Narrow capability table for payload construction and pricing. Local Policy A
validation remains authoritative for model output. Not a general model registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tads.errors import PublicScanError

# Official GPT-6 Luna reasoning efforts for Chat Completions.
LUNA_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)

# Input tokens at or below this threshold use short-context Luna rates.
# Above this threshold, long-context rates apply (per request).
LUNA_LONG_CONTEXT_INPUT_THRESHOLD = 272_000

GPT_4_1_MINI = "gpt-4.1-mini"
GPT_6_LUNA = "gpt-6-luna"


@dataclass(frozen=True)
class OpenAIPricingTier:
    """USD per 1M tokens. ``cached_input`` is documented but not estimated yet."""

    input: float
    output: float
    cached_input: Optional[float] = None
    long_input: Optional[float] = None
    long_output: Optional[float] = None
    long_cached_input: Optional[float] = None
    long_context_input_threshold: Optional[int] = None


@dataclass(frozen=True)
class OpenAIModelPolicy:
    """Per-model Chat Completions request and pricing policy."""

    model_id: str
    completion_token_param: str
    context_window_tokens: int
    supports_reasoning_effort: bool
    allowed_reasoning_efforts: frozenset[str]
    default_reasoning_effort: Optional[str]
    send_temperature_when_reasoning_none: bool
    pricing: Optional[OpenAIPricingTier] = None


_GPT_4_1_MINI_POLICY = OpenAIModelPolicy(
    model_id=GPT_4_1_MINI,
    completion_token_param="max_tokens",
    context_window_tokens=1_000_000,
    supports_reasoning_effort=False,
    allowed_reasoning_efforts=frozenset(),
    default_reasoning_effort=None,
    send_temperature_when_reasoning_none=True,
    pricing=OpenAIPricingTier(input=0.40, output=1.60),
)

_GPT_6_LUNA_POLICY = OpenAIModelPolicy(
    model_id=GPT_6_LUNA,
    completion_token_param="max_completion_tokens",
    context_window_tokens=1_050_000,
    supports_reasoning_effort=True,
    allowed_reasoning_efforts=LUNA_REASONING_EFFORTS,
    default_reasoning_effort="none",
    send_temperature_when_reasoning_none=True,
    pricing=OpenAIPricingTier(
        input=0.10,
        output=0.50,
        cached_input=0.01,
        long_input=0.20,
        long_output=0.75,
        long_cached_input=0.02,
        long_context_input_threshold=LUNA_LONG_CONTEXT_INPUT_THRESHOLD,
    ),
)

_POLICIES: dict[str, OpenAIModelPolicy] = {
    GPT_4_1_MINI: _GPT_4_1_MINI_POLICY,
    GPT_6_LUNA: _GPT_6_LUNA_POLICY,
}

# Default OpenAI model when none is selected.
DEFAULT_OPENAI_MODEL = GPT_4_1_MINI


class InvalidReasoningEffortError(PublicScanError, ValueError):
    """Controlled failure for an unsupported or unknown reasoning effort."""


def openai_model_policy(model: str) -> OpenAIModelPolicy:
    """Return the policy for a known OpenAI model, or a GPT-4.1 Mini-compatible default."""
    key = (model or "").strip().lower()
    if key in _POLICIES:
        return _POLICIES[key]
    # Unknown OpenAI models keep the pre-Luna Chat Completions shape.
    return OpenAIModelPolicy(
        model_id=key or DEFAULT_OPENAI_MODEL,
        completion_token_param="max_tokens",
        context_window_tokens=1_000_000,
        supports_reasoning_effort=False,
        allowed_reasoning_efforts=frozenset(),
        default_reasoning_effort=None,
        send_temperature_when_reasoning_none=True,
        pricing=None,
    )


def resolve_reasoning_effort(
    policy: OpenAIModelPolicy,
    requested: Optional[str],
) -> Optional[str]:
    """Resolve and validate reasoning effort for a request.

    Returns the effort string to send, or ``None`` when the field must be omitted.
    """
    if requested is not None:
        token = str(requested).strip().casefold()
        if not token:
            raise InvalidReasoningEffortError("reasoning_effort must be nonempty")
        if not policy.supports_reasoning_effort:
            raise InvalidReasoningEffortError(
                f"reasoning_effort is not supported for model {policy.model_id!r}"
            )
        if token not in policy.allowed_reasoning_efforts:
            allowed = ", ".join(sorted(policy.allowed_reasoning_efforts))
            raise InvalidReasoningEffortError(
                f"unsupported reasoning_effort {token!r}; allowed: {allowed}"
            )
        return token
    if policy.supports_reasoning_effort:
        return policy.default_reasoning_effort
    return None


def rates_for_input_tokens(
    pricing: OpenAIPricingTier, input_tokens: int
) -> tuple[float, float]:
    """Return (input_rate, output_rate) for one request's input size."""
    threshold = pricing.long_context_input_threshold
    if (
        threshold is not None
        and pricing.long_input is not None
        and pricing.long_output is not None
        and input_tokens > threshold
    ):
        return pricing.long_input, pricing.long_output
    return pricing.input, pricing.output


def recorded_reasoning_effort(
    *,
    provider_id: str,
    model: str,
    requested: Optional[str],
) -> Optional[str]:
    """Validate and return the effective effort to record in RunMetadata.

    OpenAI reasoning models return the normalized effective value (Luna default
    ``none`` when unset). Non-reasoning providers and models return ``None``
    after rejecting any explicit effort.
    """
    from tads.llm.base import reject_unsupported_reasoning_effort

    provider_key = (provider_id or "").strip().lower()
    if provider_key != "openai":
        reject_unsupported_reasoning_effort(requested)
        return None
    return resolve_reasoning_effort(openai_model_policy(model), requested)
