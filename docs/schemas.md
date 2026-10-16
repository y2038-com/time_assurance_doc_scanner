# Schemas

Canonical models live in `src/tads/schemas/`. Markdown and CSV exporters (Phase 1+) derive from one JSON-shaped finding/report model.

## Finding

Required conceptual fields:

| Field | Purpose |
|-------|---------|
| `id` | Stable ID within a report (e.g. `F-001`) |
| `finding_type` | Taxonomy type |
| `title` | Short summary |
| `description` | Full explanation |
| `severity` | critical … info |
| `confidence` | high / medium / low |
| `domains` | Y2036, Y2038, general concepts, … |
| `location` | Document locator (section id/title; optional `[start,end)` char offsets into analyzed text) |
| `evidence` | One or more quotes / references |
| `machine_interpretation` | What the analyzer inferred |
| `validation_status` | Deterministic check only: unverified / verified / failed / n/a. JSON value `verified` means deterministically checked, **not** human-validated. |
| `validation_detail` | Optional deterministic result summary |
| `time_representation` | Optional structured counter params (width, signed, epoch, unit, tick rate, source-stated `claimed_horizon`) |
| `horizon_validation` | Optional structured calculator result (bounds, instants, claim_consistent, notes). Computed last-representable / first-out-of-range values are distinct from the source-stated claim. |
| `source_verified` | Evidence quote found in analyzed document text (bool; default false) |
| `source_verification_detail` | Optional source-match summary |
| `recommendation_level1` | Optional remediation *direction* |
| `scope_relevance` | Relevance to TADS mission: `core` / `supporting` / `incidental` / `out_of_scope` (default `core` for older JSON) |
| `scope_rationale` | Optional 1–2 sentence explanation of the scope label |
| `disposition` | Human review state (`accepted` = human-confirmed) |
| `reviewer_notes` | Free-form reviewer text |

**Scope relevance** is orthogonal to severity, confidence, `validation_status`, and disposition — setting scope never auto-mutates those fields. `out_of_scope` means “not a TADS time-assurance concern,” not “technically unimportant.” Markdown shows `core` + `supporting` in the main section, `incidental` in a lower section, and omits `out_of_scope` by default (all remain in JSON).

**Public assurance status** (Markdown / CLI; derived — not a separate stored enum):

Assurance status describes the evidence and review state of an individual candidate. It is not an assurance rating for the analyzed document, protocol, implementation, or system.

| Derived status | From | Public label |
|----------------|------|--------------|
| `candidate` | default | candidate for review |
| `source_verified` | `source_verified=true` | source-verified candidate |
| `deterministically_validated` | `validation_status=verified` | deterministically checked candidate |
| `human_confirmed` | `disposition=accepted` | validated finding (human-confirmed) |
| `rejected` | `disposition=rejected` | rejected |
| `deferred` | `disposition=needs_review` | deferred |

Precedence: rejected > human_confirmed > deterministically_validated > source_verified > candidate (deferred via disposition). Reserve **validated finding** for `human_confirmed` only. Older JSON without `source_verified` / horizon / scope fields loads with those fields absent/`false`/`core`.

When `time_representation` is present, `apply_horizon_validation` fills `horizon_validation` and maps calculator status onto `validation_status` (`verified`→verified, `contradicted`→failed, insufficient/unsupported→not_applicable). This never changes `disposition`.

