# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Public CLI error contract for ``tads scan``.

Only exceptions that inherit :class:`PublicScanError` may retain their message
at the scan CLI boundary. Unknown exceptions become a fixed generic message.
Public text never derives from arbitrary ``str(exc)`` / ``repr(exc)``,
attributes, causes, contexts, or HTTP request/response objects.
"""

from __future__ import annotations

import re
from typing import Final

# Stable public fallback. No exception class name, attributes, or causes.
GENERIC_SCAN_FAILURE: Final[str] = "Scan failed: unexpected internal error"

# ESC-initiated CSI (including private-mode params). Remaining ESC pairs (OSC,
# DCS, SS2/SS3, etc.) lose their introducer via ``_OTHER_ESC``, which neuters
# terminal actions even when payload text remains as literals.
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OTHER_ESC = re.compile(r"\x1b.")
# C0 controls (incl. CR/LF/TAB/DEL), C1 controls, Unicode line/paragraph
# separators, and common bidi override/isolate controls.
_UNSAFE_CHARS = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    r"\u0080-\u009f"
    r"\u2028\u2029"
    r"\u202a-\u202e"
    r"\u2066-\u2069]"
)


class PublicScanError(Exception):
    """Marker: ``str(self)`` is TADS-owned and safe for public scan CLI output.

    Subclasses must construct messages from allowlisted, controlled text only.
    Do not put provider bodies, prompts, credentials, raw model output, or
    sensitive URL components in the message.
    """


class ProviderRequestError(PublicScanError, RuntimeError):
    """Allowlisted provider/HTTP/request failure for public CLI display."""


class UnknownCorpusError(PublicScanError):
    """Unknown or unsupported analysis corpus id."""


def public_error_text(exc: BaseException) -> str | None:
    """Return the explicit public message from a :class:`PublicScanError`.

    Returns ``exc.args[0]`` only when ``exc`` is a :class:`PublicScanError` and
    that argument is a ``str``. Unknown exceptions always return ``None`` —
    including those with string or non-string ``args[0]``. Does not call
    ``str(exc)`` / ``repr(exc)`` and does not inspect attributes, causes, or
    contexts.
    """
    if not isinstance(exc, PublicScanError):
        return None
    if not exc.args:
        return None
    first = exc.args[0]
    return first if isinstance(first, str) else None


def normalize_public_cli_line(text: str) -> str:
    """Collapse a public message to one safe terminal line (no CR/LF/ANSI).

    Removes ESC/CSI introducers (neutering OSC/DCS payloads), C0/C1 controls,
    Unicode line/paragraph separators, and bidi override/isolate controls.
    Ordinary UTF-8 letters (including non-ASCII document names) are preserved.
    Rich markup such as ``[red]…[/red]`` is left as literal text for the raw
    progress writer (not interpreted).
    """
    if not isinstance(text, str):
        return GENERIC_SCAN_FAILURE
    cleaned = _ANSI_CSI.sub("", text)
    cleaned = _OTHER_ESC.sub("", cleaned)
    cleaned = _UNSAFE_CHARS.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned


def public_scan_failure_message(exc: BaseException) -> str:
    """Map an internal exception to a single public ``Scan failed: …`` line.

    :class:`PublicScanError` instances keep a normalized copy of their message.
    All other exceptions become :data:`GENERIC_SCAN_FAILURE`.
    """
    if isinstance(exc, PublicScanError):
        raw = public_error_text(exc)
        if raw is None:
            return GENERIC_SCAN_FAILURE
        body = normalize_public_cli_line(raw)
        if not body:
            return GENERIC_SCAN_FAILURE
        if body.casefold().startswith("scan failed"):
            return body
        return f"Scan failed: {body}"
    return GENERIC_SCAN_FAILURE


def public_explicit_scan_message(message: str) -> str:
    """Normalize an already-selected public message (not from arbitrary exc)."""
    body = normalize_public_cli_line(message)
    if not body:
        return GENERIC_SCAN_FAILURE
    if body.casefold() in {"aborted.", "aborted"}:
        return "Aborted."
    if body.casefold().startswith("scan failed"):
        return body
    return f"Scan failed: {body}"
