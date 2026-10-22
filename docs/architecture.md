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
  output.py    # Terminal verbosity policy and plan presentation
  progress.py  # Scan console timestamps, live TTY progress, monotonic runtime
  cli.py       # CLI entry (`fetch` / `plan` / `scan` / `render` / …)
```

`tads scan` console telemetry (UTC ISO timestamps, live single-line progress on
interactive terminals, line-oriented progress when redirected, monotonic total
runtime) is implemented in `progress.py` and is **not** written into public
JSON/Markdown reports. Human plan text is formatted by `output.py` from
structured `ScanPlan` data. Stored `cost_estimate.notes` in JSON reports are
unchanged.

### Terminal verbosity

`tads plan` and `tads scan` share one policy: `quiet < normal < verbose < debug`.
Normal is the default. `--quiet`, `--verbose`, and `--debug` are mutually
exclusive; conflicting combinations exit 2 before ingest or provider calls.

| Level | Terminal shows |
| --- | --- |
| quiet | Safety, coverage, cost, and failure warnings; errors; final complete / partial / failed status; output paths; candidate count; runtime. No routine planning dump and no live chunk progress. Does not skip confirmation. |
| normal | Document/scope/coverage, concise request and pricing summaries (repeated per-request notes aggregated by controlled category), important warnings, live progress, repair / correction / recovery activity, completion. |
| verbose | Normal plus per-request tokens, pricing tier, estimated cost, assigned sections, and packing detail. |
| debug | Verbose plus sanitized planner diagnostics (chunk opaque refs and char offsets, shared overhead, structured-output capability, recovery eligibility/budget). Pipeline debug events use a typed `min_level` / `emit_at` path; message text cannot select a verbosity by starting with `debug:`. |

Debug output is not raw diagnostics. `--debug` and `--save-raw-on-error` are
independent. No verbosity level prints API keys, authorization headers,
complete prompts, document bodies, evidence quotes for diagnostics, raw model
responses, raw provider error bodies, URL credentials/queries/fragments, or
arbitrary exception representations.

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

Prompt framework ≥ 0.7.0 sends trusted scanner policy, output schema, task instructions, and corpus-profile guidance on the provider system channel. The user message is only an untrusted-data record (document or section identity and text, or a prior model payload for repair). Character counts and envelope labels are framing hints, not a guarantee against semantic prompt injection. TADS does not give the model tools or side-effect APIs. After think-block and single-fence cleanup, output must be one complete `{"findings": [...]}` object; mixed or invalid envelopes may get one repair attempt (duplicate JSON object keys and numeric-policy failures fail closed without repair), then the scan fails closed. Prompt framework ≥ 0.7.1 requires every listed model-authored field on each finding item. Framework ≥ 0.7.2 treats `recommendation_level1`, `time_representation`, `section_id`, and `section_title` as nullable (omitted or JSON null); other listed fields remain required. Framework ≥ 0.8.0 requires schema `0.3.0` source-stated `claimed_horizon` as `{value, precision}` or null, and states that `domains` is a closed canonical vocabulary (finding types, `privacy`, `security`, and near-synonyms are invalid). Framework ≥ 0.8.1 requires `width_bits` to be the source-stated bit count copied exactly, not concatenated with an epoch year or nearby number, and not `2^width` or a maximum value; unknown width is JSON null or omitted. One malformed item fails the whole response or section; valid siblings are not kept, and a malformed item list is not rewritten as `{"findings": []}`. A literal `{"findings": []}` is the only successful zero-finding result. Schema-valid findings remain candidates for review.

OpenAI request shape is driven by a narrow per-model policy (`tads.llm.openai_models`): completion-token parameter, reasoning-effort support, sampling compatibility, and short/long pricing thresholds. GPT-4.1 Mini keeps `max_tokens` and temperature; opt-in GPT-6 Luna uses `max_completion_tokens` and `reasoning_effort` (effective default `none`). When effort is not `none`, sampling parameters are omitted. Public report schema **0.4.0** records effective `run.reasoning_effort` and optional `actual_usage.reasoning_tokens`. Planning and Luna pricing-tier selection share one conservative per-request input overhead (`REQUEST_OVERHEAD_TOKENS` = 6000): `available_document_tokens = context_window - max_output_tokens - request_overhead_tokens`. That heuristic covers the system prompt, corpus guidance, and native structured-output schema (not a measured tokenizer count; raised after prompt 0.9.0 chunk instructions measured ≈4700 tokens for system+OpenAI schema, with margin above the RFC868 smoke gap). Chunk user-channel framing is estimated from the same live serialization used at execution. Section-aware analysis automatically packs adjacent analyzed sections into deterministic chunks (default max 8 per request; `--no-section-chunking` restores one request per section; `--max-sections-per-request` applies only to section-aware mode). Every `ParsedDocument` normalizes section IDs to be unique document-local locators before scope/planning (first occurrence kept; later collisions get `--2`, `--3`, … without colliding with preexisting IDs). Clause sectionization also drops leader-less W3C Table of Contents stubs after a `Table of Contents` marker. Chunk prompts use TADS-controlled opaque section references (`S0001`…) in trusted instructions; source titles and bodies stay in the untrusted user envelope. Whole-document prompts remain free of chunk-only opaque-reference rules. Local Policy A requires exact opaque membership and section-local evidence quotes, then substitutes the normalized canonical section IDs/titles; character offsets publish only via authoritative section→document mapping. Prompt framework **0.9.1** requires section-aware evidence quotes to be exact contiguous verbatim text from the claimed opaque section (copy extraction spacing; use `evidence: []` when uncertain; no paraphrases in `quote`). After a section-local evidence-grounding miss—and only when the primary has not already used its one secondary model request—TADS may issue one bounded semantic correction that regenerates the complete chunk response from the same trusted allowlist and the same untrusted section envelope (never attaching the invalid prior JSON, rejected quotes, sibling identities, or exception text). JSON envelope repair and semantic correction share that single secondary slot (`repair_request_count` ≤ `analysis_request_count`). The replacement must pass full Policy A; TADS does not salvage siblings from an invalid parent, reassign sections, accept an invalid quote, or strip invalid fields. When a section-aware chunk still fails with an allowlisted model-output validation error after that local slot is exhausted, TADS discards the invalid parent and may bisect the chunk at existing section boundaries (nonempty contiguous halves, original order). Only failed descendants split, down to a single section. Successful siblings and later original chunks are kept. A permanently failed singleton contributes no findings and is recorded as unanalyzed coverage. Adaptive recovery is bounded: the binary split tree is finite, and `--max-recovery-requests` (default 64; 0 disables splitting) counts every recovery-tree `complete()` call including child primaries, JSON repairs, and semantic corrections. Auth, transport, rate-limit, budget, ingest, unknown, interrupt, and output-writing failures are not eligible and still abort. If at least one planned section was analyzed successfully, TADS publishes an explicitly partial schema **0.6.0** report (`analysis_status=partial`, `coverage_complete=false`) and `tads scan` exits 3. If no planned section succeeds, the scan still fails without a report. Whole-document analysis does not use adaptive recovery. The chunk packer still rejects duplicate canonical IDs as a defensive assertion. Public report schema **0.6.0** records 0.5.0 batching provenance plus recovery/partial-execution fields. `--max-cost-usd` is an estimate-based preflight guard, not a hard billing cap; estimates exclude secondary requests and adaptive-recovery requests. Cost estimates for Luna apply the 272k-input short/long rates per request; non-`none` estimates are lower bounds until completion usage is known. Cached-input costs are not modeled.

OpenAI Chat Completions on `api.openai.com` and Gemini `generateContent` may attach a provider-native JSON schema derived from that model-facing contract when `supports_native_structured_output()` is true. Capability is endpoint-level, not a claim that every model name is known capable. Custom `OPENAI_BASE_URL` proxies stay on the prompt-only path. Native schema is an upstream reliability mechanism, not a security or acceptance boundary: returned model text still goes through local parsing and Policy A validation. Model-output `width_bits` is a JSON integer from 1 through 256 (or null); Gemini schema may include those bounds, OpenAI structured output strips `minimum`/`maximum`, and Policy A remains authoritative either way. Fallback without the native option happens only when the provider unambiguously says the structured-output parameter or feature is unknown or unsupported. A nonempty error `param` must identify that native field; schema-keyword, supplied-schema, and unrelated-parameter errors fail closed. Ollama, Anthropic, and mock stay on the existing JSON-output path. Public report schema is `0.6.0` (legacy `0.1.0`–`0.5.0` reports still load). Public `TimeRepresentationParams.width_bits` stays unbounded so older reports load.

### Deterministic validators

Separated from LLM interpretation. Every finding can carry:

- `machine_interpretation` — what the model inferred
- `validation_status` — deterministic check only (unverified / verified / failed / not_applicable); JSON `verified` ≠ human-validated
- `validation_detail` — deterministic evidence when present
- `source_verified` / `source_verification_detail` — evidence quote found in analyzed text (exact match first; flexible grounding also allows controlled whitespace before closing punctuation and before mid-word apostrophes, without rewriting the stored quote or publishing another section's span)

Public Markdown derives an **assurance status** (`candidate`, `source_verified`, `deterministically_validated`, `human_confirmed`, …). Reserve **validated finding** for human-confirmed (`disposition=accepted`). See `tads.schemas.assurance`. Markdown headings are TADS-authored; untrusted strings are escaped or shown in literal fences. Canonical JSON remains the authority. Clickable links are not emitted.

**Scope relevance:** Findings carry `scope_relevance` (`core` / `supporting` / `incidental` / `out_of_scope`) plus `scope_rationale`. Scope is orthogonal to severity/confidence/validation/disposition. Markdown presents core+supporting as primary candidates, incidental in a lower section, and omits out_of_scope by default (JSON keeps everything). Prompt framework ≥ 0.7.1 requires those fields on each model finding. Missing or invalid scope fails the response rather than defaulting to `core`. Older saved JSON without scope still loads as `core`.

**Absence-claim discipline (prompt ≥ 0.5.0):** Primary-pass prompts require searching the analyzed text before claiming “not addressed / undefined / no guidance,” preferring narrowed gaps and dual-sided quotes over global silence. A structured second-pass counterevidence enrichment remains deferred (see `docs/backlog.md`).

**Horizon calculator:** `tads.validators.horizon.validate_time_representation` computes fixed-width bounds and epoch-relative instants. Findings may carry optional `time_representation` + `horizon_validation`; the scan pipeline asks the LLM for structured params when applicable (null if unknown—no guessing), parses them, and runs `apply_horizon_validation` without changing disposition. Model-output `width_bits` is accepted from 1 through 256; the calculator still cannot process widths above 128 bits and reports status `error` without failing the scan. `claimed_horizon` is the source-stated claim; computed last-representable / first-out-of-range values stay in validation output. Year/month precision is not compared as an exact instant. Markdown renders a separate **Deterministic validation** section. Schema `0.1.0` / `0.2.0` reports still load and render with their original scalar horizon presentation.

The MVP ships schema support plus sample deterministic validators (including the horizon calculator); a broader validator suite remains an extension point.

### Cost control

Before any paid call, estimate tokens and USD (when pricing is known). Enforce `--max-cost-usd` / `--max-tokens`. Fail closed if the estimate exceeds the budget.

### Privacy

Default mode is **ephemeral**: process in memory; persist only user-requested outputs. Document bodies are not written to logs. Content is sent only to the user-selected LLM provider.

Canonical JSON and Markdown reports contain findings and evidence by design. Ordinary CLI and progress output does not. `--save-raw-on-error` is an opt-in troubleshooting path beside the JSON report (disabled by default; 256 KiB cap; sensitive as a whole; no field-level sanitation). The first persist of a scan uses `{prefix}.raw.txt`; later recovery attempts use collision-safe `{prefix}.raw.{slug}.txt` names bounded to sanitized sibling filenames in the selected output directory, and do not overwrite a file already written in that scan. JSON and Markdown are separate writes, not an atomic transaction.

Schema 0.6.0 request counters: `analysis_request_count` and `repair_request_count` count every executed logical primary and secondary (including failed parents and recovery children). `total_provider_request_count` is their sum. `recovery_request_count` overlaps that total and must not be added. `actual_usage` / `actual_cost_usd` include only completed responses that returned usage.

Ordinary provider errors include allowlisted classification only (`ProviderRequestError` and other `PublicScanError` types). At the `tads scan` CLI boundary, only those TADS-owned public messages are printed; unknown exceptions become `Scan failed: unexpected internal error` plus the timestamped `Failed after …` line (no class names, causes, attributes, or provider bodies). Provider diagnostic URLs keep scheme/host/path and drop userinfo, query, and fragment. That policy is separate from document provenance sanitation. Independently enabled httpx/httpcore DEBUG logging and shell history are not TADS-owned output. The generic boundary favors confidentiality over full terminal diagnosability; bounded raw model text remains opt-in via `--save-raw-on-error` only.

Untrusted JSON (model output, provider envelopes, error-classification snippets, saved reports, and evaluation labels) decodes with a bounded numeric policy: at most 40 integer digits, 64-character float tokens, and no `NaN`/`Infinity`. Model numeric-policy failures are nonrepairable Policy A errors; provider envelope and error-body decode failures are controlled response errors and are not persisted as raw model output. This does not cap HTTP response bytes or overall JSON document size.

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
