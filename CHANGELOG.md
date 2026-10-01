# Changelog

All notable user-visible changes to TADS are recorded here.

Package versions use PEP 440 (`0.6.0rc3`). The corresponding Git tag for this
candidate, when created, will be `v0.6.0-rc.3`. Public report schema and prompt
framework versions are independent of the package version.

## Unreleased

## 0.6.0rc3 - 2026-09-30

Release candidate. Future GitHub release title: **TADS 0.6.0 Release Candidate 3**.

Package version **0.6.0rc3**. Public report schema remains **0.3.0**. Prompt
framework remains **0.8.0**.

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

### Compatibility / upgrade notes

- Public report schema is unchanged (`0.3.0`). Prompt framework is unchanged
  (`0.8.0`).
- Ollama behavior is unchanged in this release: local parsing remains the only
  structured-output path.
- Custom OpenAI-compatible endpoints (`OPENAI_BASE_URL`) keep the pre-existing
  prompt-only / local-validation path unless a later explicit capability
  configuration is added.
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
