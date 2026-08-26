# Phase 2 — Corpus Awareness

Add corpus-specific intelligence while keeping a common scanner engine.

## Tier 1 (implemented)

| Corpus | Adapter | Remote fetch | Notes |
|--------|---------|--------------|-------|
| IETF | `ietf` | Yes (`.txt`) | RFC / Internet-Draft |
| ETSI | `etsi` | Local file | Clause/annex sectionizer + ETSI profile |
| 3GPP | `3gpp` | Local file | Clause/annex sectionizer + 3GPP profile |

## Tier 2

| Corpus | Status |
|--------|--------|
| **W3C** | Fetch-enabled: `tads fetch hr-time-3` → latest `https://www.w3.org/TR/<shortname>/` (HTML→text). See [fetch_tier2_plan.md](fetch_tier2_plan.md). |
| `itu-t`, `ieee`, `oasis`, `nist`, `iso`, `ecma` | Stubs (clause parse + prompt metadata); local-file or `tads convert <url>` |

**Parked:** ECMA / OASIS / NIST remote fetch (Phases 2–4). ETSI/3GPP remain Tier 1 local-file; IEEE/ISO stay non-fetch for licensing reasons.

## Usage

```bash
tads corpora
tads corpus-describe etsi
tads corpus-describe 3gpp
tads corpus-describe w3c

# IETF (auto-detect from RFC id)
tads plan inputs/RFC5905.txt --doc-id RFC5905

# W3C TR (remote fetch → inputs/)
tads fetch hr-time-3
# or: tads fetch hr-time-3 --corpus w3c

# Explicit corpus for local extracted text; use caps on huge specs
tads plan path/to/spec.txt --doc-id "TS 23.501" --corpus 3gpp \
  --max-sections 5 --max-input-tokens 20000
tads scan path/to/spec.txt --doc-id "ETSI TS 103 246-1" --corpus etsi -o outputs/etsi-demo
```

Auto-detect heuristics cover common id forms (`RFC5905`, `draft-…`, `TS 23.501`, `ETSI TS …`). When unsure, pass `--corpus`.

TOC/front matter and Index/Acknowledgments are skipped by default; `plan` prints document / eligible / analyzed coverage. See `docs/phase1.md` for scope flags.

## Design notes

Each adapter describes:

- document structure / clause organization
- normative language conventions
- reference conventions
- versioning
- editorial style

The pipeline injects that profile into prompts. Parsing stays corpus-specific; findings/report schemas remain shared.

## Related Phase 1 backlog

TOC skip, analysis-scope caps, and ingest (`.docx`/`.pdf`/zip/URL) are implemented. Google Docs API remains optional later; export to docx/pdf first. See `docs/phase1.md`.
