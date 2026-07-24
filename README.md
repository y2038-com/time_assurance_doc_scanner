# Time Assurance Documentation Scanner

Open-source, AI-assisted scanner that finds explicit time-related defects, implicit long-horizon assumptions, missing assurance evidence, and documentation inconsistencies in standards and technical docs.

This repository is the scanner engine. A hosted reference implementation may later appear on [y2038.ai](https://y2038.ai); the open-source scanner remains the primary asset.

## Status

**Phase 2 (Corpus Awareness)** — Tier-1 adapters for IETF, ETSI, and 3GPP; Tier-2 stubs (ITU-T, IEEE, W3C, OASIS, NIST, ISO/IEC, ECMA). Phase 1 scan CLI remains the primary workflow, with TOC skip and analysis-scope caps for large specs. Default LLM provider is **Ollama Cloud** (`OLLAMA_HOST` defaults to `https://ollama.com`).

## Principles

- Open source first; model-independent (BYOLLM)
- Corpus-aware, not keyword-driven
- Evidence-based; deterministic validation where possible
- Human review is authoritative; recommendations are advisory
- Extensible beyond Y203x without redesign
- Documents are private by default (ephemeral processing; user-controlled outputs)

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Optional: copy .env.example → .env and set OLLAMA_API_KEY (Cloud) or other keys

tads corpora
tads fetch RFC5905
tads convert ./spec.docx -o inputs/spec.txt
tads plan inputs/RFC5905.txt --doc-id RFC5905 --max-cost-usd 1.00
tads scan inputs/RFC5905.txt --doc-id RFC5905 --max-cost-usd 1.00 --yes
# Edit dispositions in outputs/RFC5905.json, then:
tads render outputs/RFC5905.json

# Local path or URL; archives prefer .docx over .pdf/.txt
tads plan path/to/23501.zip --doc-id "TS 23.501" --corpus 3gpp \
  --max-sections 5 --max-input-tokens 20000
```

Workspace folders (gitignored contents; READMEs committed):

| Folder | Purpose |
|--------|---------|
| `inputs/` | Fetched/converted source documents (`tads fetch` default) |
| `outputs/` | Scan JSON/Markdown (`tads scan` default) |
### Useful `plan` / `scan` / `convert` options

| Option | Meaning |
|--------|---------|
| `--corpus` | Corpus adapter (`ietf`, `etsi`, `3gpp`, …); auto-detect when omitted |
| `--include-front-matter` | Keep TOC/preamble in analysis (skipped by default) |
| `--max-sections N` | Analyze at most N body sections |
| `--max-chars N` | Cap analyzed document characters |
| `--max-input-tokens N` | Cap estimated **document input** tokens |
| `--max-tokens N` | Cap estimated **LLM spend** tokens (input+output) |
| `--max-cost-usd` | Cap estimated LLM spend in USD |
| `--archive-member` | Member inside `.zip`/`.tgz` |
| `--max-download-mb` | Max download/local payload size (default 100) |
| `--save-text PATH` | Persist converted plain text (ephemeral by default). If `PATH` is a directory, writes `<stem>.txt` inside it. |
| `--overwrite` / `-f` | On `fetch` / `convert` / `scan`, overwrite existing outputs without prompting |

`plan` prints document / eligible / analyzed totals and coverage percentages before any LLM call.

IETF tip: prefer `https://www.rfc-editor.org/rfc/rfcNNNN.txt` (or `tads fetch RFCNNNN`). Links from `tools.ietf.org` / datatracker PDF paths are rewritten to the RFC Editor text mirror automatically (those hosts often redirect to login).

Offline smoke test (no API key):

```bash
tads scan path/to/doc.txt --doc-id RFC9999 --provider mock --yes
```

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/architecture.md](docs/architecture.md) | Overall architecture |
| [docs/taxonomy.md](docs/taxonomy.md) | Time assurance taxonomy |
| [docs/schemas.md](docs/schemas.md) | Finding and output schemas |
| [docs/privacy.md](docs/privacy.md) | Privacy and retention defaults |
| [docs/phase0.md](docs/phase0.md) | Phase 0 deliverables |
| [docs/phase1.md](docs/phase1.md) | Phase 1 MVP usage + backlog |
| [docs/phase2.md](docs/phase2.md) | Corpus adapters and tiers |
| [eval/corpus/README.md](eval/corpus/README.md) | Bootstrap evaluation corpus |

## License

Deferred until first public visibility. Private repo: https://github.com/johnlange2/time_assurance_doc_scanner
