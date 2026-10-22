# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Bounded adaptive recovery for section-aware chunk failures.

Allowlisted recoverable failures are model-output validation problems after
the existing local JSON repair / semantic-correction slot is exhausted.
Provider, auth, budget, ingest, and unknown errors stay fail-closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tads.errors import PublicScanError
from tads.llm.base import LLMProvider, ModelOutputTruncatedError
from tads.parsing.chunks import SectionBinding
from tads.pipeline.parse_findings import FindingParseError

DEFAULT_MAX_RECOVERY_REQUESTS = 64
MAX_MAX_RECOVERY_REQUESTS = 100_000

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Narrow allowlist: explicit types only, never substring matching.
RECOVERABLE_MODEL_OUTPUT_ERRORS: tuple[type[BaseException], ...] = (
    FindingParseError,
    ModelOutputTruncatedError,
)


class InvalidRecoveryLimitError(PublicScanError, ValueError):
    """``--max-recovery-requests`` is outside the accepted range."""


class RecoveryBudgetExhausted(Exception):
    """Internal: no remaining recovery-request budget.

    Not a :class:`PublicScanError`. Callers convert it into structured
    unanalyzed-section records. Never expose ``str`` / ``repr`` publicly.
    """


def is_recoverable_model_output_failure(exc: BaseException) -> bool:
    """True only for the allowlisted model-output validation families."""
    return isinstance(exc, RECOVERABLE_MODEL_OUTPUT_ERRORS)


