# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Document and section models."""

from tads.parsing.clauses import (
    ClauseParseHealth,
    assess_clause_parse_health,
    section_plain_text_clauses,
)
from tads.parsing.document import ParsedDocument, Section
from tads.parsing.sections import choose_analysis_mode, estimate_tokens

__all__ = [
    "ClauseParseHealth",
    "ParsedDocument",
    "Section",
    "assess_clause_parse_health",
    "choose_analysis_mode",
    "estimate_tokens",
    "section_plain_text_clauses",
]