The scan prompt (framework ≥ 0.6.0; untrusted-content framing in ≥ 0.7.0; required-field contract in ≥ 0.7.1; nullable locator/enrichment contract in ≥ 0.7.2; structured source-horizon and closed-domain instructions in ≥ 0.8.0; `width_bits` as a counter bit count in ≥ 0.8.1) asks the model for `scope_relevance` / `scope_rationale` and for `time_representation` with document-established values only; omit the key or use `null` when unknown. Prefer `epoch_kind` (`unix`, `ntp`, `gps`, `mjd`, `ntfs`, `uuid`, `tai_1958`, `other`) over inventing ISO dates for conventional epochs; `epoch` datetime remains for `other` / legacy. `claimed_horizon` is a source-stated `{value, precision}` object (`year` / `month` / `day` / `instant`) or null — not a computed rollover and not coerced from a year like 2036 into January 1 or December 31. `width_bits` is the number of bits explicitly stated by the source (JSON integer 1–256, or null), copied exactly; it is not a concatenation with an epoch year or nearby number, and not `2^width`, a maximum value, a byte count, or a decimal range. Policy A enforces that model-output range; the public report field stays unbounded so schema `0.1.0` / `0.2.0` / `0.3.0` reports still load. Empty all-null time objects are dropped so the older ISO-date heuristic can still run. A scan response with missing or invalid scope fails closed. Older saved JSON without scope still loads as `core`. Framework ≥ 0.5.0 also requires searching the analyzed text before strong absence claims (“not addressed,” “undefined,” “no guidance”); a structured second-pass counterevidence check remains backlog. When signedness is unresolved but width is known, the horizon calculator reports both signed and unsigned interpretations (`ambiguous_signedness`) without guessing. Year/month source claims leave `claim_consistent` null rather than inventing an exact instant comparison. `domains` remains a closed `TimeDomain` vocabulary; `privacy` / `security` / finding types are not domain tokens. Official OpenAI and Gemini endpoints may additionally receive a provider-native JSON schema generated from this model-facing contract as a reliability aid; TADS still parses and validates locally. Custom `OPENAI_BASE_URL` endpoints do not use that native schema. Public report schema remains `0.3.0`.

## Report

A scan produces one report containing:

- Document identity (corpus, id, title, source URI/path, optional retrieved URI, content hash)
- Run metadata (timestamp, provider, model, analysis mode, privacy mode)
- Cost/usage summary
- Findings list
- Optional human-review metadata (`reviewed_at`, `reviewer`)

Primary serializations: **JSON** (canonical, schema `0.5.0`) and **Markdown** (human-readable). Schema `0.1.0`–`0.4.0` reports still load and render; older scalar `claimed_horizon` values are interpreted at their stored precision. Missing `retrieved_uri` / `reasoning_effort` / `reasoning_tokens` stay optional. Pre-`0.5.0` batching provenance fields stay null/absent — they are never invented — and Markdown omits them (or would show unknown, never fabricated `0`/`yes`). Loading or rendering does not rewrite legacy reports. Serializing a loaded legacy report preserves its historical schema shape without injecting `0.5.0` batching keys. CSV / SARIF / HTML come later from the same model.

Schema `0.4.0` added optional `run.reasoning_effort` (`none|low|medium|high|xhigh|max` or null) and optional `actual_usage.reasoning_tokens` (nonnegative integer when the provider reports it). Non-reasoning models record `reasoning_effort` as null.

Schema `0.5.0` requires present, typed, cross-field-consistent request-batching provenance:

- `run.request_batching`: `whole_document` (whole-document analysis only), `per_section` (section-aware with every chunk count `1`, including automatic packing that yields only singletons), or `section_chunks` (section-aware with at least one chunk count `> 1`).
- `run.analysis_request_count`: successful logical primary model completions (`>= 0`). Equals `len(chunk_section_counts)`. HTTP retries and structured-output fallback POSTs are transport details, not separate analysis chunks.
- `run.repair_request_count`: successful repair completions (`>= 0`, `<= analysis_request_count`).
- `run.chunk_section_counts`: list of strict positive integers (booleans rejected). For section-aware reports, `sum(counts) == sections_analyzed`. Empty analyzed scope records `[]` with `analysis_request_count=0`.
- `run.coverage_complete`: true only when **all eligible sections** were included in analyzed scope **and** all planned analysis units completed successfully. Caps (`max_sections` / `max_chars` / `max_input_tokens`) that omit eligible content yield `false`. Front-matter and Index/Acknowledgments excluded before eligibility do not make eligible coverage incomplete.

