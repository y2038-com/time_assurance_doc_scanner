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
| `validation_status` | Deterministic check only: unverified / verified / failed / n/a. JSON value `verified` means deterministically checked, **not** human-validated. |
| `validation_detail` | Optional deterministic result |
| `source_verified` | Evidence quote found in analyzed document text (bool; default false) |
| `source_verification_detail` | Optional source-match summary |
| `recommendation_level1` | Optional remediation *direction* |
| `disposition` | Human review state (`accepted` = human-confirmed) |
| `reviewer_notes` | Free-form reviewer text |

**Public assurance status** (Markdown / CLI; derived — not a separate stored enum):

| Derived status | From | Public label |
|----------------|------|--------------|
| `candidate` | default | candidate for review |
| `source_verified` | `source_verified=true` | source-verified candidate |
| `deterministically_validated` | `validation_status=verified` | deterministically checked candidate |
| `human_confirmed` | `disposition=accepted` | validated finding (human-confirmed) |
| `rejected` | `disposition=rejected` | rejected |
| `deferred` | `disposition=needs_review` | deferred |

Precedence: rejected > human_confirmed > deterministically_validated > source_verified > candidate (deferred via disposition). Reserve **validated finding** for `human_confirmed` only. Older JSON without `source_verified` loads as `false`.

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

1. Scanner writes `report.json` + `report.md` (Markdown titles items **Candidates for review**)
2. Reviewer edits dispositions (and optional notes) in JSON — `accepted` promotes to a **validated finding**
3. `tads render report.json` refreshes Markdown from the edited JSON

No interactive TUI is required for Phase 1.
