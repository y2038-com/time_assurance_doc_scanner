# Time Assurance Documentation Scanner

Open-source, AI-assisted scanner that finds explicit time-related defects, implicit long-horizon assumptions, missing assurance evidence, and documentation inconsistencies in standards and technical docs.

This repository is the scanner engine. A hosted reference implementation may later appear on [y2038.ai](https://y2038.ai); the open-source scanner remains the primary asset.

**New here?** Start with [QUICK_START.md](QUICK_START.md) (install, `.env`, provider setup, and tested models).

## Status

**Phase 2 (Corpus Awareness)** — Tier-1 adapters for IETF, ETSI, and 3GPP; Tier-2 stubs (ITU-T, IEEE, W3C, OASIS, NIST, ISO/IEC, ECMA). Phase 1 scan CLI remains the primary workflow, with TOC skip and analysis-scope caps for large specs.

**Default LLM:** Ollama Cloud (`gpt-oss:120b` when `OLLAMA_HOST` is unset). BYOLLM also supports local Ollama, OpenAI, Anthropic, and Gemini.

### Providers smoke-tested

| Provider     | Model                         | Notes                                                                                                                |
|--------------|-------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Ollama Cloud | `gpt-oss:120b`                | Free-tier friendly default; whole-doc RFC 5905 OK                                                                    |
| Ollama local | `llama3.2:3b` / `llama3.1:8b` | Official install + `ollama ps` → GPU; default context truncates whole RFCs — use section caps; watch laptop thermals |
| Gemini       | `gemini-3.6-flash`            | AI Studio key on project with Generative Language API enabled + prepaid credits; not `gemini-2.5-flash` for new keys |
| OpenAI       | `gpt-4.1-mini`                | Needs billing/credits (else 429 `insufficient_quota`)                                                                |
| Anthropic    | `claude-sonnet-4-5`           | Working end-to-end                                                                                                   |

## Principles

- Open source first; model-independent (BYOLLM)
- Corpus-aware, not keyword-driven
- Evidence-based; deterministic validation where possible
- Human review is authoritative; scan outputs are **candidates for review** (reserve **validated finding** for human-confirmed items)
- Extensible beyond Y203x without redesign
- Documents are private by default (ephemeral processing; user-controlled outputs)

## Limitations

TADS is an AI-assisted review tool, not an authoritative standards analysis or compliance tool. Its output should be treated as **candidates for review**, not confirmed defects.

Current limitations include:

- **False positives and false negatives:** LLMs may identify issues that are not defects, and may miss relevant issues.
- **Model variability:** Results can differ across models, providers, model versions, and analysis settings.
- **Incomplete context:** Guidance elsewhere in a document or in referenced standards may qualify or resolve an apparent issue.
- **Limited deterministic validation:** TADS can verify selected calculations and representation boundaries, but not all model-generated conclusions can currently be validated automatically.
- **Document extraction limitations:** PDF conversion, tables, figures, equations, and other structured content may be incomplete or interpreted incorrectly.
- **No assurance from absence of findings:** A document with no reported candidates should not be considered free of time-related risks or assurance gaps.
- **Human review remains essential:** Technical conclusions, severity assessments, and proposed remediation should be reviewed by appropriate subject-matter experts before being relied upon or submitted to standards bodies.

TADS is intended to augment human standards review by making large-scale, consistent analysis more practical, not to replace expert judgment.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then set provider keys — see QUICK_START.md

tads fetch RFC5905
tads plan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections
tads scan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections --overwrite -y
tads render outputs/RFC5905.json
```

Full provider `.env` blocks, GPU checks, and ingest tips: **[QUICK_START.md](QUICK_START.md)**.

Workspace folders (gitignored contents; READMEs committed):

| Folder     | Purpose                                                   |
|------------|-----------------------------------------------------------|
| `inputs/`  | Fetched/converted source documents (`tads fetch` default) |
| `outputs/` | Scan JSON/Markdown (`tads scan` default)                  |

### Useful `plan` / `scan` / `convert` options

| Option                                | Meaning                                                                                                       |
|---------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `--corpus`                            | Corpus adapter (`ietf`, `etsi`, `3gpp`, …); auto-detect when omitted                                          |
| `--include-front-matter`              | Keep TOC/preamble in analysis (skipped by default)                                                            |
| `--include-index-and-acknowledgments` | Keep Index and Acknowledgments in analysis (skipped by default)                                               |
| `--max-sections N`                    | Analyze at most N body sections                                                                               |
| `--max-chars N`                       | Cap analyzed document characters                                                                              |
| `--max-input-tokens N`                | Cap estimated **document input** tokens                                                                       |
| `--max-tokens N`                      | Cap estimated **LLM spend** tokens (input+output)                                                             |
| `--max-cost-usd`                      | Cap estimated LLM spend in USD                                                                                |
| `--archive-member`                    | Member inside `.zip`/`.tgz`                                                                                   |
| `--max-download-mb`                   | Max download/local payload size (default 100)                                                                 |
| `--save-text PATH`                    | Persist converted plain text (ephemeral by default). If `PATH` is a directory, writes `<stem>.txt` inside it. |
| `--overwrite` / `-f`                  | On `fetch` / `convert` / `scan`, overwrite existing outputs without prompting                                 |
| `-y` / `--yes`                        | On `scan`, skip cost confirmation                                                                             |

`plan` prints document / eligible / analyzed totals and coverage percentages before any LLM call. It accepts `--overwrite` / `-y` / `-o` for script parity with `scan` but ignores them (plan does not write reports).

For side-by-side provider runs, use `-o outputs/<doc>__<provider>__<model>` (replace `:` in model ids with `-`). Details in [QUICK_START.md](QUICK_START.md).

IETF tip: prefer `https://www.rfc-editor.org/rfc/rfcNNNN.txt` (or `tads fetch RFCNNNN`). Links from `tools.ietf.org` / datatracker PDF paths are rewritten to the RFC Editor text mirror automatically (those hosts often redirect to login).

Offline smoke test (no API key):

```bash
tads scan inputs/RFC5905.txt --doc-id RFC5905 --provider mock --overwrite -y
```

## Docs

| Doc                                                                  | Purpose                                   |
|----------------------------------------------------------------------|-------------------------------------------|
| [QUICK_START.md](QUICK_START.md)                                     | Install, `.env`, providers, tested models |
| [docs/architecture.md](docs/architecture.md)                         | Overall architecture                      |
| [docs/taxonomy.md](docs/taxonomy.md)                                 | Time assurance taxonomy                   |
| [docs/schemas.md](docs/schemas.md)                                   | Finding and output schemas                |
| [docs/privacy.md](docs/privacy.md)                                   | Privacy and retention defaults            |
| [docs/phase0.md](docs/phase0.md)                                     | Phase 0 deliverables                      |
| [docs/phase1.md](docs/phase1.md)                                     | Phase 1 MVP usage + in-scope backlog      |
| [docs/phase2.md](docs/phase2.md)                                     | Corpus adapters and tiers                 |
| [docs/backlog.md](docs/backlog.md)                                   | Parked / lower-priority ideas             |
| [docs/rfc5905_provider_compare.md](docs/rfc5905_provider_compare.md) | RFC 5905 multi-provider bake-off          |
| [eval/corpus/README.md](eval/corpus/README.md)                       | Bootstrap evaluation corpus               |

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

Copyright (c) 2026 Y2038.com LLC
