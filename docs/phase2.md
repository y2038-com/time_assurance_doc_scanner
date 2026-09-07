# Phase 2 — Corpus Awareness

> **Status: Current corpus and fetch-tier reference.**
> This document originated as the Phase 2 plan and describes the adapters and
> remote-fetch behavior in use today. Historical fetch design detail:
> [fetch_tier2_plan.md](fetch_tier2_plan.md). Architecture overview:
> [architecture.md](architecture.md).

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
| **W3C** | Fetch-enabled: `tads fetch hr-time-3` → latest `https://www.w3.org/TR/<shortname>/` (HTML→text). Design history: [fetch_tier2_plan.md](fetch_tier2_plan.md). |
| **ECMA** | Fetch-enabled (curated): `tads fetch ECMA-404` (PDF), `tads fetch ECMA-262` (pinned HTML edition; use scan caps). |
| **OASIS** | Fetch-enabled (curated): `tads fetch OpenFormula` → ODF v1.4 Part 4 OS PDF; `OpenFormula-1.3` for v1.3. |
| **NIST** | Fetch-enabled (curated): `tads fetch "SP 800-57 Part 1 Rev. 5"` (or `SP-800-57pt1r5`); also `FIPS-140-3`, `SP-800-90Ar1`. Bare `SP 800-57` rejected as ambiguous. |
| `itu-t`, `ieee`, `iso` | Stubs (clause parse + prompt metadata); local-file or `tads convert <url>` |

**Parked:** Expanding NIST/OASIS/ECMA catalogs as the bench grows. ETSI/3GPP remain Tier 1 local-file; IEEE/ISO stay non-fetch for licensing reasons.

## Usage

```bash
tads corpora
tads corpus-describe etsi
tads corpus-describe 3gpp
tads corpus-describe w3c
tads corpus-describe ecma
tads corpus-describe oasis
tads corpus-describe nist

# IETF (auto-detect from RFC id)
tads plan inputs/RFC5905.txt --doc-id RFC5905

# W3C TR (remote fetch → inputs/)
tads fetch hr-time-3
# or: tads fetch hr-time-3 --corpus w3c

# ECMA curated fetch
tads fetch ECMA-404
# tads fetch ECMA-262   # large; plan/scan with --max-sections / --max-input-tokens

# OASIS curated fetch (OpenFormula / ODF Part 4)
tads fetch OpenFormula
# tads fetch OpenFormula-1.3

# NIST curated fetch (part/rev required when ambiguous)
tads fetch "SP 800-57 Part 1 Rev. 5"
# tads fetch SP-800-57pt1r5
# tads fetch FIPS-140-3

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
