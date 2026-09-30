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
  corpus/      # Corpus adapters (IETF, ETSI, 3GPP, generic; Tier-2 fetch + local-file stubs)
  parsing/     # Document + section models
  ingest/      # Download, convert, archive extraction (hop-by-hop redirect checks)
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
| Corpora | Tier 1: IETF, ETSI, 3GPP; generic analysis profile for unidentified plan/scan ids; Tier 2 fetch: W3C, ECMA, OASIS, NIST; Tier 2 local-file stubs: ITU-T, IEEE, ISO/IEC |
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

Prompt framework ≥ 0.7.0 sends trusted scanner policy, output schema, task instructions, and corpus-profile guidance on the provider system channel. The user message is only an untrusted-data record (document or section identity and text, or a prior model payload for repair). Character counts and envelope labels are framing hints, not a guarantee against semantic prompt injection. TADS does not give the model tools or side-effect APIs. After think-block and single-fence cleanup, output must be one complete `{"findings": [...]}` object; mixed or invalid output gets one repair attempt, then the scan fails closed. Prompt framework ≥ 0.7.1 requires every listed model-authored field on each finding item. One malformed item fails the whole response or section; valid siblings are not kept, and a malformed item list is not rewritten as `{"findings": []}`. A literal `{"findings": []}` is the only successful zero-finding result. Schema-valid findings remain candidates for review.

### Deterministic validators

Separated from LLM interpretation. Every finding can carry:

- `machine_interpretation` — what the model inferred
- `validation_status` — deterministic check only (unverified / verified / failed / not_applicable); JSON `verified` ≠ human-validated
- `validation_detail` — deterministic evidence when present
- `source_verified` / `source_verification_detail` — evidence quote found in analyzed text

Public Markdown derives an **assurance status** (`candidate`, `source_verified`, `deterministically_validated`, `human_confirmed`, …). Reserve **validated finding** for human-confirmed (`disposition=accepted`). See `tads.schemas.assurance`. Markdown headings are TADS-authored; untrusted strings are escaped or shown in literal fences. Canonical JSON remains the authority. Clickable links are not emitted.

**Scope relevance:** Findings carry `scope_relevance` (`core` / `supporting` / `incidental` / `out_of_scope`) plus `scope_rationale`. Scope is orthogonal to severity/confidence/validation/disposition. Markdown presents core+supporting as primary candidates, incidental in a lower section, and omits out_of_scope by default (JSON keeps everything). Prompt framework ≥ 0.7.1 requires those fields on each model finding. Missing or invalid scope fails the response rather than defaulting to `core`. Older saved JSON without scope still loads as `core`.

**Absence-claim discipline (prompt ≥ 0.5.0):** Primary-pass prompts require searching the analyzed text before claiming “not addressed / undefined / no guidance,” preferring narrowed gaps and dual-sided quotes over global silence. A structured second-pass counterevidence enrichment remains deferred (see `docs/backlog.md`).

**Horizon calculator:** `tads.validators.horizon.validate_time_representation` computes fixed-width bounds and epoch-relative instants. Findings may carry optional `time_representation` + `horizon_validation`; the scan pipeline asks the LLM for structured params when applicable (null if unknown—no guessing), parses them, and runs `apply_horizon_validation` without changing disposition. Markdown renders a separate **Deterministic validation** section.

The MVP ships schema support plus sample deterministic validators (including the horizon calculator); a broader validator suite remains an extension point.

### Cost control

Before any paid call, estimate tokens and USD (when pricing is known). Enforce `--max-cost-usd` / `--max-tokens`. Fail closed if the estimate exceeds the budget.

### Privacy

Default mode is **ephemeral**: process in memory; persist only user-requested outputs. Document bodies are not written to logs. Content is sent only to the user-selected LLM provider.

Canonical JSON and Markdown reports contain findings and evidence by design. Ordinary CLI and progress output does not. `--save-raw-on-error` is an opt-in troubleshooting file beside the JSON report (disabled by default; 256 KiB cap; sensitive as a whole; no field-level sanitation). JSON and Markdown are separate writes, not an atomic transaction.

Ordinary provider errors include allowlisted classification only. Provider diagnostic URLs keep scheme/host/path and drop userinfo, query, and fragment. That policy is separate from document provenance sanitation. Independently enabled httpx/httpcore DEBUG logging and shell history are not TADS-owned output.

Retry counts, timeout defaults, and converter limits are unchanged here. Residual risks include retries multiplying wall-clock time, converters without an independent CPU deadline, uncapped provider response loading, DNS rebinding, and third-party HTTP DEBUG.

### Remote ingest

Remote HTTP(S) ingest follows redirects manually (`follow_redirects=False`). Each redirect target (status 301, 302, 303, 307, or 308) is validated before the next request. Other 3xx responses, including 304, are not treated as navigational redirects. HTTPS-to-HTTP redirects are refused hop by hop. `--allow-private-url` only permits private-address destinations; it does not allow an HTTPS downgrade.

`source_uri` is the sanitized URL of the first HTTP request after any document rewrite or fallback. `retrieved_uri` is the sanitized final serving URL when that locator differs from `source_uri`. TADS does not pin the DNS-validated IP address to the connection. DNS rebinding between validation and connect remains a residual risk. Independently enabled httpx/httpcore debug logging may expose complete URLs.

## Extension points (roadmap)

- Deeper corpus adapters beyond current Tier 1 / fetch-enabled Tier 2
- Structured element analysis (tables, figures, bit layouts)
- Recommendation Levels 2–3
- Finding registry and corpus-scale analytics
- MCP tool surface
- Cross-document and historical version analysis
- Candidate kind / review-stance taxonomy ([candidate_kind.md](candidate_kind.md))

Architecture should not need redesign for these; they plug into the canonical finding model and adapter interfaces.
