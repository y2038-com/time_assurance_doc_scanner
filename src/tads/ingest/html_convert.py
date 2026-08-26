# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""HTML → plain text for ingest / corpus fetch."""

from __future__ import annotations

from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    """Minimal tag stripper; skips script/style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "section",
            "article",
            "header",
            "footer",
            "table",
        }:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)
            self._chunks.append(" ")


def html_to_text(data: bytes | str) -> str:
    """Convert HTML bytes/str to rough plain text."""
    if isinstance(data, bytes):
        raw = data.decode("utf-8", errors="replace")
    else:
        raw = data
    parser = _HTMLTextExtractor()
    parser.feed(raw)
    parser.close()
    text = "".join(parser._chunks)
    # Collapse runs of whitespace while keeping paragraph breaks.
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n\n".join(lines)
