# Changelog

All notable user-visible changes to TADS are recorded here.

Package versions use PEP 440 (`0.6.0rc4`). The corresponding Git tag for this
candidate, when created, will be `v0.6.0-rc.4`. Public report schema and prompt
framework versions are independent of the package version.

## Unreleased

### Added

- `tads scan` prints UTC ISO-8601 timestamps (`[YYYY-MM-DDTHH:MM:SS.mmmZ]`) on
  execution-phase console events and reports total wall runtime using
  monotonic elapsed time (for example `Completed successfully in 30m 42.8s` or
  `Failed after 22m 10.5s`). Interactive terminals reuse one live progress line
  for chunk/repair/correction updates; redirected or CI (non-TTY) output keeps
  one ordinary line per event. Runtime is operational telemetry only and is
  **not** stored in public JSON/Markdown reports.

### Fixed

- Benchmark `--skip-existing` compares report `document.doc_id` to the
  corpus-adapter canonical identity TADS publishes (via `resolve()`), not only
  the raw CLI `--doc-id`. Fixes false metadata mismatches for ETSI and 3GPP
  benchmark documents (for example `EN 300 468` vs `ETSI EN 300 468`). Output
  prefixes and scan command `--doc-id` values are unchanged.

## 0.6.0rc4 - 2026-10-16

Release candidate. Future GitHub release title: **TADS 0.6.0 Release Candidate 4**.

Package version **0.6.0rc4**. Public report schema **0.5.0**. Prompt framework
**0.9.1**. Expected future Git tag: `v0.6.0-rc.4`.

This candidate accumulates output-contract hardening (structured output, JSON
number hardening, width constraints), opt-in GPT-6 Luna support, public schema
**0.4.0** / **0.5.0** provenance fields, section chunking, evidence grounding
and semantic correction, and benchmark-runner model-matrix improvements.
**GPT-4.1 Mini remains the default** OpenAI model.

### Added

- Benchmark harness (`scripts/run_bench_scans.py`, `scripts/plan_bench_costs.py`)
  named configurations and exact `--model provider/model[/effort]` selection,
  collision-safe output prefixes (reasoning effort + chunking/output controls),
  schema 0.5.0-aware `--skip-existing` matching, and a per-tag
  `bench_manifest[__tag].json` summary. Runner and planner keep **separate**
  historical defaults (runner: OpenAI `gpt-4.1-mini` + Gemini `gemini-3.6-flash`;
  planner: Ollama `gpt-oss:120b`, OpenAI mini, Anthropic `claude-sonnet-4-5`,
  Gemini flash). Explicit `--config` / `--model` selection is shared. Same-tag
  incompatible run definitions fail closed (choose a new tag); equivalent
  resumes merge job provenance. Lossily normalized tags append a digest.
  Model IDs containing `/` are unsupported by `--model`. Terminal aggregates
  label partial known subtotals. `max_sections_per_request` is prefix-only
  resume identity. Contingent repair/correction requests remain outside
  preflight estimates. Definitive comparisons should use a clean committed tree.
- Opt-in OpenAI **GPT-6 Luna** (`gpt-6-luna`) on Chat Completions. Select with
  `--provider openai --model gpt-6-luna`. **GPT-4.1 Mini remains the default**
  OpenAI model; the historical benchmark default matrix is unchanged.
- Deterministic **multi-section chunking** for section-aware analysis: adjacent
  analyzed sections are packed into primary requests (default
  `--max-sections-per-request 8`). Use `--no-section-chunking` for one request
  per section. Opaque TADS section references (`S0001`…) are allowlisted in
  trusted instructions; canonical IDs/titles are applied after exact membership
  validation. Oversized singletons and duplicate canonical section IDs fail at
  planning.
- Public report schema **0.5.0**: `run.request_batching`
  (`whole_document|per_section|section_chunks`), `analysis_request_count`,
  `repair_request_count`, `chunk_section_counts`, and `coverage_complete`.
  New scans write `0.5.0`. Legacy `0.1.0`–`0.4.0` still load and render with
  batching fields null/absent (never invented); loading/rendering does not
  rewrite them.
- Public report schema **0.4.0** (retained for legacy): optional
  `run.reasoning_effort` (`none|low|medium|high|xhigh|max` or null) and optional
  `actual_usage.reasoning_tokens`.
