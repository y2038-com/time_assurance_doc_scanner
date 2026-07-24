# Phase 1 — Core Scanner MVP

## Definition of done

A researcher can:

1. `tads fetch RFC5905` (or use a local `.txt` under `inputs/`)
2. `tads plan …` to see analysis mode + cost estimate
3. `tads scan …` to run BYOLLM analysis (writes `outputs/<doc_id>.json` + `.md`)
4. Review findings in JSON (edit `disposition` / `reviewer_notes`)
5. `tads render report.json` to refresh Markdown

## Commands

| Command | Purpose |
|---------|---------|
| `tads fetch <id>` | Download IETF plain text |
| `tads plan <file> --doc-id …` | Cost/mode preflight (no LLM) |
| `tads scan <file> --doc-id … [-o <prefix>]` | Full scan → `.json` + `.md` (default prefix: `outputs/<doc_id>`) |
| `tads render <report.json>` | Re-render Markdown after review |

## Providers

Configure via environment (or `.env` in the project root). Preferred knobs:

- `TADS_LLM_PROVIDER` — `ollama` (default), `openai`, `anthropic`, `gemini`, `mock`
- `TADS_MODEL` — model id for that provider

Provider credentials:

- Ollama Cloud (default): `OLLAMA_API_KEY` (`OLLAMA_HOST` defaults to `https://ollama.com`; set `http://127.0.0.1:11434` for local)
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` / `GEMINI_API_KEY`

`TADS_PROVIDER` is accepted as an alias for `TADS_LLM_PROVIDER`. See `.env.example` for copy-paste blocks. `mock` is available for offline tests.

Step-by-step `.env` examples, smoke-test commands, and **lessons learned** (which models work on free tiers, Snap vs official Ollama, Gemini 2.5 vs 3.6, OpenAI billing/429s) are in [QUICK_START.md](../QUICK_START.md).

### Smoke-tested combinations (capped RFC 5905)

| Provider | Model |
|----------|--------|
| `ollama` (Cloud) | `gpt-oss:120b` |
| `ollama` (local GPU) | `llama3.2:3b` |
| `gemini` | `gemini-3.6-flash` |
| `openai` | `gpt-4.1-mini` |
| `anthropic` | `claude-sonnet-4-5` |

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
Parked UX and deferred niceties (e.g. scan progress bar) live in [backlog.md](backlog.md).

### Input formats

| Format | Status |
|--------|--------|
| `.txt` / `.md` | Supported |
| `.docx` | Auto-convert via `python-docx` |
| `.pdf` | Auto-convert via `pymupdf` |
| `.zip` / `.tgz` | Extract preferred member (`.docx` > `.pdf` > `.txt`), or `--archive-member` |
| URL (`http`/`https`) | Download then convert (size-capped) |
| Google Docs | Not yet; export to docx/pdf/txt first |

`plan`, `scan`, and `convert` all accept a **local path or URL**.

```bash
tads convert ./spec.docx -o inputs/spec.txt
tads plan ./bundle.zip --doc-id "TS 23.501" --corpus 3gpp --max-sections 5
tads plan https://example.org/spec.pdf --doc-id ... --corpus ieee --save-text inputs/spec.txt
```

Ingest options:

| Option | Purpose |
|--------|---------|
| `--archive-member` | Choose a file inside zip/tgz |
| `--max-download-mb` | Max payload size (default 100; or `TADS_MAX_DOWNLOAD_MB`) |
| `--save-text PATH` | Persist converted plain text (ephemeral by default) |

### Front matter / TOC handling

Implemented for analysis: detect TOC-style lines, fold them into preamble, skip by default; optional `--include-front-matter`.

### Analysis caps

Implemented: see **Analysis scope options** above.