Terminology: **document sections** (parsed) → **eligible sections** (after skip filters) → **analyzed sections** (after caps) → **execution success** (all planned requests completed) → **complete eligible coverage** (`coverage_complete`).

Failed scans produce no public report and no public request counts.

`location.section_id` values are TADS-generated **document-local** locators (for example `s-1`, `s-1.2`), not source HTML anchors. When sectionization emits the same locator for more than one instance, TADS assigns deterministic suffixes in document order (`s-1--2`, `s-1--3`, …), never stealing an ID that already occurred in the original set. Public attribution therefore names exactly one section instance.

Verified evidence quotes on chunked findings use half-open ``[start_char, end_char)`` offsets into the **normalized ingested document text** (`ParsedDocument.text`) only when an authoritative `Section.start_char` mapping confirms the section text is an exact document slice and the quote has a unique match inside the claimed section. Exact substring matching is tried first. Flexible grounding may also treat controlled whitespace differences as equivalent—inter-token line-wrap/paragraph spacing, optional whitespace immediately before ordinary closing punctuation (``, ; : ? ! . ) ] }``), and optional whitespace between a word character and a straight or curly apostrophe when the apostrophe is followed by another word character (``context's`` vs ``context 's``)—without substituting, dropping, or reordering punctuation or words. A unique flexible span may publish offsets into the **unmodified** source substring (including extraction spacing); the stored model quote is never rewritten to look like that substring. Flexible matching must not publish another section's match. Ambiguous or unmapped quotes leave offsets null while retaining trusted section attribution. Empty `evidence: []` remains valid and is not quote-grounded evidence.

### Provenance / reproducibility

`source_uri` is the sanitized URL of the first HTTP request TADS issued after any document rewrite or fallback. `retrieved_uri` is present only when the sanitized final serving URL differs. Local files leave both URI fields unset and record `source_path`. Adapter `resolve()` catalog URLs are fetch locations, not copied onto a local scan. Userinfo, query strings, and fragments are dropped from both URI fields. JSON run + document metadata also include `content_sha256`, `scanner_version`, `prompt_framework_version`, provider/model, `analysis_mode`, run timestamps, scope caps / eligible-analyzed char totals, and sampling fields TADS actually sets (`temperature`, `max_output_tokens`). Unset provider defaults are omitted rather than fabricated. Low temperature improves steadiness but does **not** guarantee identical repeated LLM results. These are populated on scan today. The Markdown report header surfaces **Content SHA-256** (when present), **Scanner**, **Prompt framework**, provider/model, analysis mode, **Source URI** / **Retrieved URI** / **Source path** when set, and an explicit **Analysis scope** / **Coverage** line (PARTIAL vs COMPLETE relative to eligible text). `tads render` uses the same projection. Coverage of eligible text is not a claim that every semantic aspect of the source was assessed.

For whole-document source verification, verified evidence quotes may include half-open ``[start_char, end_char)`` offsets into the **analyzed (scoped) text**. For section-chunk findings, offsets (when published) are into the **normalized ingested document text** via authoritative section mapping — never another section's match and never scoped-join offsets mislabeled as document offsets. Offsets are omitted when verification fails, the match is ambiguous, or no honest mapping exists.

## Cost estimate

Preflight object used before LLM calls:

- Estimated input / output / total tokens
- Estimated USD (when pricing table has the model)
- Budget caps (`max_cost_usd`, `max_tokens`)
- Whether the job is allowed to proceed

## Human review workflow (MVP)

1. Scanner writes `report.json` + `report.md` (Markdown titles items **Candidates for review**)
2. Reviewer edits dispositions (and optional notes) in JSON — `accepted` promotes to a **validated finding**
3. `tads render report.json` refreshes Markdown from the edited JSON. Markdown is a projection of untrusted document, model, and reviewer strings. Headings and links are TADS-authored or omitted; evidence quotes are shown as literal fenced text. Canonical JSON is unchanged.

No interactive TUI is required for Phase 1.
