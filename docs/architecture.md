# Architecture

This document describes the **current** high-level architecture of TADS. Early
implementation was sequenced as Phase 0 (foundations) then Phase 1 (scan MVP);
those labels appear below only as historical framing. Locked early decisions are
recorded in [phase0.md](phase0.md). For commands and corpus usage, see
[phase1.md](phase1.md) and [phase2.md](phase2.md).

## Goals

The foundations below are implemented and remain the architectural baseline:

1. Canonical finding and report models
2. Corpus and LLM abstractions
3. Document / section model
4. Prompt framework
5. Cost estimation and budget caps
6. Privacy defaults
7. Bootstrap evaluation harness

## High-level pipeline

```
Document Import
    → Corpus Identification (IETF RFC / I-D first; other corpora via adapters)
    → Structured Parsing
    → Analysis strategy (whole-document OR section-aware)
    → LLM semantic analysis (BYOLLM)
    → Optional deterministic validators (horizon calculator and related checks)
    → Structured Findings (canonical model; public default = candidates for review)
    → Markdown report + JSON report
    → Human review via edited JSON dispositions (`accepted` = validated finding)
```

Keywords may increase attention but never decide what is scanned. Prefer whole-document analysis when the document fits the model context window; otherwise analyze every semantic section.

## Package layout

```
src/tads/
  schemas/     # Finding, report, cost, taxonomy enums
  corpus/      # Corpus adapters (IETF, ETSI, 3GPP; Tier-2 fetch + local-file stubs)
  parsing/     # Document + section models
  ingest/      # Download, convert, archive extraction
  fetch.py     # Curated remote fetch orchestration
  llm/         # Provider-agnostic LLM layer (httpx-backed providers)
  prompts/     # Prompt templates and builders
  cost/        # Token/USD estimation and budgets
  privacy/     # Retention / logging policy
  eval/        # Evaluation harness utilities
  pipeline/    # Finding parse, validation hooks
  validators/  # Deterministic checks (e.g. horizon calculator)
  export/      # Report rendering helpers
  cli.py       # CLI entry (`fetch` / `plan` / `scan` / `render` / …)
```

## Design constraints carried forward

| Concern | Decision |
|---------|----------|
| Language | Python 3.11+ |
| Corpora | Tier 1: IETF, ETSI, 3GPP; Tier 2 fetch: W3C, ECMA, OASIS, NIST; Tier 2 local-file stubs: ITU-T, IEEE, ISO/IEC |
| MVP users | Security / time researchers |
| LLM providers (MVP) | Cloud Ollama (default), OpenAI, Anthropic, Gemini |
| Default LLM | `TADS_LLM_PROVIDER=ollama` (alias `TADS_PROVIDER`); `OLLAMA_HOST` defaults to `https://ollama.com`; cloud model `gpt-oss:120b` (free-tier friendly) unless `TADS_MODEL` is set. See `QUICK_START.md` for tested provider/model matrix. |
| Review UX | Generate report; humans edit JSON dispositions |
| Recommendations (MVP) | Level 1 direction only |
| Registry / MCP / multi-corpus analytics | Schema-ready; implement later (see Extension points) |

## Abstraction boundaries

### Corpus adapter

Describes document structure, clause organization, reference conventions, normative language, versioning, and editorial style. The scanner engine stays corpus-agnostic.

### LLM provider

Uniform interface: estimate tokens, complete chat, report usage. Concrete providers use a shared HTTP helper; optional SDKs are not required for the MVP backends.

### Deterministic validators

Separated from LLM interpretation. Every finding can carry:

- `machine_interpretation` — what the model inferred
- `validation_status` — deterministic check only (unverified / verified / failed / not_applicable); JSON `verified` ≠ human-validated
- `validation_detail` — deterministic evidence when present
- `source_verified` / `source_verification_detail` — evidence quote found in analyzed text

Public Markdown derives an **assurance status** (`candidate`, `source_verified`, `deterministically_validated`, `human_confirmed`, …). Reserve **validated finding** for human-confirmed (`disposition=accepted`). See `tads.schemas.assurance`.

**Scope relevance:** Findings carry `scope_relevance` (`core` / `supporting` / `incidental` / `out_of_scope`) plus optional `scope_rationale`. Scope is orthogonal to severity/confidence/validation/disposition. Markdown presents core+supporting as primary candidates, incidental in a lower section, and omits out_of_scope by default (JSON keeps everything). Prompt framework ≥ 0.4.0 asks the model to classify honestly; missing/invalid scope defaults to `core`.

**Absence-claim discipline (prompt ≥ 0.5.0):** Primary-pass prompts require searching the analyzed text before claiming “not addressed / undefined / no guidance,” preferring narrowed gaps and dual-sided quotes over global silence. A structured second-pass counterevidence enrichment remains deferred (see `docs/backlog.md`).

**Horizon calculator:** `tads.validators.horizon.validate_time_representation` computes fixed-width bounds and epoch-relative instants. Findings may carry optional `time_representation` + `horizon_validation`; the scan pipeline asks the LLM for structured params when applicable (null if unknown—no guessing), parses them, and runs `apply_horizon_validation` without changing disposition. Markdown renders a separate **Deterministic validation** section.

The MVP ships schema support plus sample deterministic validators (including the horizon calculator); a broader validator suite remains an extension point.

### Cost control

Before any paid call, estimate tokens and USD (when pricing is known). Enforce `--max-cost-usd` / `--max-tokens`. Fail closed if the estimate exceeds the budget.

### Privacy

Default mode is **ephemeral**: process in memory; persist only user-requested outputs. Document bodies are not written to logs. Content is sent only to the user-selected LLM provider.

## Extension points (roadmap)

- Deeper corpus adapters beyond current Tier 1 / fetch-enabled Tier 2
- Structured element analysis (tables, figures, bit layouts)
- Recommendation Levels 2–3
- Finding registry and corpus-scale analytics
- MCP tool surface
- Cross-document and historical version analysis
- Candidate kind / review-stance taxonomy ([candidate_kind.md](candidate_kind.md))

Architecture should not need redesign for these; they plug into the canonical finding model and adapter interfaces.