- Prompt framework **0.9.0** with provider-neutral section-chunk framing.
- Prompt framework **0.9.1**: section-aware evidence quotes must be exact,
  contiguous, verbatim text from the claimed opaque section (copy extraction
  spacing as shown; use `evidence: []` when uncertain; no paraphrases in
  `quote`). One bounded **semantic correction** request may regenerate a
  complete section-aware response after a section-local evidence-grounding
  miss, sharing the single secondary-request slot with JSON envelope repair.
  Duplicate JSON object keys in model output remain immediately nonrepairable
  (no envelope repair, no semantic correction). Whole-document analysis does
  not use semantic correction.
- OpenAI model request policy (`tads.llm.openai_models`) for completion-token
  parameter choice, reasoning-effort support, sampling compatibility, and
  short/long-context pricing.
- Optional `--reasoning-effort` (validated on `plan` and `scan`). GPT-6 Luna
  accepts `none|low|medium|high|xhigh|max` (effective default `none` when unset;
  case-insensitive trim). Explicit values are rejected for non-reasoning OpenAI
  models and for Anthropic, Gemini, Ollama, and mock.
- Capture of OpenAI `completion_tokens_details.reasoning_tokens` when present.
  Actual cost uses total `completion_tokens` once (no double-count).
- Controlled failure on Chat Completions `finish_reason=length` (no envelope
  repair; no report writes).

### Changed

- Flexible evidence-quote grounding treats controlled punctuation-adjacent
  whitespace as equivalent to the tight form (extraction-style ``agent ,`` vs
  model ``agent,``; ``context 's`` vs ``context's``), while keeping exact
  substring matching first and unchanged. Unique flexible spans may publish
  offsets into the unmodified source substring; the stored model quote is not
  rewritten. Punctuation still cannot be substituted, dropped, or reordered.
- Section IDs are TADS-generated document-local locators. After parse, every
  `ParsedDocument` uniquifies collisions in document order (`s-1`, then
  `s-1--2`, …), skipping any suffix already present in the original ID set.
  After a `Table of Contents` marker, contiguous leader-less undotted TOC
  stubs (and W3C TOC-only gaps) are not promoted to body headings; undotted
  body headings with real prose in other corpora are unchanged. The chunk
  planner still fail-closes if duplicate canonical IDs reach packing.
- Planning reserves configured `--max-output-tokens` plus a shared conservative
  per-request input overhead (`REQUEST_OVERHEAD_TOKENS` = 6000). Formula:
  `available_document_tokens = context_window - max_output_tokens -
  request_overhead_tokens`. The heuristic covers system prompt, corpus
  guidance, and native structured-output schema (chunk user framing is
  estimated from the live serialized envelope). Raised from 4000 after prompt
  0.9.0 chunk instructions: measured system+OpenAI schema ≈4700 tokens with
  margin (also above the RFC868 smoke gap of ≈3,802 beyond a 756-token body).
- `coverage_complete` means every eligible section was analyzed and all planned
  analysis units completed successfully — not merely that planned requests
  succeeded. Intentional caps (`--max-sections` / `--max-chars` /
  `--max-input-tokens`) yield `coverage_complete=false`. Front-matter and
  Index/Acknowledgments skips do not make eligible coverage incomplete.
- `analysis_request_count` / `repair_request_count` count successful logical
  primary and repair completions. HTTP retries and structured-output fallback
  POSTs are transport details and are not separate analysis chunks.
- For GPT-6 Luna, `--max-output-tokens` maps to `max_completion_tokens` and
  includes reasoning plus visible JSON. Default remains **16384**.
- GPT-6 Luna short/long pricing at 272,000 input tokens per request; section-aware
  prices each request independently. Per-request input estimates use the same
  shared request overhead as context-window planning so bodies near the
  threshold are not mis-tiered as short-context and whole-document mode is not
  chosen when the request may exceed the model context window.
  Cached-input/cache-write costs are not modeled.
- `--max-cost-usd` is an estimate-based preflight guard, not a hard billing cap.
  Non-`none` reasoning estimates are lower bounds until completion usage is known.
- `repair_request_count` includes successful JSON envelope repairs and successful
  section-local evidence semantic corrections. At most one secondary model
  request is counted per logical primary (`repair_request_count` ≤
  `analysis_request_count`). Estimates exclude secondary requests; planning
  notes that up to one repair/correction per primary may occur.

### Notes

- Native structured output remains a reliability aid; local Policy A remains
  authoritative. Package **0.6.0rc4**; prompt framework **0.9.1**; public schema
  **0.5.0**. A larger context window does not guarantee TPM fit; section-aware
  chunking reduces request count but still reserves the full configured
  completion budget per request. Semantic correction regenerates a complete
  chunk response and never reassigns sections or salvages siblings locally.

## 0.6.0rc3 - 2026-09-30

