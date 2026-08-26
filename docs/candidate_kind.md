# Design note: candidate kind (review stance)

**Status:** Parked — not implemented. See [backlog.md](backlog.md).  
**Related:** [taxonomy.md](taxonomy.md) (`finding_type`, severity, scope), [schemas.md](schemas.md).

## Motivation

External review suggested a small review-oriented taxonomy so reports distinguish:

| Suggested label | Intent |
|-----------------|--------|
| Defect candidate | Something that looks wrong or contradictory |
| Documented constraint | A stated limit or tradeoff (not a bug) |
| Assurance gap | Long-horizon / operational safety not evidenced |
| Dependency concern | Relies on external means (other RFCs, OS, hardware, leap tables) |
| Improvement / clarification | Editorial or normative sharpening |
| Informational observation | Notable but non-actionable |

On RFC 5905–style scans, items like the ~34-year sync window or “eras from external means” are often **constraints** or **dependencies**, yet models frequently frame them as defects or absolute gaps. A stance label would make Markdown feel more like a standards review memo.

## Relationship to existing fields

Do **not** replace `finding_type`. That enum describes **mechanism** (how the issue appears in the document):

| `finding_type` (keep) | Role |
|-----------------------|------|
| `explicit_defect`, `internal_inconsistency`, … | Analytical / eval-friendly mechanism |

The reviewer’s list is a different axis: **how the reader should treat the item**.

| Axis | Answers |
|------|---------|
| `finding_type` | What kind of document issue is this? |
| `scope_relevance` | Is this a TADS time-assurance concern? |
| severity / confidence | How bad / how sure? |
| **`candidate_kind` (proposed)** | Defect vs constraint vs gap vs dependency vs clarification vs observation? |

Partial overlap today:

- Defect ≈ `explicit_defect` / `internal_inconsistency` + higher severity  
- Assurance gap ≈ `time_assurance_gap` / `missing_documentation`  
- Clarification / observation ≈ severity `low` / `info` or scope `incidental`  
- **Documented constraint** and **dependency** are the main gaps  

## Proposed shape (when implemented)

Add an orthogonal field, e.g. `candidate_kind`:

| Value | Meaning |
|-------|---------|
| `defect` | Incorrect, contradictory, or broken normative behavior |
| `constraint` | Documented limit or tradeoff (e.g. 34-year window) |
| `assurance_gap` | Missing evidence that long-horizon behavior is safe |
| `dependency` | Correctness depends on external state, specs, or services |
| `clarification` | Wording / procedure should be sharpened |
| `observation` | Informational; limited actionability |

Rules:

- Independent of severity, confidence, `validation_status`, disposition, and `scope_relevance` (same orthogonality pattern as scope).
- Prefer Markdown **sections grouped by kind** (that is the maturity win), not only a bullet label.
- Default for older JSON: e.g. `assurance_gap` or derive a best-effort map from `finding_type` — decide at implementation time.
- Avoid the phrase “defect candidate” as an enum value; “candidate” is already the public assurance vocabulary for all machine outputs.

### Minimum useful slice

If a full six-way set is too much at once, ship **`constraint` + `dependency`** first (plus clearer prompt rules for gap vs defect). Those two buy most of the “don’t call stated limits bugs” benefit.

### Cheap precursor (no schema)

Prompt-only guidance: if the document states a limit, prefer framing as a constraint (or dependency) rather than an absolute defect/absence claim. Can land before the new field.

## Non-goals

- Large taxonomy expansion beyond ~6 kinds  
- Auto-mutating disposition or severity from kind  
- Replacing scope relevance or assurance-status derivation  

## Implementation checklist (later)

1. Decision table + 8–10 fixtures (esp. constraint vs gap vs dependency)  
2. Schema + parse aliases + back-compat default  
3. Prompt framework bump  
4. Markdown grouping / summary counts by kind  
5. Soft-label check on RFC 5905 `__pf0.5.0` bake-off themes  

## Priority

High for **report UX**; similar or slightly below **counterevidence check** for false-positive reduction. Do not block Phase 0 absence discipline (already shipped).
