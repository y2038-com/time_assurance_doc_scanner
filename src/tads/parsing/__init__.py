# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Document and section models."""

from tads.parsing.clauses import section_plain_text_clauses
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.sections import choose_analysis_mode, estimate_tokens

__all__ = [
    "ParsedDocument",
    "Section",
    "choose_analysis_mode",
    "estimate_tokens",
    "section_plain_text_clauses",
]