def validate_max_recovery_requests(value: int) -> int:
    """Reject non-integers and out-of-range limits before provider contact."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRecoveryLimitError(
            "max-recovery-requests must be an integer from 0 through "
            f"{MAX_MAX_RECOVERY_REQUESTS}"
        )
    if value < 0 or value > MAX_MAX_RECOVERY_REQUESTS:
        raise InvalidRecoveryLimitError(
            "max-recovery-requests must be an integer from 0 through "
            f"{MAX_MAX_RECOVERY_REQUESTS}"
        )
    return value


def bisect_bindings(
    bindings: tuple[SectionBinding, ...],
) -> tuple[tuple[SectionBinding, ...], tuple[SectionBinding, ...]]:
    """Split at a section boundary into two nonempty contiguous halves.

    Uses deterministic floor-bisection (``n // 2``) so a 7-section range
    becomes 3+4 and an 8-section range becomes 4+4. Original order is kept.
    """
    n = len(bindings)
    if n < 2:
        raise ValueError("adaptive recovery cannot bisect a single section")
    mid = n // 2
    left = bindings[:mid]
    right = bindings[mid:]
    if not left or not right:
        raise ValueError("adaptive recovery split produced an empty child")
    return left, right


def opaque_range_slug(bindings: tuple[SectionBinding, ...]) -> str:
    """Collision-safe slug from opaque refs (not document body)."""
    if not bindings:
        return "empty"
    first = bindings[0].opaque_ref
    last = bindings[-1].opaque_ref
    if first == last:
        return _safe_slug(first)
    return _safe_slug(f"{first}-{last}")


def _safe_slug(raw: str) -> str:
    text = _SLUG_UNSAFE.sub("-", str(raw)).strip("-._")
    if not text or text in {".", ".."} or ".." in text:
        text = "chunk"
    return text[:80]


def _bounded_sibling_path(dest: Path, filename: str) -> Optional[Path]:
    """Return ``dest.with_name(filename)`` only when it stays a sibling file."""
    if not filename or filename in {".", ".."}:
        return None
    if any(sep in filename for sep in ("/", "\\", "\x00")):
        return None
    if ".." in filename:
        return None
    try:
        candidate = dest.with_name(filename)
    except (ValueError, OSError):
        return None
    if candidate.name != filename:
        return None
    if candidate.parent != dest.parent:
        return None
    return candidate


def unanalyzed_category_for(exc: BaseException) -> str:
    """Map an allowlisted failure to a public report category string."""
    if isinstance(exc, ModelOutputTruncatedError):
        return "truncated_output"
    if isinstance(exc, FindingParseError):
        return "invalid_model_output"
    return "invalid_model_output"


@dataclass
class RecoveryBudget:
    """Counts actual recovery-tree ``complete()`` calls, including children."""

    limit: int
    used: int = 0
    split_count: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def try_consume(self, n: int = 1) -> bool:
        if n <= 0:
            return True
        if self.used + n > self.limit:
            return False
        self.used += n
        return True

    def record_split(self) -> None:
        self.split_count += 1


@dataclass
class UnanalyzedLeaf:
    """Sanitized per-section failure provenance for a public report."""

    section_id: str
    category: str
    char_count: int


@dataclass
class LogicalRequestTally:
    """Counts logical ``complete()`` calls for one analysis unit.

    The first counted call is the primary. Later counted calls in the same
    unit are JSON-repair or semantic-correction secondaries. Transport HTTP
    retries inside one ``complete()`` are not extra logical requests.
    ``RecoveryBudgetExhausted`` is not a logical request (no provider call).
    ``usages`` records ``LLMResponse.usage`` only when ``complete()`` returns;
    truncation and HTTP failures that raise before a response are omitted.
    """

    primary: int = 0
    repair: int = 0
    usages: list = field(default_factory=list)

    def wrap(self, inner: LLMProvider) -> LLMProvider:
        return _TallyingProvider(inner, self)


class _TallyingProvider:
    """Count executed logical provider calls, including failed ones."""

    def __init__(self, inner: LLMProvider, tally: LogicalRequestTally) -> None:
        self._inner = inner
        self._tally = tally

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def complete(self, *args, **kwargs):
        try:
            response = self._inner.complete(*args, **kwargs)
        except RecoveryBudgetExhausted:
            raise
        except Exception:
            self._note()
            raise
        self._note()
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._tally.usages.append(usage)
        return response

    def _note(self) -> None:
        if self._tally.primary == 0:
            self._tally.primary = 1
        else:
            self._tally.repair += 1


@dataclass
class RangeOutcome:
    """Result of analyzing one original chunk or recovery descendant."""

    findings: list = field(default_factory=list)
    succeeded_section_ids: list[str] = field(default_factory=list)
    succeeded_chars: int = 0
    failures: list[UnanalyzedLeaf] = field(default_factory=list)
    analysis_requests: int = 0
    repair_requests: int = 0


def merge_range_outcomes(left: RangeOutcome, right: RangeOutcome) -> RangeOutcome:
    """Concatenate in original document order (left then right)."""
    return RangeOutcome(
        findings=list(left.findings) + list(right.findings),
        succeeded_section_ids=(
            list(left.succeeded_section_ids) + list(right.succeeded_section_ids)
        ),
        succeeded_chars=left.succeeded_chars + right.succeeded_chars,
        failures=list(left.failures) + list(right.failures),
        analysis_requests=left.analysis_requests + right.analysis_requests,
        repair_requests=left.repair_requests + right.repair_requests,
    )


def failed_range(
    bindings: tuple[SectionBinding, ...],
    *,
    category: str,
    analysis_requests: int = 0,
    repair_requests: int = 0,
) -> RangeOutcome:
    """Mark every section in ``bindings`` as unanalyzed; keep no findings."""
    failures = [
        UnanalyzedLeaf(
            section_id=binding.canonical_id,
            category=category,
            char_count=binding.section.char_count,
        )
        for binding in bindings
    ]
    return RangeOutcome(
        failures=failures,
        analysis_requests=analysis_requests,
        repair_requests=repair_requests,
    )


class RecoveryLimitedProvider:
    """Delegate that consumes :class:`RecoveryBudget` on every ``complete()``."""

    def __init__(self, inner: LLMProvider, budget: RecoveryBudget) -> None:
        self._inner = inner
        self._budget = budget

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def complete(self, *args, **kwargs):
        if not self._budget.try_consume(1):
            raise RecoveryBudgetExhausted()
        return self._inner.complete(*args, **kwargs)


class RawDiagnosticSink:
    """Collision-safe opt-in raw diagnostic writer for one scan.

    The first persist of a scan uses the canonical ``*.raw.txt`` path so
    existing single-failure tests keep working. Later persists use
    ``{stem}.raw.{slug}.txt`` and never overwrite a path already written
    during this scan.
    """

    def __init__(self, canonical_path: Optional[str]) -> None:
        self.canonical_path = canonical_path
        self._written: list[str] = []
        self._seq = 0

    def destination(self, slug: Optional[str] = None) -> Optional[str]:
        if not self.canonical_path:
            return None
        dest = Path(self.canonical_path)
        if not self._written:
            return str(dest)
        self._seq += 1
        label = _safe_slug(slug or f"attempt-{self._seq:02d}")
        name = dest.name
        if name.endswith(".raw.txt"):
            stem = name[: -len(".raw.txt")]
            filename = f"{stem}.raw.{label}.txt"
        else:
            filename = f"{dest.stem}.{label}.raw.txt"
        candidate = _bounded_sibling_path(dest, filename)
        if candidate is None:
            return None
        n = 2
        used = set(self._written)
        while str(candidate) in used:
            if name.endswith(".raw.txt"):
                stem = name[: -len(".raw.txt")]
                filename = f"{stem}.raw.{label}-{n}.txt"
            else:
                filename = f"{dest.stem}.{label}-{n}.raw.txt"
            candidate = _bounded_sibling_path(dest, filename)
            if candidate is None:
                return None
            n += 1
        return str(candidate)

    def remember(self, path: str) -> None:
        self._written.append(path)

    @property
    def written_paths(self) -> tuple[str, ...]:
        return tuple(self._written)
