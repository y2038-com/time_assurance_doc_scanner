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

## Notes

- Whole-document analysis when the text fits the model context; otherwise section-aware.
- Level-1 remediation directions only.
- Deterministic validation is a light scaffold (e.g. known horizon dates).
- Source documents are not retained unless you keep your own input files; reports are written only to `--output`.