Release candidate. Future GitHub release title: **TADS 0.6.0 Release Candidate 3**.

Package version **0.6.0rc3**. Public report schema remains **0.3.0**. Prompt
framework **0.8.1**.

OpenAI and Gemini can request provider-native structured output for the
model-facing findings JSON. That is an upstream reliability aid. TADS still
parses and validates the returned text locally (Policy A). Native provider
acceptance is not the TADS assurance boundary.

### Added

- Explicit `supports_native_structured_output()` provider capability. Official
  OpenAI Chat Completions (`api.openai.com`) and Gemini `generateContent`
  advertise support. Custom `OPENAI_BASE_URL` proxies stay on the prompt-only
  path. Ollama, Anthropic, and mock do not enable native structured output.
- Provider JSON Schema derived from the model-facing findings contract
  (`ModelFindingsResponse`), including required `findings`, closed enums,
  nullable enrichment fields, and `claimed_horizon` as object-or-null with
  `year` / `month` / `day` / `instant` precision.
- OpenAI `response_format.json_schema` (strict) and Gemini
  `generationConfig.responseMimeType` / `responseSchema` when capability is
  enabled. Native schema is a reliability aid, not a security boundary; TADS
  still parses and validates locally.
- One fallback request without the native schema option only when the provider
  unambiguously says that the structured-output parameter or feature is unknown
  or unsupported. A nonempty error `param` is decisive: only the native
  structured-output field permits fallback. Schema-keyword, supplied-schema,
  and unrelated-parameter errors fail closed as ordinary non-retryable HTTP
  400s even if the message also names `response_format` or `responseSchema`.
  Unrelated 400, authentication, 429, and server errors do not fall back. An
  instance that has already seen an unsupported feature does not probe again.

### Changed

- Prompt framework **0.8.1**: `width_bits` is the number of bits explicitly
  stated by the source. Copy that bit count exactly. Do not concatenate it
  with an epoch year, date, numeric range, scale factor, byte count, or
  nearby number, and do not output `2^width` or the maximum representable
  value. Unknown or unsupported width is JSON null or omitted.
- Model-output and Policy A `width_bits` accept only JSON integers from 1
  through 256. Booleans, floats, and numeric strings are rejected. Gemini
  native schema now includes `minimum`/`maximum` as a reliability aid.
  OpenAI still strips those keywords; local Policy A remains authoritative.
  Decoder failures still surface as `Invalid JSON number` and are
  nonrepairable. Out-of-range integers that decode successfully fail Policy A
  without a repair request. Observed Gemini RFC 868 output of `3200` is
  rejected.

### Compatibility / upgrade notes

- Public report schema is unchanged (`0.3.0`). Prompt framework is **0.8.1**.
- Model-output `width_bits` must be a JSON integer from 1 through 256, or
  null/omitted when unknown. Local Policy A enforces that range. The public
  `TimeRepresentationParams` model stays unbounded so schema `0.1.0` /
  `0.2.0` / `0.3.0` reports still load. The horizon calculator still caps
  deterministic arithmetic at 128 bits; widths 129–256 may be stored as
  model evidence with calculator status `error`.
- `ticks_per_second` still has no positive/magnitude bound beyond a finite
  JSON number. That constraint is deferred.
- Ollama behavior is unchanged in this release: local parsing remains the only
  structured-output path.
- Custom OpenAI-compatible endpoints (`OPENAI_BASE_URL`) keep the pre-existing
  prompt-only / local-validation path unless a later explicit capability
  configuration is added.
- Untrusted JSON numeric tokens are bounded (40 integer digits; 64-character
  float tokens). Non-finite and nonstandard constants (`NaN`, `Infinity`)
  fail closed. Model numeric-policy failures are not envelope-repaired and
  do not retry the provider. Provider envelopes and error bodies are not
  persisted. This does not cap HTTP response bytes or overall JSON document
  size.
- An HTTP 400 is not assumed to be unbillable.

## 0.6.0rc2 - 2026-09-30

Release candidate. Future GitHub release title: **TADS 0.6.0 Release Candidate 2**.

Package version **0.6.0rc2**. Public report schema **0.3.0**. Prompt framework
**0.8.0**.

This is a model-output contract change. Benchmark results are not directly
comparable with `0.6.0rc1`.

### Changed

- Source-stated `claimed_horizon` is now a structured `{value, precision}` object
  (or JSON null) instead of a scalar date/datetime. Allowed precisions are
  `year`, `month`, `day`, and `instant`, each with a matching lexical form.
  Partial year/month claims are preserved as stated (for example
  `{"value":"2036","precision":"year"}`) and are not coerced to January 1,
  month-end, or any other invented instant.
