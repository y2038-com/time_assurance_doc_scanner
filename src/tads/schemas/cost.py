"""Cost and token usage schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CostBudget(BaseModel):
    """Hard caps for a scan job. Fail closed if estimates exceed these."""

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
