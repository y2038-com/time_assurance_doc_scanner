# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Context-specific Markdown helpers for untrusted report strings."""

from __future__ import annotations

import re

# Embeddings, overrides, and isolates. LRM/RLM and ZWJ/ZWNJ are left intact.
_BIDI_CONTROLS = frozenset(
    {
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)
_VERTICAL_WS = frozenset({0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029})
_INLINE_SPECIALS = frozenset({"\\", "`", "*", "_", "[", "]", "(", ")", "!", "|", "~"})
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED_PREFIX = re.compile(r"^(\d{1,9})([.)])")
_SCHEME_SLASHES = re.compile(r"://")
_WWW_DOT = re.compile(r"(?i)\bwww\.")
_MAILTO = re.compile(r"(?i)\bmailto:")

TADS_ATX_HEADINGS = (
    ("#", "Time Assurance Scan Report"),
    ("##", "Assurance notice"),
    ("##", "Summary"),
    ("##", "Human review"),
    ("##", "Candidates for review"),
    ("##", "Incidental observations"),
    ("####", "Deterministic validation"),
)


def escape_single_line(value: str) -> str:
    """Fold vertical whitespace and escape for a labeled one-line field."""
    text = _replace_dangerous(value)
    text = _fold_vertical(text)
    return _break_autolinks(_escape_inline_specials(text))


def escape_list_value(value: str) -> str:
    """Escape a value that must remain on one list item."""
    return escape_single_line(value)


def escape_paragraphs(value: str) -> str:
    """Preserve paragraph breaks; escape every line against Markdown structure."""
    text = _replace_dangerous(value)
    text = _normalize_newlines(text)
    raw_lines = text.split("\n")
    out: list[str] = []
    blank = False
    for raw in raw_lines:
        stripped_nl = raw.replace("\t", " ")
        if stripped_nl.strip() == "":
            if out and not blank:
                out.append("")
                blank = True
            continue
        blank = False
        out.append(_escape_paragraph_line(stripped_nl))
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def render_inline_code(value: str) -> str:
    """Wrap in backticks with a delimiter longer than any run; else escape."""
    text = _replace_dangerous(value)
    if any(ord(ch) in _VERTICAL_WS for ch in text) or "\t" in text:
        text = _fold_vertical(text)
    if "\n" in text or "\r" in text:
        return escape_single_line(value)
    n = _max_run(text, "`") + 1
    if n < 1:
        n = 1
    if n > 32:
        return escape_single_line(value)
    delim = "`" * n
    body = text
    if (
        not body
        or body.startswith("`")
        or body.endswith("`")
        or body.startswith(" ")
        or body.endswith(" ")
    ):
        body = f" {body} "
    return f"{delim}{body}{delim}"


def render_literal_block(value: str) -> str:
    """Fenced literal with no info string; cannot be closed by the content."""
    text = _replace_dangerous(value)
    text = _normalize_newlines(text)
    if text.endswith("\n"):
        text = text[:-1]
    marker = "`"
    run_bt = _max_run(text, "`")
    run_td = _max_run(text, "~")
    if run_td < run_bt:
        marker = "~"
        run = run_td
    else:
        run = run_bt
    n = max(3, run + 1)
    while _content_closes_fence(text, marker, n):
        n += 1
    fence = marker * n
    return f"{fence}\n{text}\n{fence}"


def render_list_line(label: str, value: str) -> str:
    """TADS list label plus a pre-escaped or pre-coded value."""
    return f"- **{label}:** {value}"


def labeled_line(label: str, value: str, *, hard_break: bool = True) -> str:
    """TADS bold label plus a pre-escaped or pre-coded value."""
    line = f"**{label}:** {value}"
    if hard_break:
        line += "  "
    return line


def atx_heading_lines(markdown: str) -> list[tuple[str, str]]:
    """Return (marker, text) for ATX headings outside fenced code blocks."""
    found: list[tuple[str, str]] = []
    fence_char: str | None = None
    fence_len = 0
    for line in markdown.splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_char is None:
            if fence_match:
                token = fence_match.group(1)
                fence_char = token[0]
                fence_len = len(token)
                continue
            match = _ATX_HEADING.match(line)
            if match:
                found.append((match.group(1), match.group(2)))
            continue
        if fence_match:
            token = fence_match.group(1)
            if token[0] == fence_char and len(token) >= fence_len:
                fence_char = None
                fence_len = 0
    return found


def is_allowed_atx_heading(marker: str, text: str) -> bool:
    if (marker, text) in TADS_ATX_HEADINGS:
        return True
    return marker == "###" and _is_inline_code_span(text)


def _is_inline_code_span(text: str) -> bool:
    if not text.startswith("`") or "\n" in text:
        return False
    n = 0
    for ch in text:
        if ch != "`":
            break
        n += 1
    if n < 1:
        return False
    closing = "`" * n
    if not text.endswith(closing):
        return False
    inner = text[n : len(text) - n]
    return closing not in inner


def _replace_dangerous(value: str) -> str:
    out: list[str] = []
    for ch in value:
        cp = ord(ch)
        if cp == 0 or cp in _BIDI_CONTROLS:
            out.append(_visible_hex(cp))
        elif cp < 32 and ch not in "\t\n\r":
            out.append(_visible_hex(cp))
        elif cp == 127:
            out.append(_visible_hex(cp))
        else:
            out.append(ch)
    return "".join(out)


def _visible_hex(cp: int) -> str:
    return f"\\u{cp:04x}"


def _fold_vertical(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ord(ch) in _VERTICAL_WS or ch == "\t":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _normalize_newlines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u0085", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    text = text.replace("\v", "\n").replace("\f", "\n")
    return text


def _escape_inline_specials(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out: list[str] = []
    for ch in text:
        if ch in _INLINE_SPECIALS:
            out.append(f"\\{ch}")
        else:
            out.append(ch)
    return "".join(out)


def _escape_paragraph_line(text: str) -> str:
    escaped = _break_autolinks(_escape_inline_specials(text))
    return _neutralize_line_start(escaped)


def _neutralize_line_start(text: str) -> str:
    i = 0
    while i < len(text) and text[i] == " ":
        i += 1
    prefix = "&#32;" * i
    rest = text[i:]
    if not rest:
        return prefix
    if rest[0] in "#>*+-=~`|_":
        rest = f"\\{rest}"
    else:
        rest = _ORDERED_PREFIX.sub(r"\1\\\2", rest, count=1)
    return prefix + rest


def _break_autolinks(text: str) -> str:
    text = _SCHEME_SLASHES.sub("&#58;//", text)
    text = _MAILTO.sub("mailto&#58;", text)
    text = _WWW_DOT.sub("www&#46;", text)
    return text.replace("@", "&#64;")


def _max_run(text: str, char: str) -> int:
    longest = 0
    current = 0
    for ch in text:
        if ch == char:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def _content_closes_fence(text: str, marker: str, n: int) -> bool:
    fence = marker * n
    for line in text.split("\n"):
        stripped = line.rstrip()
        leading = len(stripped) - len(stripped.lstrip(" "))
        if leading > 3:
            continue
        body = stripped[leading:]
        if body.startswith(fence) and set(body) <= {marker}:
            return True
    return False
