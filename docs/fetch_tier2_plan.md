# Plan: remote fetch for open Tier-2 corpora (W3C, ECMA, OASIS, NIST)

**Status:** Phase 0 (shared fetch plumbing) **implemented**. Phases 1–4 (per-corpus resolvers) not started. See [backlog.md](backlog.md).  
**Related:** [phase2.md](phase2.md), `tads.fetch`, `tads convert` / `tads.ingest` pipeline.

## Goal

Extend `tads fetch <id> [--corpus …]` beyond IETF so **W3C, ECMA, OASIS, and NIST** resolve a document id to a **public direct URL**, download, convert to plain text if needed, and write under `inputs/` (gitignored). Same UX as IETF; no login or search-page scraping.

ETSI, 3GPP, ITU-T, IEEE, and ISO/IEC remain **local-file** (or full-URL `convert`/`scan`) for now.

## Locked design choices

1. **Curated URL tables for v1** — Map well-known ids → stable download URLs (bench-driven). Grow the tables as the eval suite expands. Do **not** scrape SDO search UIs or build a general discovery engine in v1.
2. **Always write plain text** — Default output remains `inputs/<normalized_id>.txt` after conversion when the source is HTML/PDF/zip.
3. **Version policy**
   - **W3C:** default to latest TR URL (`https://www.w3.org/TR/<shortname>/`); print a clear note that “latest” was used.
   - **NIST / OASIS:** require part/rev or stage in the id when ambiguous; otherwise fail with a helpful message (prefer explicit over silent wrong edition).
   - **ECMA:** curated edition URLs for known standards (start with ECMA-404, ECMA-262).
4. **Split adapters** — Any corpus with `supports_remote_fetch=True` gets its own module (or a dedicated fetch-capable class), not an overloaded generic Tier-2 stub.
5. **Reuse ingest conversion** — Extend `tads.fetch` to download bytes and hand off to the existing `convert`/ingest pipeline when `media_type` is not plain text. Do not duplicate PDF/HTML stacks.
6. **Escape hatch** — If resolve fails, users keep using `tads convert <url>` or a local path (already supported).

## Current constraint

Today `fetch_text` assumes `resolve()` yields a URI and `response.text` is usable plain text (IETF). Non-IETF sources are often HTML or PDF, so Phase 0 must make fetch **format-aware**.

## Phased work

### Phase 0 — Shared fetch plumbing

**Done.** `tads.fetch` downloads via `ingest_to_text` (plain text, PDF, HTML, zip→member), rejects portal/search URIs, and writes UTF-8 `.txt`. HTML→text uses a stdlib tag stripper; URL HTML requires `allow_html` or a `.html` URL (corpus fetch enables HTML when the ref looks like HTML / W3C TR). `detect_corpus` recognizes common ECMA/W3C/OASIS/NIST id shapes. Per-corpus `supports_remote_fetch` remains IETF-only until Phases 1–4.

### Phase 1 — W3C

- IDs: `hr-time-3`, shortname forms, optional full TR URL pass-through
- Resolve: `https://www.w3.org/TR/<shortname>/` (latest)
- Format: HTML → text
- Smoke: `tads fetch hr-time-3 --corpus w3c` → `tads plan` succeeds  
- Bench relevance: High Resolution Time (monotonic / scope)

### Phase 2 — ECMA

- IDs: `ECMA-404`, `ECMA-262`, …
- Resolve: curated public URLs on ecma-international.org
- Smoke: fetch ECMA-404 (negative control); 262 fetch OK but scans should use caps
- Bench relevance: ECMA-404 negative control; ECMA-262 runtime time

### Phase 3 — OASIS

- IDs: OpenFormula / known short names
- Resolve: curated PDF/HTML permalinks; pin URLs in metadata (stages change)
- Smoke: fetch OpenFormula → text suitable for scan
- Bench relevance: explicit epoch + interoperability warning

### Phase 4 — NIST

- IDs: `SP 800-…`, `FIPS …` with part/rev when needed
- Resolve: curated CSRC direct PDF links
- Smoke: one SP fetches and converts; scan gets `content_sha256`
- Bench relevance: as suite expands

**Suggested ship order for the multi-corpus bench:** W3C → ECMA-404 → OASIS OpenFormula → NIST as needed.

## Effort (indicative)

| Step | Est. |
|------|------|
| Phase 0 shared fetch+convert | 1–2 days |
| Phase 1 W3C | 0.5–1 day |
| Phase 2 ECMA | 0.5–1 day |
| Phase 3 OASIS | ~1 day |
| Phase 4 NIST | ~1 day |
| Docs / examples | ~0.5 day |

Roughly **one focused week** for curated v1.

## Non-goals (v1)

- Auto-fetch for ETSI, 3GPP, ITU-T, IEEE, ISO/IEC  
- Login, cookies, or ToS-bypass scraping  
- Committing downloaded PDFs to git  
- Perfect HTML structure preservation  
- Resolving every historical edition without an explicit version in the id  

## Acceptance criteria

1. `tads corpora` shows **fetch** for `w3c`, `ecma`, `oasis`, `nist`
2. `tads fetch <id> --corpus <…>` writes plain text under `inputs/` for ≥1 real doc per corpus
3. IETF fetch behavior unchanged
4. Unit tests for id→URL; optional network smokes
5. Docs list id examples and `convert <url>` fallback
6. Failures name a portal and suggest local file / `convert <url>`
