# RFC 5905 provider comparison

Whole-document scans of the same IETF NTP v4 plain text (`inputs/RFC5905.txt`), with TOC/front matter skipped. Analysis mode for all four Cloud/API runs: **whole_document**.

Finding **counts are not quality scores**. Human review remains authoritative. Prefer themes shared by multiple providers as gold-label candidates.

Sources:

| Provider     | Report                                                   |
|--------------|----------------------------------------------------------|
| Ollama Cloud | `outputs/RFC5905__ollama__gpt-oss-120b.json`             |
| OpenAI       | `outputs/RFC5905__openai__gpt-4.1-mini.json`             |
| Anthropic    | `outputs/RFC5905__anthropic__claude-sonnet-4-5.json`     |
| Gemini       | `outputs/RFC5905__gemini__gemini-3.6-flash.json`         |

Local Ollama whole-doc runs (`llama3.2:3b`, `llama3.1:8b`) are **excluded**: they reported 0 findings because default local context (~4k tokens) truncated the ~60k-token document.

## Run metrics

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

## Severity mix

| Provider     | Critical | High | Medium | Low | Info |
|--------------|----------|------|--------|-----|------|
| Ollama Cloud | 1        | 6    | 3      | 0   | 0    |
| OpenAI       | 1        | 1    | 4      | 1   | 0    |
| Anthropic    | 0        | 1    | 4      | 4   | 1    |
| Gemini       | 0        | 1    | 3      | 0   | 0    |

## Theme coverage (heuristic)

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

## Notable findings (sample)

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

## How to use this comparison

1. Treat **era / leap / signedness / sync** themes shared by ≥3 providers as primary review and gold-label candidates.
2. Review singleton findings separately (e.g. Gemini IPv6 refid MD5, Ollama LOG2D macro) — they may be insightful or noisy.
3. Do not rank providers by raw finding count alone. Ollama’s set is severity-heavy; Anthropic’s is broader/softer; Gemini is sparse but on-theme.

## Reproduce

```bash
export DOC=inputs/RFC5905.txt
export ID=RFC5905

time tads scan "$DOC" --doc-id "$ID" --provider ollama --model gpt-oss:120b \
  --overwrite -y -o outputs/RFC5905__ollama__gpt-oss-120b
time tads scan "$DOC" --doc-id "$ID" --provider openai --model gpt-4.1-mini \
  --overwrite -y -o outputs/RFC5905__openai__gpt-4.1-mini
time tads scan "$DOC" --doc-id "$ID" --provider anthropic --model claude-sonnet-4-5 \
  --overwrite -y -o outputs/RFC5905__anthropic__claude-sonnet-4-5
time tads scan "$DOC" --doc-id "$ID" --provider gemini --model gemini-3.6-flash \
  --overwrite -y -o outputs/RFC5905__gemini__gemini-3.6-flash
```

See also [QUICK_START.md](../QUICK_START.md) for provider setup and naming conventions.
