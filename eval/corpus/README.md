# Bootstrap evaluation corpus

Phase 0 ships a **shortlist + labeling guide + harness**, not a complete gold set.
Labels are filled as we review public documents.

## Proposed seed RFCs

| Doc | Why it is useful for time assurance |
|-----|-------------------------------------|
| [RFC 5905](https://www.rfc-editor.org/rfc/rfc5905.txt) | NTPv4 — 32-bit seconds, era / **Y2036** |
| [RFC 4330](https://www.rfc-editor.org/rfc/rfc4330.txt) | SNTPv4 — related timestamp representation |
| [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.txt) | Internet date/time profile — ranges, leap seconds, UTC |
| [RFC 5280](https://www.rfc-editor.org/rfc/rfc5280.txt) | X.509 — certificate validity horizons |
| [RFC 3550](https://www.rfc-editor.org/rfc/rfc3550.txt) | RTP — 32-bit timestamp wraparound |

These are starting points, not an exclusive list. Prefer **public IETF texts only** in this tree.

## Labeling guide

For each expected finding in `labels/<doc_id>.json`:

1. Skim for epoch, width, signedness, rollover, leap seconds, validity periods.
2. Record `finding_type`, optional `domains`, and at least one of:
   - `section_id_contains` (e.g. `"s-6"`)
   - `quote_contains` (distinctive substring from the RFC)
3. Add `notes` explaining why this is a real assurance issue.
4. Keep labels sparse and high-quality (quality over quantity).

Example shape:

```json
{
  "doc_id": "RFC5905",
  "findings": [
    {
      "id": "E-001",
      "finding_type": "implied_assumption",
      "domains": ["y2036", "rollover"],
      "section_id_contains": "s-6",
      "quote_contains": "era",
      "notes": "Era / wrap behavior should be assessed for long-horizon clarity."
    }
  ]
}
```

## Workflow

1. Fetch plain text (`tads fetch` for IETF and open Tier-2 corpora; otherwise `tads convert` or local file).
2. Run scanner (Phase 1) → `report.json`.
3. Compare with `tads eval` (Phase 1 CLI) / harness helpers in `tads.eval`.
4. Promote stable, human-accepted findings into labels.

Empty or partial label files are expected early on.
