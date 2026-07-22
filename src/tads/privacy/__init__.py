"""Privacy policy helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tads.schemas.report import PrivacyMode

logger = logging.getLogger("tads.privacy")


@dataclass(frozen=True)
class PrivacyPolicy:
    mode: PrivacyMode = PrivacyMode.EPHEMERAL
    log_document_bodies: bool = False

    @property
    def may_persist_source(self) -> bool:
        return self.mode == PrivacyMode.WORKSPACE

    @property
    def may_persist_outputs(self) -> bool:
        return self.mode in {PrivacyMode.PERSIST_OUTPUTS, PrivacyMode.WORKSPACE}

    def warn_if_debug_prompts(self, enabled: bool) -> None:
        if enabled:
            logger.warning(
                "Prompt/debug dumping is enabled; document content may appear in logs."
            )


DEFAULT_POLICY = PrivacyPolicy()


def redact_for_log(text: str, *, enabled: bool, limit: int = 0) -> str:
    """Return a safe log fragment; never return bodies unless explicitly enabled."""
    if not enabled:
        return f"<redacted len={len(text)}>"
    if limit and len(text) > limit:
        return text[:limit] + "…"
    return text
