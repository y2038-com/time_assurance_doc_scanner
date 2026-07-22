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
- Temporary files, if unavoidable, are created with restrictive permissions and deleted on success/failure paths

### `persist_outputs` (user opt-in)

- Same as ephemeral for source retention
- User explicitly asks to save JSON/Markdown (and optionally a review copy)

### `workspace` (future / explicit opt-in)

- May keep a local workspace under `.tads/` for multi-step review
- Still never uploads anywhere except the chosen LLM provider
- Not required for Phase 1

## What leaves the machine

Document text (or sections thereof) is sent to the configured LLM API endpoint. Users should treat provider choice as a data-handling decision. Local/offline models (when configured) keep content on-machine aside from any remote host the user points at (e.g. cloud Ollama).

## Logging

- Prefer structured logs with document IDs, section IDs, token counts, and finding IDs
- Never log raw section bodies at default verbosity
- Debug flags that dump prompts must warn that secrets/document content may appear

## Evaluation corpus

Seed labels and manifests in `eval/corpus/` should use **public IETF documents** only. Do not commit private customer documents.
