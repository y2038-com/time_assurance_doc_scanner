# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Mock provider behavior is unchanged by native structured-output support."""

from __future__ import annotations

import json

from tads.llm.base import ChatMessage
from tads.llm.providers.mock_provider import MockProvider
from tads.pipeline.parse_findings import extract_json_object, parse_findings_payload


def test_mock_complete_still_returns_canned_json_without_http():
    provider = MockProvider()
    assert provider.supports_native_structured_output() is False
    response = provider.complete(
        [
            ChatMessage(role="system", content="trusted"),
            ChatMessage(role="user", content="untrusted"),
        ]
    )
    payload = json.loads(response.content)
    assert "findings" in payload
    findings = parse_findings_payload(extract_json_object(response.content))
    assert len(findings) == 1
    assert findings[0].time_representation is not None
    assert findings[0].time_representation.claimed_horizon is not None
    assert findings[0].time_representation.claimed_horizon.precision.value == "day"
