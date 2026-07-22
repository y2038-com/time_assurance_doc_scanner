# Time Assurance Documentation Scanner

Open-source, AI-assisted scanner that finds explicit time-related defects, implicit long-horizon assumptions, missing assurance evidence, and documentation inconsistencies in standards and technical docs.

This repository is the scanner engine. A hosted reference implementation may later appear on [y2038.ai](https://y2038.ai); the open-source scanner remains the primary asset.

## Status

**Phase 0 (Research & Architecture)** — schemas, abstractions, prompt framework, cost model, privacy defaults, and bootstrap evaluation harness.

Phase 1 will add a CLI that scans one IETF RFC / Internet-Draft and emits Markdown + JSON findings for human review (edit dispositions in JSON).

## Principles

- Open source first; model-independent (BYOLLM)
- Corpus-aware, not keyword-driven
- Evidence-based; deterministic validation where possible
- Human review is authoritative; recommendations are advisory
- Extensible beyond Y203x without redesign
- Documents are private by default (ephemeral processing)

## Quick start (Phase 0)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
tads --help
```

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/architecture.md](docs/architecture.md) | Overall architecture |
| [docs/taxonomy.md](docs/taxonomy.md) | Time assurance taxonomy |
| [docs/schemas.md](docs/schemas.md) | Finding and output schemas |
| [docs/privacy.md](docs/privacy.md) | Privacy and retention defaults |
| [docs/phase0.md](docs/phase0.md) | Phase 0 deliverables and decisions |
| [eval/corpus/README.md](eval/corpus/README.md) | Bootstrap evaluation corpus |

## License

Deferred until first public visibility. The empty GitHub repo is currently private: https://github.com/johnlange2/time_assurance_doc_scanner
