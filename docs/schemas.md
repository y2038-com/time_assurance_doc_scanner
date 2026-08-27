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
| `validation_detail` | Optional deterministic result summary |
| `time_representation` | Optional structured counter params (width, signed, epoch, unit, tick rate, claimed horizon) |
| `horizon_validation` | Optional structured calculator result (bounds, instants, claim_consistent, notes) |
| `source_verified` | Evidence quote found in analyzed document text (bool; default false) |
| `source_verification_detail` | Optional source-match summary |
| `recommendation_level1` | Optional remediation *direction* |
| `scope_relevance` | Relevance to TADS mission: `core` / `supporting` / `incidental` / `out_of_scope` (default `core` for older JSON) |
| `scope_rationale` | Optional 1–2 sentence explanation of the scope label |
| `disposition` | Human review state (`accepted` = human-confirmed) |
| `reviewer_notes` | Free-form reviewer text |

**Scope relevance** is orthogonal to severity, confidence, `validation_status`, and disposition — setting scope never auto-mutates those fields. `out_of_scope` means “not a TADS time-assurance concern,” not “technically unimportant.” Markdown shows `core` + `supporting` in the main section, `incidental` in a lower section, and omits `out_of_scope` by default (all remain in JSON).

**Public assurance status** (Markdown / CLI; derived — not a separate stored enum):

| Derived status | From | Public label |
|----------------|------|--------------|
| `candidate` | default | candidate for review |
| `source_verified` | `source_verified=true` | source-verified candidate |
| `deterministically_validated` | `validation_status=verified` | deterministically checked candidate |
| `human_confirmed` | `disposition=accepted` | validated finding (human-confirmed) |
| `rejected` | `disposition=rejected` | rejected |
| `deferred` | `disposition=needs_review` | deferred |

Precedence: rejected > human_confirmed > deterministically_validated > source_verified > candidate (deferred via disposition). Reserve **validated finding** for `human_confirmed` only. Older JSON without `source_verified` / horizon / scope fields loads with those fields absent/`false`/`core`.

When `time_representation` is present, `apply_horizon_validation` fills `horizon_validation` and maps calculator status onto `validation_status` (`verified`→verified, `contradicted`→failed, insufficient/unsupported→not_applicable). This never changes `disposition`.

The scan prompt (framework ≥ 0.5.0) asks the model for `scope_relevance` / `scope_rationale` and for `time_representation` with document-established values only; use `null` when unknown. Empty all-null time objects are dropped so the older ISO-date heuristic can still run. Invalid/missing scope defaults to `core`. Framework ≥ 0.5.0 also requires searching the analyzed text before strong absence claims (“not addressed,” “undefined,” “no guidance”); a structured second-pass counterevidence check remains backlog.

## Report

A scan produces one report containing:

- Document identity (corpus, id, title, source URI/path, content hash)
- Run metadata (timestamp, provider, model, analysis mode, privacy mode)
- Cost/usage summary
- Findings list
- Optional human-review metadata (`reviewed_at`, `reviewer`)

Primary serializations: **JSON** (canonical) and **Markdown** (human-readable). CSV / SARIF / HTML come later from the same model.

### Provenance / reproducibility

JSON run + document metadata allow others to reproduce or compare scans: `content_sha256`, `scanner_version`, `prompt_framework_version`, provider/model, `analysis_mode`, and run timestamps (plus scope caps). These are populated on scan today. The Markdown report header surfaces **Content SHA-256** (when present), **Scanner** (package version), and **Prompt framework** (prompt template version), along with provider/model and analysis mode. `tads render` uses the same projection.

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
