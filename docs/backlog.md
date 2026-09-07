# Parked backlog

Current roadmap for lower-priority polish and deferred niceties — not a
commitment to ship. For in-scope MVP behavior, prefer the
[README](../README.md), [architecture.md](architecture.md),
[phase1.md](phase1.md), and [phase2.md](phase2.md). [phase0.md](phase0.md) is a
historical Phase 0 record only.

When an item is picked up, remove or update it here and implement against the
current architecture and relevant reference docs.

## CLI / UX

- **Scan progress UI** — Rich text progress for long runs. Section-aware: true % (refresh ~every 2%). Whole-document: spinner + elapsed time (honest % needs streaming). Lower priority; nice for “something is happening” during LLM waits.

## Ingest

- **Google Docs** — Still Phase 1–scope in spirit, but deferred: export to docx/pdf/txt first; API/URL path later (no interactive login in the first cut). See `docs/phase1.md`.

## Corpus

- **Remote fetch for open Tier-2 corpora (W3C, ECMA, OASIS, NIST)** — Phase 0–4 **done**. Plan: [fetch_tier2_plan.md](fetch_tier2_plan.md). ETSI/3GPP/ITU/IEEE/ISO stay local-file for now (IEEE/ISO licensing).
- **Deepen Tier 2 adapters** — Beyond fetch: richer parse/profile for stubs when there is a real scan workload. Fetch plan above is the first concrete slice for W3C/ECMA/OASIS/NIST.
- **Ollama Cloud pricing table** — USD estimates show “unknown” for cloud models; optional when public pricing is stable enough to encode.

## LLM / providers

- **OpenRouter provider** — Optional first-class `openrouter` backend (OpenAI-compatible chat completions at `https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`, optional Referer/title headers). Candidate first supported model: **`z-ai/glm-5.3-flash`** (Ox Alpha / `stealth/ox-alpha` free preview retired; same family now billed on OpenRouter at low $/M vs current bake-off set). Not release-blocking: smoke findings/JSON quality on RFC 5905 before promoting. Interim: `openai` + `OPENAI_BASE_URL=https://openrouter.ai/api/v1` already works. Prefer one marketplace gateway over adding Hugging Face Inference in the same slice.

## Quality / eval

- **Candidate kind / review-stance taxonomy** — Orthogonal field (e.g. `defect` / `constraint` / `assurance_gap` / `dependency` / `clarification` / `observation`) plus Markdown grouping. Do **not** replace `finding_type`. Biggest gaps today: documented constraints and external dependencies. Design note: [candidate_kind.md](candidate_kind.md). Minimum slice: `constraint` + `dependency` first; optional prompt-only precursor.
- **Whole-document counterevidence check (Phase 1+)** — Optional second LLM enrichment for absence-framed findings (`missing_documentation`, “not addressed / undefined / no guidance”). Structured `counterevidence_status` + quotes; do not auto-reject. Primary-pass absence discipline is already in prompt framework ≥ 0.5.0. Defer until bake-off before/after shows residual false positives.
- **Optional second-pass scope review** — CLI flag (e.g. `--scope-review`) for a focused LLM pass that only reclassifies `scope_relevance` / `scope_rationale` without rewriting findings. Schema already supports fields; defer until primary-pass scoping quality is measured on RFC 5905.
- **Gold labels from reviewed scans** — Promote accepted RFC 5905 / capped 3GPP findings into `eval/corpus/labels/` after human review. Multi-provider bake-off remains the practical soft-label proxy until then.
- **Multi-model comparison** — Same doc + caps across providers already smoke-tested (Ollama Cloud `gpt-oss:120b`, local `llama3.2:3b`, Gemini `gemini-3.6-flash`, OpenAI `gpt-4.1-mini`, Anthropic `claude-sonnet-4-5`); compare finding overlap (manual or harness-assisted). Finding counts differ substantially on the same slice. See also [docs/rfc5905_provider_compare.md](rfc5905_provider_compare.md).
- **Multi-LLM ensemble / mixture-of-experts merge** — Run the same scan with multiple providers and/or models, then combine findings into one stronger report (e.g. union with overlap boosting, cluster near-duplicates, promote themes seen by ≥N models, optional judge/merge pass). Goal: better recall/precision than any single model at a controllable cost tradeoff (cheap ensemble of mid-tier models vs one expensive model). Not high priority; bake-off + gold labels should come first so merge rules can be evaluated. Possible CLI shape later: `--providers` / multi `--model` + a merge strategy flag; keep single-provider `scan` as the default.

## Review / collections

Not implemented. Today review is hand-edited dispositions in per-run report JSON (`tads render` refresh). These items cover multi-document / multi-run scale; related to deferred finding registry / corpus analytics in [architecture.md](architecture.md) and Phase 7 disposition workflow notes in [taxonomy.md](taxonomy.md). Keep single-report JSON edit as the MVP path until a thin orchestration layer is needed.

- **Collections** — Named, frozen sets of scan runs (e.g. “12-doc bench”, “IETF quarterly”) with a manifest of run paths / content hashes, provider/model/prompt versions, and policy notes (coverage caps, exclusions). New scans become a new collection or revision; do not silently overwrite runs under active review. Possible later CLI: `tads collection create|status|diff`.
- **Review units and queues** — Assign work by **document** (or theme/corpus), not by provider-specific file. Collection-level progress (`% docs complete`, open findings, `needs_review`). Prioritize multi-provider agreement and source-/horizon-verified findings before absence-only singletons. Optional assignee metadata; no interactive TUI required for a first CLI/queue slice.
- **Durable review state / disposition carry-forward** — Keep human dispositions out of (or exportable from) provider-specific report files so re-scans do not wipe review. Fingerprint findings by doc + content hash + theme/section (not run-local `F-001`). On collection diff, carry forward matching `accepted`/`rejected` and only queue new/changed/unmatched items. Feeds gold-label promotion and a future finding registry.
- **Collection compare / theme matrix** — Side-by-side titles/themes across providers within a collection; soft-label candidates from ≥N-provider agreement. Complements multi-model comparison and reserved `cross_model_supported` assurance status (ensemble phase); do not treat raw finding counts as quality scores.
