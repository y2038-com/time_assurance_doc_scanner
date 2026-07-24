"""Shared environment helpers for LLM defaults."""

from __future__ import annotations

import os


def env_first(*names: str, default: str = "") -> str:
    """Return the first non-empty environment value among names."""
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def default_provider_id() -> str:
    """
    Preferred: TADS_LLM_PROVIDER.
    Alias: TADS_PROVIDER.
    Default: ollama (Cloud when OLLAMA_HOST is unset or ollama.com).
    """
    return env_first("TADS_LLM_PROVIDER", "TADS_PROVIDER", default="ollama").lower()


def default_model_id() -> str | None:
    """Preferred: TADS_MODEL. Alias: TADS_LLM_MODEL."""
    value = env_first("TADS_MODEL", "TADS_LLM_MODEL")
    return value or None
