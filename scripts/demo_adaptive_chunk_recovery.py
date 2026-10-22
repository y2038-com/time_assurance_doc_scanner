#!/usr/bin/env python3
# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Offline mocked demonstrations of adaptive section-chunk recovery.

Does not contact a live LLM provider. Uses the in-process mock provider only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tads.errors import ProviderRequestError  # noqa: E402
from tads.export import report_to_markdown  # noqa: E402
from tads.llm.base import LLMResponse  # noqa: E402
from tads.llm.providers.mock_provider import MockProvider  # noqa: E402
from tads.llm.registry import register_provider  # noqa: E402
from tads.pipeline import run_scan  # noqa: E402
from tads.pipeline.parse_findings import FindingParseError  # noqa: E402
from tads.schemas.report import AnalysisMode  # noqa: E402


def _doc(n: int) -> str:
    return "\n".join(
        f"{i}. Section {i}\n\nUnique quote {i:02d} timestamps are 32-bit seconds.\n"
        for i in range(1, n + 1)
    )


def _invalid() -> dict:
    return {"findings": [{"finding_type": "time_assurance_gap", "title": "bad"}]}


EMPTY = {"findings": []}


class _Sequenced(MockProvider):
    def __init__(self, payloads: list) -> None:
        super().__init__()
        self._payloads = list(payloads)
        self.calls = 0

    def complete(self, messages, *, model=None, max_output_tokens=4096, reasoning_effort=None):
        from tads.llm.base import reject_unsupported_reasoning_effort
        from tads.schemas.cost import TokenUsage

        reject_unsupported_reasoning_effort(reasoning_effort)
        idx = min(self.calls, len(self._payloads) - 1)
        self.calls += 1
        payload = self._payloads[idx]
        if isinstance(payload, BaseException):
            raise payload
        content = payload if isinstance(payload, str) else json.dumps(payload)
        _ = messages
        return LLMResponse(
            content=content,
            model=model or self.default_model(),
            usage=TokenUsage(input_tokens=8, output_tokens=4),
        )


def _show(title: str, report=None, error=None) -> None:
    print(f"\n== {title} ==")
    if error is not None:
        print(f"raised: {type(error).__name__}: {error}")
        return
    assert report is not None
    run = report.run
    print(f"analysis_status={run.analysis_status}")
    print(f"coverage_complete={run.coverage_complete}")
    print(
        f"sections succeeded/failed={run.sections_succeeded}/{run.sections_failed}"
    )
    print(
        f"requests primary/repair/total/recovery="
        f"{run.analysis_request_count}/{run.repair_request_count}/"
        f"{run.total_provider_request_count}/{run.recovery_request_count} "
        f"splits={run.recovery_split_count}"
    )
    md = report_to_markdown(report)
    status_line = next(
        (line for line in md.splitlines() if "Analysis status" in line),
        "(no analysis status line)",
    )
    print(status_line)


def main() -> int:
    # 1. Complete recovery: last 4-section chunk splits into two successful halves.
    register_provider(_Sequenced([EMPTY, _invalid(), EMPTY, EMPTY]))
    report = run_scan(
        _doc(8),
        doc_id="DEMO-COMPLETE",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=4,
    )
    _show("complete recovery", report)

    # 2. Partial recovery: one singleton leaf remains unrecoverable.
    register_provider(
        _Sequenced([EMPTY, _invalid(), EMPTY, _invalid()])
    )
    report = run_scan(
        _doc(4),
        doc_id="DEMO-PARTIAL",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_sections_per_request=2,
    )
    _show("partial recovery with one failed leaf", report)

    # 3. Recovery-limit exhaustion after one extra request.
    register_provider(_Sequenced([_invalid(), EMPTY, _invalid()]))
    report = run_scan(
        _doc(4),
        doc_id="DEMO-LIMIT",
        provider="mock",
        force_mode=AnalysisMode.SECTION_AWARE,
        enforce_budget=False,
        max_recovery_requests=1,
    )
    _show("recovery-limit exhaustion", report)

    # 4. Ineligible provider error still aborts with no report.
    register_provider(
        _Sequenced(
            [
                ProviderRequestError(
                    "openai HTTP 401 for https://api.openai.com/v1/chat/completions "
                    "(non-retryable)"
                )
            ]
        )
    )
    try:
        run_scan(
            _doc(4),
            doc_id="DEMO-ABORT",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
        )
    except ProviderRequestError as exc:
        _show("ineligible failure still aborts", error=exc)
    else:
        print("ERROR: ineligible failure produced a report")
        return 1

    register_provider(_Sequenced([_invalid()]))
    try:
        run_scan(
            _doc(4),
            doc_id="DEMO-ALL-FAIL",
            provider="mock",
            force_mode=AnalysisMode.SECTION_AWARE,
            enforce_budget=False,
            max_recovery_requests=0,
        )
    except FindingParseError as exc:
        _show("all sections fail (no report)", error=exc)
    else:
        print("ERROR: all-fail produced a report")
        return 1

    print("\nOffline recovery demonstrations finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
