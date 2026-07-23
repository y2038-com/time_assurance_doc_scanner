# Phase 1 — Core Scanner MVP

## Definition of done

A researcher can:

1. `tads fetch RFC5905` (or use a local `.txt`)
2. `tads plan …` to see analysis mode + cost estimate
3. `tads scan … -o out/RFC5905` to run BYOLLM analysis
4. Review findings in JSON (edit `disposition` / `reviewer_notes`)
5. `tads render report.json` to refresh Markdown

## Commands

| Command | Purpose |
|---------|---------|
| `tads fetch <id>` | Download IETF plain text |
| `tads plan <file> --doc-id …` | Cost/mode preflight (no LLM) |
| `tads scan <file> --doc-id … -o <prefix>` | Full scan → `.json` + `.md` |
| `tads render <report.json>` | Re-render Markdown after review |

## Providers

Configure via environment (or `.env` in the project root):

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` / `GEMINI_API_KEY`
- `OLLAMA_HOST` / `OLLAMA_API_KEY`

`mock` provider is available for offline tests.

## Analysis scope options

TOC / front matter is **skipped by default**. Caps apply to the remaining body content and are honored by both `plan` and `scan`.

| Option | Purpose |
|--------|---------|
| `--include-front-matter` | Include TOC/preamble sections |
| `--max-sections N` | Analyze at most N body sections |
| `--max-chars N` | Cap analyzed document characters |
| `--max-input-tokens N` | Cap estimated **document input** tokens |
| `--max-tokens N` | Cap estimated **LLM spend** tokens (input+output) |
| `--max-cost-usd` | Cap estimated LLM spend in USD |

### Preflight coverage summary

`plan` / `scan` print:

- **document** — full parsed size (sections, chars, token estimate)
- **eligible** — after TOC/front-matter skip
- **analyzed** — after caps
- **coverage** — analyzed as % of eligible and of the full document

Example for a capped 3GPP plan:

```text
document: 1150 sections, 2,904,808 chars, ~726,202 tokens
eligible: 1149 sections (after skipping 1 front-matter/TOC), …
analyzed: 5 sections, 22,020 chars, ~5,505 tokens
coverage: sections 0.4% of eligible, chars 0.8% of eligible (0.8% of full document)
```

## Notes

- Whole-document analysis when the scoped text fits the model context; otherwise section-aware.
- Level-1 remediation directions only.
- Deterministic validation is a light scaffold (e.g. known horizon dates).
- Source documents are not retained unless you keep your own input files; reports are written only to `--output`.
- Scope settings are recorded in report `run` metadata (`max_sections`, `max_input_tokens`, `scope_notes`, …).

## Phase 1 backlog (still in scope)

These belong with the core scanner, not Phase 3 (structured elements) or Phase 2 (corpus adapters).

### Input formats (auto-convert → text)

| Format | Intent |
|--------|--------|
| `.txt` | Supported today |
| `.docx` | Auto-extract paragraphs/tables to plain text before plan/scan |
| `.pdf` | Auto-extract text (layout-aware enough for clauses) |
| Google Docs | Accept export (`.docx`/`.txt`) or Docs API/URL fetch later; do not require interactive Google login in MVP |

Conversion should produce an ephemeral or user-requested text artifact, then reuse the existing sectionizers. Prefer Word/DOCX extraction over PDF when both exist (better headings).

### Front matter / TOC handling

Implemented for analysis: detect TOC-style lines, fold them into preamble, skip by default; optional `--include-front-matter`.

### Analysis caps

Implemented: see **Analysis scope options** above.