- `claimed_horizon` records a horizon the source document explicitly states. It
  is not a model-computed rollover and not a TADS-computed bound. Deterministic
  last-representable and first-out-of-range instants remain in horizon
  validation output. Year/month claims leave `claim_consistent` unset rather
  than inventing an exact comparison.
- Trusted prompt instructions keep `domains` as a closed vocabulary. Finding
  types, `privacy`, `security`, and near-synonyms such as `gps` are not domain
  tokens. Parser behavior remains fail-closed (Policy A).
- Finding-item validation treats `recommendation_level1`, `time_representation`,
  `section_id`, and `section_title` as nullable enrichment or locator fields.
  Missing or JSON null is accepted; a malformed non-null value still fails the
  whole response or section (Policy A).

### Compatibility / upgrade notes

- New scans write schema `0.3.0` with the structured horizon object.
- Schema `0.1.0` and `0.2.0` reports still load and render. Older scalar
  `claimed_horizon` values are interpreted at their stored precision and are
  not rewritten merely because the report was loaded or rendered.
- Prompt framework `0.8.0` is a model-output contract change relative to
  `0.7.2` / package `0.6.0rc1`. Live benchmark counts and themes are not
  directly comparable.

## 0.6.0rc1 - 2026-09-29

Release candidate since tagged `0.5.1` (`v0.5.1`). Future GitHub release title:
**TADS 0.6.0 Release Candidate 1**.

Public result schema remains **0.2.0**. Prompt framework remains **0.7.1**.

### Added

- Generic analysis profile for otherwise unidentified local `plan` / `scan`
  documents, instead of synthesizing an IETF identity or URL.
- Factual provenance: local scans record `source_path`; remote scans record a
  sanitized `source_uri` and, when the final serving URL differs, `retrieved_uri`.
- Report schema `0.2.0` for the optional retrieved-URI field. Schema `0.1.0`
  reports still load.
- Manual, hop-by-hop redirect handling with HTTPS-to-HTTP downgrade rejection.
- Output filename and path containment for derived default paths.
- Archive, DOCX package, conversion, and converted-text resource limits.
- Prompt system/user trust boundaries (framework 0.7.1 required-field contract).
- Strict whole-response and finding-item validation; one malformed item fails
  the response or section.
- Fail-closed section-aware behavior: a section parse failure aborts the scan.
- Safe Markdown rendering of untrusted report strings (TADS-controlled headings
  and no clickable links).
- Allowlisted provider errors and exception-graph sanitation.
- Opt-in, 256 KiB-capped, permission-protected raw-on-error artifacts
  (`--save-raw-on-error`, off by default).
- Operational SECURITY policy and private vulnerability-reporting channels.
- CI coverage for tests, optional PDF extras, and package build/validation.

### Compatibility / upgrade notes

These are behavior changes relative to `0.5.1`:

- Undetected `plan` / `scan` document IDs now use the `generic` corpus instead
  of inheriting IETF identity. `fetch` still defaults undetected IDs to IETF.
- Hostile or path-like document IDs produce contained, sanitized default
  filenames under `outputs/` or `inputs/`. Explicit `-o` remains user-controlled.
- Malformed finding items now fail the response or section rather than being
  silently discarded or defaulted.
- Markdown headings and links are TADS-controlled. Untrusted strings are escaped
  or shown in literal fences.
- HTTPS-to-HTTP redirects are rejected hop by hop.
- New reports use schema `0.2.0`. Older `0.1.0` JSON still loads and renders.
- `--save-raw-on-error` is disabled by default and must be requested explicitly.
- Ordinary provider errors no longer include response-body excerpts.

### Known limitations

- Model conclusions remain candidates for review, not certification or a
  completeness verdict.
- Prompt channel separation reduces but cannot eliminate semantic
  prompt-injection risk.
- DNS validation is not connection-level IP pinning; DNS rebinding remains
  possible.
- Hosted processing of untrusted files should add process or container CPU,
  memory, and egress controls.
- Provider response loading is not independently byte-capped.
- Converters are resource-bounded but do not have independent CPU deadlines.
- JSON and Markdown reports are separate writes, not one transaction.
- Independently enabled httpx/httpcore DEBUG logging and shell history are
  outside TADS sanitation.
- Raw-on-error output is sensitive when explicitly enabled. TADS does not
  attempt field-level secret or URL sanitation inside that file.

TADS does not claim complete protection, exhaustive detection, or certification.
