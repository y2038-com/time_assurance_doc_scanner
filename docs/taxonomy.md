# Time Assurance Taxonomy

The scanner reasons about **long-horizon time assurance**, not keyword hits for “Y2038”.

## Priority domains (initial knowledge emphasis)

### High priority

| Domain | Summary |
|--------|---------|
| **Y2036** | NTP 32-bit seconds / era rollover (7 Feb 2036) |
| **Y2038** | 32-bit signed Unix `time_t` overflow (19 Jan 2038) |

### Medium priority

| Domain | Summary |
|--------|---------|
| **Y2100** | RTC / leap-year implementation edge cases around year 2100 |
| **Y2106** | 32-bit unsigned Unix time overflow (7 Feb 2106) |

## General time-assurance concepts

The knowledge base and prompts should recognize (non-exhaustive):

- Epoch assumptions
- Time representations (width, signedness, units)
- Signed vs unsigned values
- Date ranges and supported horizons
- Calendar assumptions
- Leap years / leap seconds
- UTC, TAI, GPS time
- Monotonic clocks
- Relative vs absolute time
- Serialization and persistence
- Synchronization
- Long-term archival
- Certificate / credential validity
- Scheduling and timers
- Migration and transition behavior
- Undefined rollover behavior
- Missing documentation of time behavior

Future modules (new eras, protocol-specific pitfalls) must plug in without changing core architecture.

## Finding types (Phase 1)

| Type | Meaning |
|------|---------|
| `explicit_defect` | Incorrect or contradictory time-related statement |
| `internal_inconsistency` | Conflict between sections / normative vs informative text |
| `missing_documentation` | Required time behavior not specified |
| `implied_assumption` | Unstated assumption about epoch, range, or clock |
| `time_assurance_gap` | Missing evidence that long-horizon behavior is safe |
| `lifetime_representation_mismatch` | Stated lifetime incompatible with representation width/type |

## Severity (initial scale)

| Level | Guidance |
|-------|----------|
| `critical` | Incorrect normative behavior likely to cause interoperability or safety failure at a known horizon |
| `high` | Clear defect or undefined rollover in a normative path |
| `medium` | Material ambiguity or missing assurance for long-lived systems |
| `low` | Editorial / minor clarity issue with limited assurance impact |
| `info` | Notable observation without a clear defect |

Severity is advisory; human disposition is authoritative.

## Confidence (initial scale)

| Level | Guidance |
|-------|----------|
| `high` | Direct quote + clear taxonomy match; optionally deterministically verified |
| `medium` | Reasonable inference from nearby context |
| `low` | Speculative; needs human confirmation |

## Validation status (deterministic checks only)

| Status | Meaning |
|--------|---------|
| `unverified` | No deterministic check applied |
| `verified` | Deterministic check confirms a claimed fact (e.g. horizon date) — public label: **deterministically checked candidate**, not a validated finding |
| `failed` | Deterministic check contradicts the interpretation |
| `not_applicable` | No deterministic check exists for this finding |

## Disposition (human review)

| Disposition | Meaning | Public assurance status |
|-------------|---------|-------------------------|
| `new` | Fresh machine candidate, not yet reviewed | `candidate` |
| `accepted` | Reviewer agrees this is a real issue | `human_confirmed` (**validated finding**) |
| `rejected` | Reviewer marks false positive / not actionable | `rejected` |
| `needs_review` | Parked for further analysis | `deferred` |
| `edited` | Reviewer modified fields (type, severity, notes, …) | still `candidate` unless other signals apply |

## Public assurance vocabulary

Reports lead with **candidates for review**. Reserve **validated finding** for human-confirmed items (`disposition=accepted`). See `docs/schemas.md` and `tads.schemas.assurance`.

Phase 7 may expand workflow states (triaged, submitted, resolved, …). The Phase 0 schema keeps `disposition` + free-form `reviewer_notes` so the registry can attach later without rewriting findings.
