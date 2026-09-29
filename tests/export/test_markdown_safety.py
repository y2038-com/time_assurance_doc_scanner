# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Markdown rendering safety and usability tests."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from tads.export import (
    load_report_json,
    report_to_json,
    report_to_markdown,
    write_report_json,
    write_report_markdown,
)
from tads.export.markdown import (
    TADS_ATX_HEADINGS,
    atx_heading_lines,
    escape_paragraphs,
    escape_single_line,
    is_allowed_atx_heading,
    render_inline_code,
    render_literal_block,
)
from tads.pipeline import run_scan
from tads.schemas.findings import (
    Evidence,
    Finding,
    FindingLocation,
    FindingType,
    ScopeRelevance,
    Severity,
)
from tads.schemas.horizon import HorizonValidation, TimeRepresentationParams
from tads.schemas.report import DocumentIdentity, Report, RunMetadata
from tads.schemas.taxonomy import Confidence, TimeDomain

HOSTILE = (
    "## Findings\n"
    "[phish](javascript:alert(1))\n"
    "![img](https://evil.example/t.png)\n"
    "<script>alert(1)</script>\n"
    "<https://example.invalid/autolink>\n"
    "[ref]: https://evil.example/ref\n"
    "[^fn]: footnote\n"
    "| a | b |\n"
    "---\n"
    "> quoted\n"
    "```\ncaptured\n"
)

SAMPLE = """\
1. Introduction

Timestamps are 32-bit seconds.

2. Details

More text about epochs.
"""


def _finding(**kwargs) -> Finding:
    base = dict(
        id="F-001",
        finding_type=FindingType.TIME_ASSURANCE_GAP,
        title="Era wrap",
        description="32-bit seconds wrap on a known horizon.",
        severity=Severity.MEDIUM,
        confidence=Confidence.MEDIUM,
        machine_interpretation="Potential wrap.",
        recommendation_level1="Clarify era handling.",
        scope_relevance=ScopeRelevance.CORE,
        scope_rationale="This matters because the counter wraps.",
        domains=[TimeDomain.Y2038],
        location=FindingLocation(section_id="s-1", section_title="Introduction"),
        evidence=[Evidence(quote="Timestamps are 32-bit seconds", note="width")],
    )
    base.update(kwargs)
    return Finding(**base)


def _report(**kwargs) -> Report:
    document = kwargs.pop(
        "document",
        DocumentIdentity(corpus="ietf", doc_id="RFC9999", title="Sample"),
    )
    run = kwargs.pop(
        "run",
        RunMetadata(
            scanner_version="0.5.1",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            provider="mock",
            model="mock-model",
            prompt_framework_version="0.7.1",
        ),
    )
    findings = kwargs.pop("findings", [_finding()])
    return Report(document=document, run=run, findings=findings, **kwargs)


def _outside_fences(markdown: str) -> str:
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in lines:
        stripped = line.lstrip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_char is None:
            if fence_match:
                token = fence_match.group(1)
                fence_char = token[0]
                fence_len = len(token)
                continue
            out.append(line)
            continue
        if fence_match:
            token = fence_match.group(1)
            if token[0] == fence_char and len(token) >= fence_len:
                fence_char = None
                fence_len = 0
    return "".join(out)


def _strip_inline_code(text: str) -> str:
    out: list[str] = []
    i = 0
    nlen = len(text)
    while i < nlen:
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue
        n = 0
        while i + n < nlen and text[i + n] == "`":
            n += 1
        j = i + n
        found: int | None = None
        while j < nlen:
            if text[j] != "`":
                j += 1
                continue
            m = 0
            while j + m < nlen and text[j + m] == "`":
                m += 1
            if m == n:
                found = j
                break
            j += m
        if found is None:
            out.append(text[i : i + n])
            i += n
            continue
        out.append(" ")
        i = found + n
    return "".join(out)


def _outside_markup(markdown: str) -> str:
    return _strip_inline_code(_outside_fences(markdown))


