# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Cost and token usage schemas."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    # Optional OpenAI completion_tokens_details.reasoning_tokens (schema 0.4.0).
    # Included in output_tokens / completion_tokens; never add a second time for cost.
    reasoning_tokens: Optional[int] = Field(default=None, ge=0)

    @field_validator("input_tokens", "output_tokens", "reasoning_tokens", mode="before")
    @classmethod
    def _reject_bool_and_unsafe(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("token counts must be integers, not booleans")
        return value

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CostBudget(BaseModel):
    """Estimate-based preflight guards for a scan job (not a hard billing cap)."""

    max_cost_usd: Optional[float] = Field(default=None, ge=0)
    max_tokens: Optional[int] = Field(default=None, ge=0)


class CostEstimate(BaseModel):
    """Preflight estimate shown to the user before submitting LLM work."""

    provider: str
    model: str
    estimated_input_tokens: int = Field(ge=0)
    estimated_output_tokens: int = Field(ge=0)
    estimated_cost_usd: Optional[float] = Field(
        default=None,
        description="None when pricing for the model is unknown.",
    )
    currency: str = "USD"
    within_budget: bool = True
    notes: list[str] = Field(default_factory=list)

    @property
    def estimated_total_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens
