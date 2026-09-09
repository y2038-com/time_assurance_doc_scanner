# Time Assurance Documentation Scanner

Open-source, AI-assisted scanner (`tads`) for long-horizon **time assurance** issues in standards and technical docs: explicit defects, implicit assumptions, missing assurance evidence, and related inconsistencies.

This repository is the open-source scanner engine and CLI — the primary asset.

**New here?** Follow **[QUICK_START.md](QUICK_START.md)** — install through a first scan in about 15 minutes (offline mock path, then optional real LLM).

## Status

**MVP (package 0.5.0)** with **Phase 2 corpus support**: Tier-1 adapters for IETF, ETSI, and 3GPP; remote fetch for W3C, ECMA, OASIS, and NIST; local-file stubs for ITU-T, IEEE, and ISO/IEC. The everyday workflow is `fetch` → `plan` → `scan` → human review → `render`.

**Default LLM:** Ollama Cloud (`gpt-oss:120b` when `OLLAMA_HOST` is unset). BYOLLM also supports local Ollama, OpenAI, Anthropic, Gemini, and an offline `mock` provider.

| Provider | Model (smoke-tested) | Notes |
|----------|----------------------|--------|
| Ollama Cloud | `gpt-oss:120b` | Free-tier friendly default |
| Ollama local | `llama3.2:3b` / `llama3.1:8b` | Use section caps; small context truncates whole RFCs |
| Gemini | `gemini-3.6-flash` | AI Studio key + Generative Language API + credits |
| OpenAI | `gpt-4.1-mini` | Needs billing/credits |
| Anthropic | `claude-sonnet-4-5` | Working end-to-end |
| Mock | (built-in) | No API key; plumbing / CI only |

## Privacy (read this)

Processing is **ephemeral by default**: the tool does not keep a private document store. That does **not** mean “never leaves your machine.”

- **Document text is sent to the LLM provider you configure** (Ollama Cloud, OpenAI, Anthropic, Gemini, or your local Ollama daemon).
- Reports are written only where you ask (`outputs/` by default). Prefer `mock` or local Ollama for sensitive drafts.

Details: [docs/privacy.md](docs/privacy.md).

## Principles

- Open source first; model-independent (BYOLLM)
- Corpus-aware, not keyword-driven
- Evidence-based; deterministic checks where possible
- Human review is authoritative — scan outputs are **candidates for review** (**validated finding** = human `disposition=accepted` only)
- Extensible beyond Y203x without redesign

## Limitations

TADS is an AI-assisted review aid, not an authoritative standards or compliance oracle. Treat outputs as **candidates for review**, not confirmed defects.

- False positives and false negatives are expected.
- Scope labels (`core` / `supporting` / `incidental` / `out_of_scope`) are advisory.
- Absence claims (“not addressed / no guidance”) can still be wrong.
- Results vary by model, provider, and analysis caps.
- PDF/HTML extraction and tables/figures may be incomplete.
- No findings ≠ “no time-assurance risk.”
- Experts should review severity and remediation before relying on results or submitting to standards bodies.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
# Optional PDF conversion (PyMuPDF): pip install -e ".[pdf]"

# Offline plumbing (no API key) — fetch needs network once:
tads fetch RFC5905
tads scan inputs/RFC5905.txt --doc-id RFC5905 --provider mock \
  --max-sections 2 --force-sections --overwrite -y
```

Then open `outputs/RFC5905.md`. For a real LLM scan and provider `.env` setup, use **[QUICK_START.md](QUICK_START.md)**.

Default install covers plain text, HTML, and `.docx`. PDF conversion needs the optional `[pdf]` extra (third-party PyMuPDF; see License).

| Folder | Purpose |
|--------|---------|
| `inputs/` | Source documents (`tads fetch` default; contents gitignored) |
| `outputs/` | Scan JSON + Markdown (contents gitignored) |

**Reading reports:** Markdown shows primary **candidates for review**; JSON is canonical (all candidates, dispositions, evidence, provenance). Edit dispositions in JSON, then `tads render outputs/RFC5905.json -f`.

## Docs

**Start here:** [QUICK_START.md](QUICK_START.md)

| Doc | Status | Purpose |
|-----|--------|---------|
| [docs/architecture.md](docs/architecture.md) | Current | High-level architecture |
| [docs/schemas.md](docs/schemas.md) | Current | Finding and output schemas |
| [docs/taxonomy.md](docs/taxonomy.md) | Current | Time assurance taxonomy |
| [docs/privacy.md](docs/privacy.md) | Current | Privacy and retention defaults |
| [docs/phase1.md](docs/phase1.md) | Current | MVP commands and review workflow |
| [docs/phase2.md](docs/phase2.md) | Current | Corpus adapters and fetch tiers |
| [docs/backlog.md](docs/backlog.md) | Roadmap | Parked ideas (not a ship commitment) |

**Historical / design (optional):** [docs/phase0.md](docs/phase0.md) (completed Phase 0 record), [docs/fetch_tier2_plan.md](docs/fetch_tier2_plan.md) (completed Tier-2 fetch plan), [docs/candidate_kind.md](docs/candidate_kind.md) (design note, not implemented), [docs/rfc5905_provider_compare.md](docs/rfc5905_provider_compare.md) (bake-off notes), [eval/corpus/README.md](eval/corpus/README.md) (bootstrap eval labels), [scripts/README.md](scripts/README.md) (optional 12-doc bench harness).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and PR expectations.
To report a vulnerability, use [SECURITY.md](SECURITY.md) (not a public issue).

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

Copyright (c) 2026 Y2038.com LLC

Optional PDF support (`pip install "time-assurance-doc-scanner[pdf]"`) pulls in
[PyMuPDF](https://pymupdf.readthedocs.io/), which is dual-licensed under AGPL-3.0
or an Artifex commercial license. Review PyMuPDF/Artifex terms if you enable that
extra. TADS itself remains Apache-2.0.
