# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""CLI validation for --max-sections-per-request."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.parsing.chunks import SectionChunkingError, pack_section_chunks
from tads.parsing.document import Section


@pytest.mark.parametrize("value", ["0", "-1", "-8"])
def test_cli_plan_rejects_nonpositive_max_sections_per_request(
    tmp_path: Path, value: str
):
    src = tmp_path / "doc.txt"
    src.write_text(
        "1. Intro\nTimestamps are 32-bit seconds.\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "plan",
            str(src),
            "--doc-id",
            "RFC9999",
            "--provider",
            "mock",
            "--force-sections",
            "--max-sections-per-request",
            value,
        ],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "Traceback" not in combined
    assert "ValueError" not in combined
    # Click/Typer range validation or controlled chunking message.
    assert (
        "max-sections-per-request" in combined.lower()
        or "range" in combined.lower()
        or "invalid" in combined.lower()
    )


def test_internal_api_zero_raises_section_chunking_error():
    sec = Section(
        id="s-1",
        title="1. Intro",
        text="Timestamps are 32-bit seconds.",
        level=1,
        start_char=0,
        end_char=30,
    )
    with pytest.raises(SectionChunkingError, match="max_sections_per_request"):
        pack_section_chunks(
            [sec],
            context_window=100_000,
            max_output_tokens=16_384,
            max_sections_per_request=0,
        )
