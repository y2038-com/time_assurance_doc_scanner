# RFC 5905 provider comparison

Whole-document scans of the same IETF NTP v4 plain text (`inputs/RFC5905.txt`), with TOC/front matter skipped. Analysis mode for all Cloud/API runs: **whole_document**.

Finding **counts are not quality scores**. Human review remains authoritative. Prefer themes shared by multiple providers as soft-label candidates (multi-provider agreement is the practical proxy for gold labels).

This document has two layers:

1. **Original bake-off** (below) — early prompt frameworks; useful for theme coverage and provider personality.
2. **Prompt framework 0.5.0 re-run** ([appendix](#appendix-prompt-framework-050-re-run)) — absence-claim discipline; use `__pf0.5.0` outputs as the current comparison baseline.

## Original bake-off (historical)

Sources (untagged paths; **do not overwrite** when re-running):

| Provider     | Report                                                   | Prompt framework (on disk) |
|--------------|----------------------------------------------------------|----------------------------|
| Ollama Cloud | `outputs/RFC5905__ollama__gpt-oss-120b.json`             | 0.2.0 (**0 findings** on disk; table below reflects an earlier successful run) |
| OpenAI       | `outputs/RFC5905__openai__gpt-4.1-mini.json`             | 0.2.0 |
| Anthropic    | `outputs/RFC5905__anthropic__claude-sonnet-4-5.json`     | 0.2.0 |
| Gemini       | `outputs/RFC5905__gemini__gemini-3.6-flash.json`         | 0.4.0 |

Local Ollama whole-doc runs (`llama3.2:3b`, `llama3.1:8b`) are **excluded**: they reported 0 findings because default local context (~4k tokens) truncated the ~60k-token document.

### Run metrics

| Provider     | Model                 | Findings | Runtime | Est. USD (preflight) | Actual USD (usage) | Crit / High | In / Out tokens (actual) |
|--------------|-----------------------|----------|---------|----------------------|--------------------|-------------|--------------------------|
| Ollama Cloud | `gpt-oss:120b`        | **10**   | 48.5 s  | n/a (no pricing row) | n/a                | 1 / 6       | 51,650 / 5,598           |
| OpenAI       | `gpt-4.1-mini`        | **7**    | 23.4 s  | **$0.031**           | **$0.024**         | 1 / 1       | 51,586 / 1,983           |
| Anthropic    | `claude-sonnet-4-5`   | **10**   | 109 s   | **$0.242**           | **$0.254**         | 0 / 1       | 59,646 / 4,993           |
| Gemini       | `gemini-3.6-flash`    | **4**    | 13.7 s  | **$0.121**           | **$0.092**         | 0 / 1       | 56,615 / 909             |

Notes:

- **Est. USD** = `cost_estimate.estimated_cost_usd` (indicative pricing × estimated tokens at plan time).
- **Actual USD** = `actual_cost_usd` (indicative pricing × reported usage), when available.
- Ollama Cloud has no entry in the built-in pricing table, so USD fields are unavailable even though the run succeeded.
- Among billed providers on this slice: OpenAI cheapest (~$0.02–0.03), then Gemini (~$0.09–0.12), then Anthropic (~$0.24–0.25).

### Severity mix

| Provider     | Critical | High | Medium | Low | Info |
|--------------|----------|------|--------|-----|------|
| Ollama Cloud | 1        | 6    | 3      | 0   | 0    |
| OpenAI       | 1        | 1    | 4      | 1   | 0    |
| Anthropic    | 0        | 1    | 4      | 4   | 1    |
| Gemini       | 0        | 1    | 3      | 0   | 0    |

### Theme coverage (heuristic)

Themes assigned from finding titles/descriptions (keyword buckets), not exact finding IDs. “Yes” means at least one finding matched that theme.

| Theme                                       | Ollama | OpenAI | Anthropic | Gemini |
|---------------------------------------------|--------|--------|-----------|--------|
| NTP era / 2036 / 32-bit seconds             | Yes    | Yes    | Yes       | Yes    |
| Leap second handling                        | —      | Yes    | Yes       | Yes    |
| Y2038 / signed time (Unix conversion)       | Yes    | Yes    | Yes       | —      |
| Sync / stratum / clock discipline           | Yes    | Yes    | Yes       | Yes    |
| On-wire / packet / timestamp format         | Yes    | Yes    | Yes       | Yes    |
| Calendar / pre-1972 UTC / date range        | Yes    | Yes    | Yes       | —      |
| Dispersion / `^` operator bug               | Yes    | Yes    | —         | —      |
| Client within ~34y of server (era window)   | —      | —      | Yes       | Yes    |

### Notable findings (sample)

| Provider  | Severity | Title                                                        |
|-----------|----------|--------------------------------------------------------------|
| Ollama    | critical | Insufficient guidance for NTP era rollover (post-2036)       |
| Ollama    | high     | Incorrect use of `^` for exponentiation in dispersion        |
| OpenAI    | critical | Incorrect bitwise XOR in clock filter dispersion             |
| OpenAI    | high     | 64-bit timestamp era rollover / client-server Δt limits      |
| Anthropic | high     | NTP era rollover in 2036 not addressed in operations         |
| Anthropic | medium   | No specification for leap second insertion/deletion          |
| Gemini    | high     | On-wire 32-bit seconds rollover in 2036 (external era)       |
| Gemini    | medium   | Underspecified leap second insertion and slewing             |

### How to use this comparison

1. Treat **era / leap / signedness / sync** themes shared by ≥3 providers as primary review and soft-label candidates.
2. Review singleton findings separately (e.g. Gemini IPv6 refid MD5, Ollama LOG2D macro) — they may be insightful or noisy.
3. Do not rank providers by raw finding count alone. Ollama’s set is severity-heavy; Anthropic’s is broader/softer; Gemini is sparse but on-theme.

---

## Appendix: prompt framework 0.5.0 re-run

Re-scan after **Phase 0 absence-claim discipline** (prompt framework **0.5.0**): models must search the analyzed text before claiming “not addressed / undefined / no guidance,” prefer narrowed gaps, and cite related passages when present. Naming: append `__pf0.5.0` so historical outputs are preserved.

### Sources

| Provider     | Report |
|--------------|--------|
| Ollama Cloud | `outputs/RFC5905__ollama__gpt-oss-120b__pf0.5.0.json` |
| OpenAI       | `outputs/RFC5905__openai__gpt-4.1-mini__pf0.5.0.json` |
| Anthropic    | `outputs/RFC5905__anthropic__claude-sonnet-4-5__pf0.5.0.json` |
| Gemini       | `outputs/RFC5905__gemini__gemini-3.6-flash__pf0.5.0.json` |

### Run metrics (pf0.5.0)

| Provider     | Model               | Findings | Runtime (wall) | Est. USD | Actual USD | Crit / High | In / Out tokens |
|--------------|---------------------|----------|----------------|----------|------------|-------------|-----------------|
| Ollama Cloud | `gpt-oss:120b`      | **6**    | ~34 s          | n/a      | n/a        | 1 / 1       | 52,651 / 4,066  |
| OpenAI       | `gpt-4.1-mini`      | **8**    | ~47 s          | $0.031   | $0.026     | 0 / 1       | 52,587 / 3,181  |
| Anthropic    | `claude-sonnet-4-5` | **8**    | ~150 s         | $0.242   | $0.293     | 0 / 1       | 60,758 / 7,371  |
| Gemini       | `gemini-3.6-flash`  | **4**    | ~30 s          | $0.121   | $0.102     | 0 / 1       | 57,676 / 2,013  |

### Counts vs historical (same model)

| Provider  | Historical findings | pf0.5.0 | Δ   | Historical prompt (on disk) |
|-----------|---------------------|---------|-----|-----------------------------|
| Ollama    | 10 (table) / **0** on disk | 6 | n/a (no usable on-disk baseline) | 0.2.0 |
| OpenAI    | 7                   | 8       | +1  | 0.2.0 |
| Anthropic | 10                  | 8       | −2  | 0.2.0 |
| Gemini    | 4                   | 4       | 0   | 0.4.0 |

**Caveat:** OpenAI/Anthropic historical files are prompt **0.2.0**; Gemini historical is **0.4.0** (already had scope relevance). Count and theme deltas therefore mix Phase 0 with other prompt/schema drift — they are directional, not a pure A/B.

### Scope mix (pf0.5.0 only)

| Provider  | core | supporting | incidental | out_of_scope |
|-----------|------|------------|------------|--------------|
| Ollama    | 3    | 2          | 1          | 0            |
| OpenAI    | 6    | 1          | 1          | 0            |
| Anthropic | 7    | 1          | 0          | 0            |
| Gemini    | 3    | 1          | 0          | 0            |

### What improved (Phase 0 signal)

- **Gemini (strongest):** Interpretations explicitly record document search (e.g. “Searched document for era / leap…”). Era finding acknowledges §6 era discussion while keeping the on-wire / external-state gap. Leap reframed as encoding ambiguity. IPv6 RefID remains `supporting`.
- **Anthropic F-001:** *“Not Explicitly Addressed in Protocol Operations”* → *“lacks operational guidance”* with quotes that eras exist and are external — the intended absence-claim reframe.
- **OpenAI titles:** Softened *“Lack of…”* toward *“Limited explicit guidance…”* on era/leap.

### What did not improve enough / regressions

- **Anthropic:** Still produces a stack of absence-style era findings (persistence, broadcast, discipline, signaling). **Leap-second theme dropped** vs historical.
- **OpenAI:** Still heavy “does not specify / no explicit…” wording; added incidental timezone noise; **lost the XOR/`^` dispersion defect** present in the historical run.
- Absence **keyword counts** are a poor score: bodies may still say “does not define” even when titles and framing improve. Prefer “related text acknowledged?” over phrase tally.

### Theme coverage (pf0.5.0, heuristic)

| Theme                         | Ollama | OpenAI | Anthropic | Gemini |
|-------------------------------|--------|--------|-----------|--------|
| NTP era / 2036 / 32-bit       | Yes    | Yes    | Yes       | Yes    |
| Leap second                   | Yes    | Yes    | —         | Yes    |
| ~34y / sync window            | Yes    | Yes    | Yes       | Yes    |
| Signedness / arithmetic       | Yes    | Yes    | Yes       | Yes    |
| Dispersion / `^` XOR bug      | —      | —      | —         | —      |
| IPv6 RefID / MD5              | —      | —      | —         | Yes (`supporting`) |
| UTC pre-1972 / calendar       | Yes    | —      | —         | Yes    |

Shared soft positives (≥3 providers): **era/2036**, **34y window**, **signedness/arithmetic**. Leap remains multi-provider except Anthropic this run.

### Verdict

Keep Phase 0. Residual Anthropic-style “no guidance on X” stacks still motivate an optional **Phase 1 counterevidence** enrichment (targeted second pass; do not auto-reject). Do not treat count deltas alone as success.

---

## Reproduce

**Current baseline (prompt 0.5.0)** — preferred for new comparisons:

```bash
export DOC=inputs/RFC5905.txt
export ID=RFC5905

time tads scan "$DOC" --doc-id "$ID" --provider ollama --model gpt-oss:120b \
  --overwrite -y -o outputs/RFC5905__ollama__gpt-oss-120b__pf0.5.0
time tads scan "$DOC" --doc-id "$ID" --provider openai --model gpt-4.1-mini \
  --overwrite -y -o outputs/RFC5905__openai__gpt-4.1-mini__pf0.5.0
time tads scan "$DOC" --doc-id "$ID" --provider anthropic --model claude-sonnet-4-5 \
  --overwrite -y -o outputs/RFC5905__anthropic__claude-sonnet-4-5__pf0.5.0
time tads scan "$DOC" --doc-id "$ID" --provider gemini --model gemini-3.6-flash \
  --overwrite -y -o outputs/RFC5905__gemini__gemini-3.6-flash__pf0.5.0
```

**Historical untagged paths** (original bake-off; avoid `--overwrite` unless intentional):

```bash
# … -o outputs/RFC5905__{provider}__{model}   # no __pf tag
```

See also [QUICK_START.md](../QUICK_START.md) for provider setup and naming conventions.
