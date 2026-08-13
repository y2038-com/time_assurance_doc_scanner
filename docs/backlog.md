# Parked backlog

Ideas worth remembering that are **not** current phase definition-of-done.
Phase docs (`phase0.md` …) stay authoritative for in-scope work; this file is for lower-priority polish and deferred niceties.

When an item is picked up, move or delete it here and implement against the relevant phase doc.

## CLI / UX

- **Scan progress UI** — Rich text progress for long runs. Section-aware: true % (refresh ~every 2%). Whole-document: spinner + elapsed time (honest % needs streaming). Lower priority; nice for “something is happening” during LLM waits.
- **`render` overwrite prompt** — `fetch` / `convert` / `scan` already guard existing outputs; optionally extend the same `--overwrite` / `-f` pattern to `render`.

## Ingest

- **Google Docs** — Still Phase 1–scope in spirit, but deferred: export to docx/pdf/txt first; API/URL path later (no interactive login in the first cut). See `docs/phase1.md`.

## Corpus

- **Deepen Tier 2 adapters** — ECMA (and others) are stubs; add fetch/parsers only when there is a real scan workload.
- **Ollama Cloud pricing table** — USD estimates show “unknown” for cloud models; optional when public pricing is stable enough to encode.

## Quality / eval

- **Optional second-pass scope review** — CLI flag (e.g. `--scope-review`) for a focused LLM pass that only reclassifies `scope_relevance` / `scope_rationale` without rewriting findings. Schema already supports fields; defer until primary-pass scoping quality is measured on RFC 5905.
- **Gold labels from reviewed scans** — Promote accepted RFC 5905 / capped 3GPP findings into `eval/corpus/labels/` after human review.
- **Multi-model comparison** — Same doc + caps across providers already smoke-tested (Ollama Cloud `gpt-oss:120b`, local `llama3.2:3b`, Gemini `gemini-3.6-flash`, OpenAI `gpt-4.1-mini`, Anthropic `claude-sonnet-4-5`); compare finding overlap (manual or harness-assisted). Finding counts differ substantially on the same slice. See also [docs/rfc5905_provider_compare.md](rfc5905_provider_compare.md).
- **Multi-LLM ensemble / mixture-of-experts merge** — Run the same scan with multiple providers and/or models, then combine findings into one stronger report (e.g. union with overlap boosting, cluster near-duplicates, promote themes seen by ≥N models, optional judge/merge pass). Goal: better recall/precision than any single model at a controllable cost tradeoff (cheap ensemble of mid-tier models vs one expensive model). Not high priority; bake-off + gold labels should come first so merge rules can be evaluated. Possible CLI shape later: `--providers` / multi `--model` + a merge strategy flag; keep single-provider `scan` as the default.
