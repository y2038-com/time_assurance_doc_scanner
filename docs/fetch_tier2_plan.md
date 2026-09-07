# Plan: remote fetch for open Tier-2 corpora (W3C, ECMA, OASIS, NIST)

> **Status: Completed implementation plan.**
> Phases 0–4 in this document are **implemented** (shared plumbing + W3C + ECMA +
> OASIS + NIST). Retain this file for design history and acceptance criteria; for
> day-to-day fetch usage and corpus tiers, see [phase2.md](phase2.md). Remaining
> corpus work is tracked in [backlog.md](backlog.md).

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

## Current constraint (at plan time; since resolved)

At plan time, `fetch_text` assumed `resolve()` yielded a URI and `response.text` was usable plain text (IETF). Non-IETF sources are often HTML or PDF, so Phase 0 made fetch **format-aware** (now implemented).

## Phased work

### Phase 0 — Shared fetch plumbing

**Done.** `tads.fetch` downloads via `ingest_to_text` (plain text, PDF, HTML, zip→member), rejects portal/search URIs, and writes UTF-8 `.txt`. HTML→text uses a stdlib tag stripper; URL HTML requires `allow_html` or a `.html` URL (corpus fetch enables HTML when the ref looks like HTML / W3C TR). `detect_corpus` recognizes common ECMA/W3C/OASIS/NIST id shapes.

### Phase 1 — W3C

**Done.** Dedicated `W3CAdapter` (`supports_remote_fetch=True`) resolves shortnames and `/TR/…` URLs to `https://www.w3.org/TR/<shortname>/` (latest), `media_type=text/html`, with a version note. Aliases include `hr-time` → `hr-time-3`. Smoke: `tads fetch hr-time-3` (corpus auto-detect or `--corpus w3c`).

### Phase 2 — ECMA

**Done.** Dedicated `EcmaAdapter` with curated catalog: `ECMA-404` → 2nd-edition PDF; `ECMA-262` → pinned HTML `https://262.ecma-international.org/17.0/`. Unknown `ECMA-NNN` fails with supported-id list. Smoke: `tads fetch ECMA-404` (negative control); `tads fetch ECMA-262` works but scans should use caps.

### Phase 3 — OASIS

**Done.** Dedicated `OasisAdapter` with curated catalog: `OpenFormula` / `OpenFormula-1.4` → ODF v1.4 Part 4 OS PDF; `OpenFormula-1.3` → v1.3 OS PDF. Unknown work products fail with supported-id list. Smoke: `tads fetch OpenFormula`.

### Phase 4 — NIST

**Done.** Dedicated `NistAdapter` with curated nvlpubs PDFs: `SP-800-57pt1r5` (aliases like `SP 800-57 Part 1 Rev. 5`), `SP-800-90Ar1`, `FIPS-140-3`. Bare `SP 800-57` fails as ambiguous (part/rev required). Smoke: `tads fetch "SP 800-57 Part 1 Rev. 5"`.

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
