# Privacy and retention

Privacy is a first-order principle for this scanner.

## Defaults

| Setting | Default |
|---------|---------|
| Privacy mode | `ephemeral` |
| Persist source documents | No |
| Persist findings | Only if the user writes `--output` / explicit paths |
| Log document bodies | No |
| Third-party sharing | Only the user-selected LLM provider receives document content |

## Modes

### `ephemeral` (default)

- Parse and analyze in memory
- Do not write source text to a cache directory
- Emit artifacts only to user-specified output paths
- Current ingest/conversion paths generally process document content in memory rather than creating temporary document files. If future converters or features require temporary files, they should use secure temporary-file handling, appropriate permissions, and cleanup

### `persist_outputs` (user opt-in)

- Same as ephemeral for source retention
- User explicitly asks to save JSON/Markdown (and optionally a review copy)

### `workspace` (future / explicit opt-in)

- May keep a local workspace under `inputs/` and `outputs/` for multi-step review
- Legacy `.tads/` directories (if present) are ignored; prefer the root workspace folders
- Still never uploads anywhere except the chosen LLM provider
- Not required for Phase 1

## What leaves the machine

Document text (or sections thereof) is sent to the configured LLM API endpoint as untrusted data. Embedded instructions in that text are not treated as TADS policy, but the model may still follow them. Users should treat provider choice as a data-handling decision. Local/offline models (when configured) keep content on-machine aside from any remote host the user points at (e.g. cloud Ollama).

## Logging

- Prefer structured logs with document IDs, section IDs, token counts, and finding IDs
- Never log raw section bodies at default verbosity
- TADS does not ship a prompt-dump debug flag
- Ordinary CLI and progress output is not a substitute for canonical reports
- Opt-in `--save-raw-on-error` files may contain complete model output and document excerpts; they are disabled by default, capped at 256 KiB, sensitive as a whole, and not field-sanitized
- Provider errors carry allowlisted classification only (no response bodies or headers)
- TADS-owned provenance, notes, errors, and reports omit URL userinfo, query strings, and fragments. `source_uri` is the sanitized fetch-start URL; `retrieved_uri` is emitted only when the sanitized final serving URL differs
- Document URL sanitation and provider diagnostic URL sanitation are separate helpers even when the drop rules match
- Shell history and independently enabled httpx/httpcore debug logging may still print complete URLs, headers, or bodies
- TADS does not claim perfect secret detection

## Evaluation corpus

Seed labels and manifests in `eval/corpus/` should use **public IETF documents** only. Do not commit private customer documents.
