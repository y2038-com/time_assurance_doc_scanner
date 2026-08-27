# Architecture (Phase 0)

## Goals

Phase 0 locked the approach below; Phase 1 implements the scan pipeline on top of it.

1. Canonical finding and report models
2. Corpus and LLM abstractions
3. Document / section model
4. Prompt framework
5. Cost estimation and budget caps
6. Privacy defaults
7. Bootstrap evaluation harness

## High-level pipeline (Phase 1 target)

```
Document Import
    → Corpus Identification (IETF RFC / I-D first)
    → Structured Parsing
    → Analysis strategy (whole-document OR section-aware)
    → LLM semantic analysis (BYOLLM)
    → Optional deterministic validators (scaffold in Phase 1)
    → Structured Findings (canonical model; public default = candidates for review)
    → Markdown report + JSON report
    → Human review via edited JSON dispositions (`accepted` = validated finding)
```

Keywords may increase attention but never decide what is scanned. Prefer whole-document analysis when the document fits the model context window; otherwise analyze every semantic section.

## Package layout

```
src/tads/
  schemas/     # Finding, report, cost, taxonomy enums
  corpus/      # Corpus adapters (IETF, ETSI, 3GPP; Tier-2 fetch + stubs)
  parsing/     # Document + section models
  llm/         # Provider-agnostic LLM layer
  prompts/     # Prompt templates and builders
  cost/        # Token/USD estimation and budgets
  privacy/     # Retention / logging policy
  eval/        # Evaluation harness utilities
  pipeline/    # Orchestration stubs for Phase 1
  cli.py       # CLI entry (Phase 0: info commands)
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
| Recommendations in Phase 1 | Level 1 direction only |
| Registry / MCP / multi-corpus | Schema-ready; implement in later phases |
| y2038.ai | Out of this repo; hosted reference later |

## Abstraction boundaries

### Corpus adapter

Describes document structure, clause organization, reference conventions, normative language, versioning, and editorial style. The scanner engine stays corpus-agnostic.

### LLM provider

Uniform interface: estimate tokens, complete chat, report usage. Provider SDKs are optional dependencies behind the interface; Phase 0 ships interface + stubs.

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

Phase 1 includes schema + 1–2 sample validators; Phase 4 expands the suite.

### Cost control

Before any paid call, estimate tokens and USD (when pricing is known). Enforce `--max-cost-usd` / `--max-tokens`. Fail closed if the estimate exceeds the budget.

### Privacy

Default mode is **ephemeral**: process in memory; persist only user-requested outputs. Document bodies are not written to logs. Content is sent only to the user-selected LLM provider.

## Extension points (later phases)

- Additional corpus adapters (ETSI, 3GPP, …)
- Structured element analysis (tables, figures, bit layouts)
- Recommendation Levels 2–3
- Finding registry and corpus-scale analytics
- MCP tool surface
- Cross-document and historical version analysis
- Candidate kind / review-stance taxonomy ([candidate_kind.md](candidate_kind.md))

Architecture should not need redesign for these; they plug into the canonical finding model and adapter interfaces.
