# Time Assurance Documentation Scanner

Open-source, AI-assisted scanner that finds explicit time-related defects, implicit long-horizon assumptions, missing assurance evidence, and documentation inconsistencies in standards and technical docs.

This repository is the scanner engine. A hosted reference implementation may later appear on [y2038.ai](https://y2038.ai); the open-source scanner remains the primary asset.

## Status

**Phase 1 (Core Scanner MVP)** — CLI scans one IETF RFC / Internet-Draft, emits Markdown + JSON, human review via JSON dispositions.

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

# Optional: copy .env.example → .env and set provider keys

tads fetch RFC5905
tads plan .tads/inputs/RFC5905.txt --doc-id RFC5905 --provider openai --max-cost-usd 1.00
tads scan .tads/inputs/RFC5905.txt --doc-id RFC5905 --provider openai --max-cost-usd 1.00 -o out/RFC5905 --yes
# Edit dispositions in out/RFC5905.json, then:
tads render out/RFC5905.json -o out/RFC5905.md
```

Offline smoke test (no API key):

```bash
tads scan path/to/doc.txt --doc-id RFC9999 --provider mock -o out/demo --yes
```

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/architecture.md](docs/architecture.md) | Overall architecture |
| [docs/taxonomy.md](docs/taxonomy.md) | Time assurance taxonomy |
| [docs/schemas.md](docs/schemas.md) | Finding and output schemas |
| [docs/privacy.md](docs/privacy.md) | Privacy and retention defaults |
| [docs/phase0.md](docs/phase0.md) | Phase 0 deliverables |
| [docs/phase1.md](docs/phase1.md) | Phase 1 MVP usage |
| [eval/corpus/README.md](eval/corpus/README.md) | Bootstrap evaluation corpus |

## License

Deferred until first public visibility. Private repo: https://github.com/johnlange2/time_assurance_doc_scanner
