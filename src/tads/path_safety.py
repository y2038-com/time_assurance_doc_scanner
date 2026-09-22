# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Safe automatic output filenames and directory containment.

Used only for paths TADS derives from a document id, normalized id, or other
untrusted token. Explicit user ``--output`` / ``--save-text`` paths are not
passed through this module.

Ordinary spaces become underscores without a digest, matching the historical
``doc_id.replace(" ", "_")`` filenames. That preserves the pre-existing
``foo bar`` versus ``foo_bar`` collision. A digest is added only when further
sanitization, truncation, a reserved device name, or an empty result is
required.

This helper does not defend against a hostile pre-existing symlink at the
output root, and it does not close filesystem race conditions.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_STEM_LENGTH = 80
_DIGEST_LENGTH = 12
_SAFE_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_DRIVE_COMPONENT = re.compile(r"^[A-Za-z]:$")
_RESERVED_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *{f"COM{i}" for i in range(1, 10)},
        *{f"LPT{i}" for i in range(1, 10)},
    }
)


def safe_filename(raw: str) -> str:
    """Return a single path component derived from ``raw``.

    Already-safe identifiers (after the legacy space-to-underscore conversion)
    are preserved. Unsafe path syntax and other characters are replaced; a
    12-hex SHA-256 prefix of the original string is appended when that extra
    change, truncation, a reserved name, or an empty result is required.
    """
    original = raw if isinstance(raw, str) else str(raw)
    spaced = original.replace(" ", "_")
    if _is_already_safe(spaced):
        return spaced

    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    suffix = f"_{digest}"
    base = _sanitize_component(spaced)
    if not base or _is_reserved_device(base) or _is_effectively_empty(base):
        base = "doc"
    max_base = MAX_STEM_LENGTH - len(suffix)
    if max_base < 1:
        max_base = 1
    if len(base) > max_base:
        base = base[:max_base].rstrip("._")
        if not base or _is_reserved_device(base) or _is_effectively_empty(base):
            base = "doc"
    result = f"{base}{suffix}"
    if _is_reserved_device(result) or not result or result in {".", ".."}:
        result = f"doc{suffix}"
    return result


def path_under(root: Path, filename: str) -> Path:
    """Join ``filename`` under ``root`` and require the result stay inside ``root``.

    ``filename`` must already be a single component (no separators, no absolute
    or drive/UNC form). ``root`` need not exist yet. Returns the joined path
    without resolving it so callers keep relative ``outputs/`` / ``inputs/``
    names.
    """
    name = filename if isinstance(filename, str) else str(filename)
    if not name or name in {".", ".."} or _has_path_syntax(name):
        raise ValueError(f"Refusing unsafe automatic output name: {filename!r}")
    root_path = Path(root)
    candidate = root_path / name
    resolved_root = root_path.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(
            f"Automatic output {candidate} escapes intended root {root_path}"
        )
    return candidate


def _is_already_safe(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    if name.startswith(".") or name.endswith("."):
        return False
    if not _SAFE_CHARS.fullmatch(name):
        return False
    if _is_reserved_device(name) or _is_effectively_empty(name):
        return False
    if len(name) > MAX_STEM_LENGTH:
        return False
    return True


def _is_effectively_empty(name: str) -> bool:
    return not name or set(name) <= {"_", ".", "-"}


def _is_reserved_device(name: str) -> bool:
    """True for Windows reserved device names, including trailing-dot forms."""
    trimmed = name.rstrip(". ")
    if not trimmed:
        return False
    stem = trimmed.split(".", 1)[0]
    return stem.upper() in _RESERVED_DEVICES


def _has_path_syntax(name: str) -> bool:
    if "/" in name or "\\" in name:
        return True
    if name.startswith(("\\\\", "//")):
        return True
    if _DRIVE_PREFIX.match(name):
        return True
    return False


def _sanitize_component(value: str) -> str:
    text = value.replace("\\", "/")
    text = text.lstrip("/")
    text = _DRIVE_PREFIX.sub("", text)
    text = text.lstrip("/")
    parts: list[str] = []
    for part in text.split("/"):
        if not part or part in {".", ".."} or _DRIVE_COMPONENT.match(part):
            continue
        cleaned = "".join(_safe_char(ch) for ch in part)
        cleaned = re.sub(r"_+", "_", cleaned).strip("._")
        if not cleaned or cleaned in {".", ".."}:
            continue
        parts.append(cleaned)
    joined = "_".join(parts)
    joined = re.sub(r"_+", "_", joined).strip("._")
    return joined


def _safe_char(ch: str) -> str:
    if "A" <= ch <= "Z" or "a" <= ch <= "z" or "0" <= ch <= "9" or ch in "._-":
        return ch
    return "_"