def _assert_allowed_headings(markdown: str) -> None:
    for marker, text in atx_heading_lines(markdown):
        assert is_allowed_atx_heading(marker, text), (marker, text)
        if marker != "###":
            assert (marker, text) in TADS_ATX_HEADINGS
    h1 = [text for marker, text in atx_heading_lines(markdown) if marker == "#"]
    assert h1 == ["Time Assurance Scan Report"]


def _first_literal_fence(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        match = re.match(r"^(```+|~~~+)$", stripped)
        if not match:
            continue
        token = match.group(1)
        for j in range(i + 1, len(lines)):
            closer = re.match(r"^(```+|~~~+)", lines[j].lstrip())
            if (
                closer
                and closer.group(1)[0] == token[0]
                and len(closer.group(1)) >= len(token)
            ):
                return token, "\n".join(lines[i + 1 : j])
    raise AssertionError("no literal fence found")


def _assert_inactive_block_structure(text: str) -> None:
    for raw in text.splitlines():
        line = raw.replace("&#32;", "")
        stripped = line.lstrip()
        assert not stripped.startswith(("#", ">", "-", "*", "+", "```", "~~~"))
        assert not re.match(r"^\d+[.)]\s", stripped)
        assert not re.match(r"^\[.+\]:", stripped)
        assert not re.match(r"^\[\^.+\]:", stripped)
        assert stripped not in {"---", "***", "___", "* * *"}
    lowered = text.lower()
    assert "](" not in text
    assert "![" not in text
    assert "<script" not in lowered
    assert "<!--" not in text
    assert "<https:" not in lowered
    assert "<http:" not in lowered
    assert "://" not in text
    assert "www." not in lowered


def _assert_no_active_markup(markdown: str) -> None:
    outside = _outside_markup(markdown)
    lowered = outside.lower()
    assert "](javascript:" not in outside
    assert "](data:" not in outside
    assert "](http" not in outside
    assert "<script" not in lowered
    assert "<img" not in lowered
    assert "<details" not in lowered
    assert "![img]" not in outside
    assert "<https://" not in outside
    assert "<http://" not in outside


def test_helpers_fold_and_escape_structure():
    folded = escape_single_line("Era\n## Findings")
    assert "\n" not in folded
    assert folded.startswith("Era")
    assert "\\#" in folded or folded.find("#") == -1 or "Findings" in folded
    paras = escape_paragraphs("ok\n\n## Findings\n- listed")
    assert "\n\n" in paras
    for line in paras.splitlines():
        if line == "":
            continue
        assert not line.startswith("##")
        assert not line.startswith("- ")


def test_inline_code_dynamic_delimiter_and_fallback():
    assert render_inline_code("F-001") == "`F-001`"
    coded = render_inline_code("a`b")
    assert coded.startswith("``")
    assert "a`b" in coded
    folded = render_inline_code("a\nb")
    assert "\n" not in folded
    assert "a b" in folded or "a b" in escape_single_line("a\nb")


def test_literal_block_cannot_close_on_content():
    quote = "pre\n````\npost"
    block = render_literal_block(quote)
    opening = block.split("\n", 1)[0]
    closing = block.rsplit("\n", 1)[-1]
    assert opening == closing
    assert len(opening) >= 3
    assert opening[0] in "`~"
    assert opening == opening[0] * len(opening)
    inner = "\n".join(block.split("\n")[1:-1])
    assert inner == quote
    for line in inner.split("\n"):
        stripped = line.rstrip()
        leading = len(stripped) - len(stripped.lstrip(" "))
        if leading > 3:
            continue
        body = stripped[leading:]
        assert not (body.startswith(opening) and set(body) <= {opening[0]})


def test_literal_block_mixed_runs_stay_closed():
    quote = "````\n~~~~\n```"
    block = render_literal_block(quote)
    opening = block.split("\n", 1)[0]
    inner = "\n".join(block.split("\n")[1:-1])
    assert inner == quote
    for line in inner.split("\n"):
        stripped = line.rstrip()
        leading = len(stripped) - len(stripped.lstrip(" "))
        body = stripped[leading:]
        assert not (body.startswith(opening) and set(body) <= {opening[0]})


def test_nul_and_bidi_are_visible_hex():
    text = escape_single_line("A\x00B\u202eC")
    assert "\x00" not in text
    assert "\u202e" not in text
    assert "\\u0000" in text
    assert "\\u202e" in text


def test_zwj_international_preserved():
    raw = "日本語\u200d連携 Époque"
    assert "日本語" in escape_single_line(raw)
    assert "Époque" in escape_single_line(raw)
    assert "\u200d" in escape_single_line(raw)


def test_h1_is_tads_only_and_id_is_labeled():
    md = report_to_markdown(
        _report(
            document=DocumentIdentity(
                corpus="ietf",
                doc_id="RFC9999\n# Forged title",
                title="Sample\n## Assurance notice",
            )
        )
    )
    assert md.splitlines()[0] == "# Time Assurance Scan Report"
    _assert_allowed_headings(md)
    assert "RFC9999" in md
    assert "Forged title" in md


def test_finding_heading_is_id_only():
    md = report_to_markdown(
        _report(findings=[_finding(title="Era\n## Assurance notice\nAll clear")])
    )
    _assert_allowed_headings(md)
    assert "### `F-001`" in md
    assert "**Title:**" in md
    assert "All clear" in md
    headings = [text for marker, text in atx_heading_lines(md) if marker == "##"]
    assert "Assurance notice" in headings
    assert headings.count("Assurance notice") == 1


def test_hostile_payloads_in_interpolated_fields():
    cases = []
    cases.append(_report(document=DocumentIdentity(corpus="ietf", doc_id=HOSTILE, title="t")))
    cases.append(
        _report(document=DocumentIdentity(corpus=HOSTILE, doc_id="RFC1", title="t"))
    )
    cases.append(
        _report(
            document=DocumentIdentity(
                corpus="ietf",
                doc_id="RFC1",
                title=HOSTILE,
                source_uri="javascript:alert(1)",
                source_path=HOSTILE,
                content_sha256=HOSTILE,
            )
        )
    )
    cases.append(
        _report(
            run=RunMetadata(
                scanner_version=HOSTILE,
                provider=HOSTILE,
                model=HOSTILE,
                prompt_framework_version=HOSTILE,
                scope_truncated=True,
                scope_notes=["Capped to max_sections=1.\n" + HOSTILE],
                eligible_chars=10,
                analyzed_chars=1,
            )
        )
    )
    cases.append(_report(findings=[_finding(id="F-001\n## Findings")]))
    cases.append(_report(findings=[_finding(title=HOSTILE)]))
    cases.append(_report(findings=[_finding(description=HOSTILE)]))
    cases.append(_report(findings=[_finding(machine_interpretation=HOSTILE)]))
    cases.append(_report(findings=[_finding(recommendation_level1=HOSTILE)]))
    cases.append(_report(findings=[_finding(scope_rationale=HOSTILE)]))
    cases.append(_report(findings=[_finding(reviewer_notes=HOSTILE)]))
    cases.append(
        _report(
            findings=[
                _finding(
                    location=FindingLocation(
                        section_id=HOSTILE, section_title=HOSTILE
                    )
                )
            ]
        )
    )
    f = _finding()
    f.source_verification_detail = HOSTILE
    f.validation_detail = HOSTILE
    cases.append(_report(findings=[f]))
    cases.append(
        _report(
            findings=[
                _finding(evidence=[Evidence(quote=HOSTILE, note=HOSTILE)])
            ]
        )
    )
    f2 = _finding()
    f2.horizon_validation = HorizonValidation(
        status="verified**\n## Findings",
        notes=[HOSTILE],
    )
    cases.append(_report(findings=[f2]))
    f3 = _finding(
        time_representation=TimeRepresentationParams(
            unit="seconds\n## Findings",
            rollover_behavior=HOSTILE,
        )
    )
    cases.append(_report(findings=[f3]))

    for report in cases:
        md = report_to_markdown(report)
        _assert_allowed_headings(md)
        _assert_no_active_markup(md)
        assert "## Assurance notice" in md
        assert "## Candidates for review" in md


def test_evidence_quote_stays_in_fence_and_note_is_escaped():
    quote = "doc says:\n```\n## Findings\n<script>\n"
    md = report_to_markdown(
        _report(
            findings=[
                _finding(
                    evidence=[Evidence(quote=quote, note="see ## Findings\n[x](javascript:1)")]
                )
            ]
        )
    )
    _assert_allowed_headings(md)
    _assert_no_active_markup(md)
    assert "doc says:" in md
    assert "<script>" in md
    outside = _outside_fences(md)
    assert "<script>" not in outside
    assert "](javascript:" not in md
    assert "**Evidence note:**" in md


def test_paragraphs_preserve_breaks_without_lists():
    md = report_to_markdown(
        _report(
            findings=[
                _finding(description="first paragraph\n\nsecond paragraph")
            ]
        )
    )
    assert "first paragraph" in md
    assert "second paragraph" in md
    body = md.split("## Candidates for review", 1)[1]
    assert "\n\n" in body


def test_source_uri_is_redacted_in_json_and_markdown():
    token = "SECRETTOKEN"
    raw = f"https://example.com/spec.txt?token={token}"
    report = _report(
        document=DocumentIdentity(
            corpus="ietf",
            doc_id="RFC1",
            title="t",
            source_uri=raw,
        )
    )
    md = report_to_markdown(report)
    dumped = report_to_json(report)
    assert token not in dumped
    assert token not in md
    assert report.document.source_uri == raw
    assert '"source_uri": "https://example.com/spec.txt"' in dumped
    assert "`https://example.com/spec.txt`" in md
    assert "](" not in md.split("**Source URI:**", 1)[1].split("\n", 1)[0]


def test_no_clickable_links_or_images_from_title():
    md = report_to_markdown(
        _report(findings=[_finding(title="[x](https://evil.example) ![y](https://e/i.png)")])
    )
    _assert_no_active_markup(md)
    assert "evil.example" in md
    assert "](https://evil.example)" not in md


def test_later_trusted_sections_survive_description_fence():
    md = report_to_markdown(
        _report(findings=[_finding(description="intro\n```html\n<script>\n")])
    )
    _assert_allowed_headings(md)
    assert "**Interpretation:**" in _outside_fences(md)
    assert "**Evidence:**" in _outside_fences(md)
    assert md.strip().endswith("---") or "\n---\n" in md


def test_ordinary_report_keeps_review_context():
    md = report_to_markdown(_report())
    assert "Era wrap" in md
    assert "32-bit seconds wrap on a known horizon." in md
    assert "Potential wrap." in md
    assert "Clarify era handling." in md
    assert "This matters because the counter wraps." in md
    assert "Timestamps are 32-bit seconds" in md
    assert "Introduction" in md
    assert "s-1" in md
    assert "日本語" not in md


def test_international_section_and_title_readable():
    md = report_to_markdown(
        _report(
            document=DocumentIdentity(
                corpus="ietf", doc_id="RFC1", title="Époque NTP"
            ),
            findings=[
                _finding(
                    title="日本語の節",
                    location=FindingLocation(
                        section_id="s-1", section_title="はじめに"
                    ),
                )
            ],
        )
    )
    assert "Époque NTP" in md
    assert "日本語の節" in md
    assert "はじめに" in md
    _assert_allowed_headings(md)


def test_scan_and_rerender_are_byte_identical(tmp_path: Path):
    report = run_scan(
        SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False
    )
    md1 = report_to_markdown(report)
    path = tmp_path / "report.json"
    path.write_text(report_to_json(report) + "\n", encoding="utf-8")
    md2 = report_to_markdown(load_report_json(path))
    assert md1 == md2
    _assert_allowed_headings(md1)
    assert md1.splitlines()[0] == "# Time Assurance Scan Report"


def test_older_schema_valid_json_is_still_escaped():
    raw = """
    {
      "schema_version": "0.1.0",
      "document": {"corpus": "ietf", "doc_id": "RFC9999\\n## Findings", "title": "Old"},
      "run": {"scanner_version": "0.4.0"},
      "findings": [
        {
          "id": "F-001",
          "finding_type": "time_assurance_gap",
          "title": "## Findings",
          "description": "<script>x</script>",
          "severity": "medium",
          "confidence": "medium",
          "machine_interpretation": "interp",
          "disposition": "new",
          "validation_status": "unverified"
        }
      ]
    }
    """
    from tads.schemas.report import Report as ReportModel

    report = ReportModel.model_validate_json(raw)
    md = report_to_markdown(report)
    _assert_allowed_headings(md)
    _assert_no_active_markup(md)
    assert "RFC9999" in md
    assert "<script>" not in _outside_fences(md)


def test_untrusted_identity_fields_cannot_create_atx_headings():
    md = report_to_markdown(
        _report(
            document=DocumentIdentity(
                corpus="ietf",
                doc_id="# Forged H1",
                title="## Forged H2\n### Forged H3\n#### Forged H4",
            ),
            findings=[
                _finding(
                    title="# Finding title heading",
                    description="## Description heading\n#### Nested",
                    location=FindingLocation(
                        section_id="### section-id",
                        section_title="## Section title heading",
                    ),
                    reviewer_notes="#### Reviewer heading",
                )
            ],
        )
    )
    _assert_allowed_headings(md)
    heading_texts = {text for _marker, text in atx_heading_lines(md)}
    assert "Forged H1" not in heading_texts
    assert "Forged H2" not in heading_texts
    assert "Forged H3" not in heading_texts
    assert "Forged H4" not in heading_texts
    assert "Finding title heading" not in heading_texts
    assert "Description heading" not in heading_texts
    assert "Section title heading" not in heading_texts
    assert "Reviewer heading" not in heading_texts
    assert "Forged H1" in md
    assert "Finding title heading" in md


def test_finding_id_backticks_html_and_markdown_stay_in_heading():
    fid = "F-00`1\n## Findings<script>alert(1)</script>"
    md = report_to_markdown(_report(findings=[_finding(id=fid)]))
    _assert_allowed_headings(md)
    h3 = [text for marker, text in atx_heading_lines(md) if marker == "###"]
    assert h3 == [render_inline_code(fid)]
    assert h3[0].startswith("``")
    assert "\n" not in h3[0]
    assert "<script>" not in _outside_markup(md)
    assert "## Findings" not in [
        text for marker, text in atx_heading_lines(md) if marker == "##"
    ]


def test_evidence_fence_line_start_backtick_and_tilde_payloads():
    later_title = "Later trusted finding"
    cases = (
        "````\nquoted backtick run",
        "~~~~\nquoted tilde run",
        "```\n~~~~\nboth at line start",
    )
    for quote in cases:
        md = report_to_markdown(
            _report(
                findings=[
                    _finding(
                        evidence=[Evidence(quote=quote, note="keep note")],
                        reviewer_notes="keep reviewer",
                    ),
                    _finding(id="F-002", title=later_title),
                ]
            )
        )
        token, inner = _first_literal_fence(md)
        assert len(token) >= 3
        assert inner == quote.replace("\r\n", "\n")
        for line in inner.split("\n"):
            stripped = line.rstrip()
            leading = len(stripped) - len(stripped.lstrip(" "))
            if leading > 3:
                continue
            body = stripped[leading:]
            assert not (body.startswith(token) and set(body) <= {token[0]})
        outside = _outside_fences(md)
        assert later_title in outside
        assert "### `F-002`" in outside
        assert "**Evidence note:**" in outside
        assert "keep note" in outside
        assert "keep reviewer" in outside
        _assert_allowed_headings(md)


def test_evidence_note_is_outside_quote_and_structurally_inert():
    quote = "verbatim <b>quote</b>"
    note = (
        "## Findings\n"
        "[x](https://evil.example/click)\n"
        "![t](https://evil.example/pixel.png)\n"
        "<http://evil.example/auto>\n"
        "www.evil.example\n"
        "<!-- comment -->\n"
        "[^1]: https://evil.example/fn\n"
        "[ref]: https://evil.example/ref\n"
        "- listed\n"
        "> quoted\n"
        "---\n"
        "```\ncaptured"
    )
    md = report_to_markdown(
        _report(findings=[_finding(evidence=[Evidence(quote=quote, note=note)])])
    )
    token, inner = _first_literal_fence(md)
    assert quote in inner
    assert token
    outside = _outside_fences(md)
    assert quote not in outside
    assert "**Evidence note:**" in outside
    assert "Findings" in outside
    assert "listed" in outside
    _assert_inactive_block_structure(escape_paragraphs(note))
    _assert_allowed_headings(md)
    _assert_no_active_markup(md)
    assert "Findings" in md
    assert "listed" in md


def test_paragraphs_defeat_links_html_lists_and_fences():
    raw = (
        "[inline](https://evil.example/click)\n"
        "[text][ref]\n"
        "![img](https://tracker.example/pixel.png)\n"
        "<https://evil.example/autolink>\n"
        "https://evil.example/bare\n"
        "www.evil.example/path\n"
        "mailto:exfil@evil.example\n"
        "<script>alert(1)</script>\n"
        "<!-- remote -->\n"
        "[^fn]: https://evil.example/fn\n"
        "[ref]: https://evil.example/ref\n"
        "- listed\n"
        "* starred\n"
        "+ plus\n"
        "1. ordered\n"
        "> quoted\n"
        "---\n"
        "***\n"
        "```\nfenced\n"
        "~~~\nalso fenced"
    )
    escaped = escape_paragraphs(raw)
    _assert_inactive_block_structure(escaped)
    md = report_to_markdown(
        _report(
            findings=[
                _finding(
                    description=raw,
                    machine_interpretation=raw,
                    recommendation_level1=raw,
                    scope_rationale=raw,
                    reviewer_notes=raw,
                )
            ]
        )
    )
    _assert_allowed_headings(md)
    _assert_no_active_markup(md)
    assert "inline" in md
    assert "pixel.png" in md
    assert "ordered" in md
    assert "fenced" in md


def test_source_uri_userinfo_query_fragment_canaries():
    userinfo = "USERINFOCANARY"
    query = "QUERYCANARY"
    fragment = "FRAGMENTCANARY"
    uri = (
        f"https://{userinfo}:secret@example.com/spec.txt"
        f"?token={query}#{fragment}"
    )
    report = _report(
        document=DocumentIdentity(
            corpus="ietf", doc_id="RFC1", title="t", source_uri=uri
        )
    )
    before = report_to_json(report)
    md = report_to_markdown(report)
    after = report_to_json(report)
    assert before == after
    assert report.document.source_uri == uri
    assert userinfo not in before
    assert query not in before
    assert fragment not in before
    assert userinfo not in md
    assert query not in md
    assert fragment not in md
    assert '"source_uri": "https://example.com/spec.txt"' in before
    assert "`https://example.com/spec.txt`" in md
    uri_line = md.split("**Source URI:**", 1)[1].split("\n", 1)[0]
    assert "](" not in uri_line
    assert uri_line.count("`") >= 2


def test_inline_code_spaces_tabs_and_vertical_whitespace():
    spaced = render_inline_code("  padded  ")
    assert spaced.startswith("`")
    assert spaced.endswith("`")
    assert "padded" in spaced
    tabbed = render_inline_code("a\tb")
    assert "\t" not in tabbed
    assert "\n" not in tabbed
    assert "a b" in tabbed
    vertical = render_inline_code("a\u2028b")
    assert "\u2028" not in vertical
    assert "a b" in vertical
    ticks = render_inline_code("x````y")
    assert ticks.startswith("`````")
    assert "x````y" in ticks
    empty = render_inline_code("")
    assert empty.startswith("`")
    assert empty.endswith("`")


def test_bidi_isolates_and_zwnj_policy():
    text = escape_single_line("A\x00B\u202eC\u2066D")
    assert "\\u0000" in text
    assert "\\u202e" in text
    assert "\\u2066" in text
    assert "\x00" not in text
    assert "\u202e" not in text
    mixed = escape_single_line("عربى\u200clink\u200d日本語")
    assert "عربى" in mixed
    assert "日本語" in mixed
    assert "\u200c" in mixed
    assert "\u200d" in mixed


def test_markdown_rendering_does_not_change_canonical_json():
    report = _report(
        document=DocumentIdentity(
            corpus="ietf",
            doc_id="RFC1",
            title="t",
            source_uri="https://example.com/a?token=KEEPJSON",
        ),
        findings=[_finding(description="visible description")],
    )
    before = report_to_json(report)
    md = report_to_markdown(report)
    after = report_to_json(report)
    assert before == after
    assert report.document.source_uri == "https://example.com/a?token=KEEPJSON"
    assert "KEEPJSON" not in before
    assert "KEEPJSON" not in md
    assert "visible description" in md


def test_scan_markdown_matches_tads_render_bytes(tmp_path: Path):
    report = run_scan(
        SAMPLE, doc_id="RFC9999", provider="mock", enforce_budget=False
    )
    scan_md = report_to_markdown(report)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    write_report_json(report, json_path)
    write_report_markdown(load_report_json(json_path), md_path)
    assert md_path.read_bytes() == scan_md.encode("utf-8")
    assert scan_md == report_to_markdown(load_report_json(json_path))


def test_review_context_fields_remain_readable():
    md = report_to_markdown(
        _report(
            findings=[
                _finding(
                    title="Readable title",
                    description="Readable description.",
                    machine_interpretation="Readable interpretation.",
                    recommendation_level1="Readable recommendation.",
                    validation_detail="Readable validation detail.",
                    reviewer_notes="Readable reviewer notes.",
                    location=FindingLocation(
                        section_id="s-9", section_title="Readable section"
                    ),
                    evidence=[
                        Evidence(quote="Readable evidence quote", note="Readable note")
                    ],
                )
            ]
        )
    )
    for snippet in (
        "Readable title",
        "Readable description.",
        "Readable interpretation.",
        "Readable recommendation.",
        "Readable validation detail.",
        "Readable reviewer notes.",
        "Readable section",
        "s-9",
        "Readable evidence quote",
        "Readable note",
    ):
        assert snippet in md


def test_no_raw_string_field_interpolations_in_exporter():
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "tads"
        / "export"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "{finding.title}",
        "{finding.description}",
        "{finding.id}",
        "{finding.machine_interpretation}",
        "{finding.recommendation_level1}",
        "{finding.scope_rationale}",
        "{finding.reviewer_notes}",
        "{finding.validation_detail}",
        "{finding.source_verification_detail}",
        "{doc.doc_id}",
        "{doc.title}",
        "{doc.corpus}",
        "{doc.source_uri}",
        "{doc.retrieved_uri}",
        "{doc.source_path}",
        "{doc.content_sha256}",
        "{ev.quote}",
        "{ev.note}",
        "{loc.section_id}",
        "{loc.section_title}",
        "{hv.status}",
        "{params.unit}",
        "{params.rollover_behavior}",
        "{run.provider}",
        "{run.model}",
        "{run.scanner_version}",
        "{run.prompt_framework_version}",
    )
    for pattern in forbidden:
        assert pattern not in src, pattern

