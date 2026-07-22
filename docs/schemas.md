# Schemas

Canonical models live in `src/tads/schemas/`. Markdown and CSV exporters (Phase 1+) derive from one JSON-shaped finding/report model.

## Finding

Required conceptual fields:

| Field | Purpose |
|-------|---------|
| `id` | Stable ID within a report (e.g. `F-001`) |
| `finding_type` | Taxonomy type |
| `title` | Short summary |
| `description` | Full explanation |
| `severity` | critical … info |
| `confidence` | high / medium / low |
| `domains` | Y2036, Y2038, general concepts, … |
| `location` | Document locator (section id, title, char offsets, quote) |
| `evidence` | One or more quotes / references |
| `machine_interpretation` | What the analyzer inferred |
| `validation_status` | unverified / verified / failed / n/a |
| `validation_detail` | Optional deterministic result |
| `recommendation_level1` | Optional remediation *direction* |
| `disposition` | Human review state |
| `reviewer_notes` | Free-form reviewer text |

## Report

A scan produces one report containing:

- Document identity (corpus, id, title, source URI/path, content hash)
- Run metadata (timestamp, provider, model, analysis mode, privacy mode)
- Cost/usage summary
- Findings list
- Optional human-review metadata (`reviewed_at`, `reviewer`)

Primary serializations: **JSON** (canonical) and **Markdown** (human-readable). CSV / SARIF / HTML come later from the same model.

## Cost estimate

Preflight object used before LLM calls:

- Estimated input / output / total tokens
- Estimated USD (when pricing table has the model)
- Budget caps (`max_cost_usd`, `max_tokens`)
- Whether the job is allowed to proceed

## Human review workflow (MVP)

1. Scanner writes `report.json` + `report.md`
2. Reviewer edits dispositions (and optional notes) in JSON
3. Optional later command re-renders Markdown from the edited JSON

No interactive TUI is required for Phase 1.
