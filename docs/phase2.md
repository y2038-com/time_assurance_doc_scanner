# Phase 2 — Corpus Awareness

Add corpus-specific intelligence while keeping a common scanner engine.

## Tier 1 (implemented)

| Corpus | Adapter | Remote fetch | Notes |
|--------|---------|--------------|-------|
| IETF | `ietf` | Yes (`.txt`) | RFC / Internet-Draft |
| ETSI | `etsi` | Local file | Clause/annex sectionizer + ETSI profile |
| 3GPP | `3gpp` | Local file | Clause/annex sectionizer + 3GPP profile |

## Tier 2 (stubs)

Registered with generic clause parsing and prompt metadata:

`itu-t`, `ieee`, `w3c`, `oasis`, `nist`, `iso`, `ecma`

Also note ECMA International as a Tier 2 stub (local-file first; deepen later if needed).

## Usage

```bash
tads corpora
tads corpus-describe etsi
tads corpus-describe 3gpp

# IETF (auto-detect from RFC id)
tads plan inputs/RFC5905.txt --doc-id RFC5905

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
