"""Deterministic mock provider for tests and dry integration."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from tads.llm.base import ChatMessage, LLMProvider, LLMResponse
from tads.llm.providers import heuristic_token_count
from tads.schemas.cost import TokenUsage


class MockProvider(LLMProvider):
    provider_id = "mock"
    display_name = "Mock (tests)"

    def __init__(self, response_json: Optional[dict] = None) -> None:
        self._response_json = response_json or {
            "findings": [
                {
                    "finding_type": "time_assurance_gap",
                    "title": "Era wrap guidance may be incomplete",
                    "description": "Document discusses 32-bit seconds and eras; long-horizon ops guidance should be reviewed.",
                    "severity": "medium",
                    "confidence": "medium",
                    "domains": ["y2036", "rollover"],
                    "section_id": "s-1",
                    "section_title": "1. Introduction",
                    "evidence": [
                        {
                            "quote": "32-bit",
                            "note": "Representation width mentioned",
                        }
                    ],
                    "machine_interpretation": "Potential Y2036-related assurance gap.",
                    "recommendation_level1": "Clarify era rollover and supported operational horizon.",
                }
            ]
        }

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str:
        return "mock-model"

    def estimate_tokens(self, text: str) -> int:
        return heuristic_token_count(text)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        max_output_tokens: int = 4096,
    ) -> LLMResponse:
        _ = max_output_tokens
        content = json.dumps(self._response_json)
        # Rough usage based on prompt size for cost accounting tests
        prompt = "\n".join(m.content for m in messages)
        usage = TokenUsage(
            input_tokens=self.estimate_tokens(prompt),
            output_tokens=self.estimate_tokens(content),
        )
        return LLMResponse(
            content=content,
            usage=usage,
            model=model or self.default_model(),
        )
