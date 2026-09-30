# Changelog

All notable user-visible changes to TADS are recorded here.

Package versions use PEP 440 (`0.6.0rc1`). The corresponding Git tag for this
candidate, when created, will be `v0.6.0-rc.1`. Public report schema and prompt
framework versions are independent of the package version.

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
