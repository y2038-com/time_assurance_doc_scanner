# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Packaging metadata checks related to optional extras."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pymupdf_is_optional_pdf_extra_not_required():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert not any("pymupdf" in d.lower() for d in deps)
    pdf_extra = data["project"]["optional-dependencies"]["pdf"]
    assert any(d.lower().startswith("pymupdf") for d in pdf_extra)
    assert data["project"]["license"]["text"] == "Apache-2.0"
