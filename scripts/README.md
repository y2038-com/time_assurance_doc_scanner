# scripts/

Optional research harnesses for the **12-document multi-corpus benchmark**. Not part of the core `tads` CLI — prefer `tads plan` / `tads scan` for everyday use.

| Script | Purpose |
|--------|---------|
| `bench_common.py` | Shared named configurations, output identity, resume matching, manifests |
| `plan_bench_costs.py` | Cost preflight across selected configurations (no LLM / provider calls) |
| `run_bench_scans.py` | Execute scans for selected configurations; resume + manifest |

## Historical defaults (script-specific)

Runner and planner keep **separate** historical default matrices. Explicit
`--config` / `--model` selection is shared and behaves identically in both.

**Runner** (`run_bench_scans.py`) with no `--config` / `--model`:

- `openai-mini` → OpenAI / `gpt-4.1-mini`
- `gemini-flash` → Gemini / `gemini-3.6-flash`

**Planner** (`plan_bench_costs.py`) with no `--config` / `--model` (pre-branch
four-model order):

- Ollama / `gpt-oss:120b`
- OpenAI / `gpt-4.1-mini`
- Anthropic / `claude-sonnet-4-5`
- Gemini / `gemini-3.6-flash`

```bash
# From repo root, with .venv active and provider keys in .env:
python scripts/plan_bench_costs.py
python scripts/run_bench_scans.py --dry-run
python scripts/run_bench_scans.py --skip-existing --max-cost-usd 5.00 --tag bench2026
```

Output prefixes for the runner’s unchanged default keep the legacy shape
`<doc>__<provider>__<model>[__tag]` (for example
`RFC5905__openai__gpt-4.1-mini__bench2026`).

## Named configurations and exact models

| `--config` name | Provider | Model | Reasoning |
|-----------------|----------|-------|-----------|
| `openai-mini` | openai | `gpt-4.1-mini` | _(omit)_ |
| `gemini-flash` | gemini | `gemini-3.6-flash` | _(omit)_ |
| `openai-luna-none` | openai | `gpt-6-luna` | `none` |
| `openai-luna-low` | openai | `gpt-6-luna` | `low` |

Exact combinations use `--model provider/model` or
`--model provider/model/effort`. The `/` separator allows Ollama ids that
contain `:` (example: `ollama/gpt-oss:120b`). Reasoning models such as
`gpt-6-luna` **require** an explicit effort segment.

**Limitation:** model identifiers that themselves contain `/` are **not**
supported by this CLI grammar. Extra slash components are rejected with a
controlled error (they are not misread as reasoning effort). Prefer a named
`--config` or a model id without `/`.

```bash
# Mini + Luna none/low + Gemini
python scripts/run_bench_scans.py --dry-run \
  --config openai-mini --config openai-luna-none \
  --config openai-luna-low --config gemini-flash \
  --tag luna-cmp

# Caller-specified Anthropic / Ollama (no invented runner defaults)
python scripts/run_bench_scans.py --dry-run \
  --model anthropic/claude-sonnet-4-5 \
  --model ollama/gpt-oss:120b \
  --tag alt-providers
```

Duplicate configuration ids are rejected before any subprocess starts.
Unsupported reasoning effort is rejected using TADS model-policy helpers (no
second policy table).

## Controlled chunked comparison vs automatic planning

**Automatic planning** (default): TADS chooses whole-document vs section-aware
mode and packs sections with the CLI defaults. Candidate counts and costs are
not directly comparable across documents of different sizes.

**Controlled comparison** (recommended for model bake-offs): force the same
analysis shape for every document, for example:

```bash
python scripts/run_bench_scans.py --dry-run \
  --config openai-mini --config openai-luna-none \
  --config openai-luna-low --config gemini-flash \
  --force-sections --max-sections-per-request 8 \
  --tag chunked-cmp-8
```

`--force-sections` and packing flags are passed through to `tads`. They do not
change TADS defaults when omitted. Planner flags match the runner
(`--force-sections`, `--no-section-chunking`, `--max-sections-per-request`,
`--max-output-tokens`). Explicit `--max-output-tokens` appears on constructed
scan commands, plan-only displayed commands, cost planning, prefix identity,
and manifest controls.

## Output-prefix identity

Prefixes are collision-safe under `--outputs-dir`:

```text
<doc>__<provider>__<model>[__re-<effort>][__fs][__nc][__mspr-N][__mot-N][__tag]
```

- Reasoning effort appears only when the configuration sets it (`re-none` vs `re-low`).
- `fs` = `--force-sections`; `nc` = `--no-section-chunking`.
- `mspr-N` / `mot-N` appear when those flags are explicitly supplied.
- Tags already matching the safe filename grammar (letters/digits/`.`/`_`/`-`,
  including dotted release-style names such as `0.6.0rc4-bench`) are preserved
  unchanged. Tags that require lossy normalization get a short deterministic
  digest of the **original** tag so `foo bar` and `foo_bar` never collide.
- Absolute paths, `/`, `\`, traversal, and control characters are refused or
  neutralized; prefixes stay a single component under `--outputs-dir`.

Successful scans still write `<prefix>.json` and `<prefix>.md` by **concatenating**
suffixes (dotted models/tags are not truncated by `Path.with_suffix`).

## Resume (`--skip-existing`)

Resume uses the canonical JSON report at `<prefix>.json` (paid-scan completion
artifact), loaded through `load_report_json` (no second schema allowlist):

- Matching JSON + Markdown → skip (no provider call, no rewrite).
- Matching JSON, missing Markdown → regenerate Markdown only.
- Markdown-only, malformed, loader-rejected, or metadata mismatch → fail closed
  (no scan, no overwrite).
- Raw-only (`.raw.txt`) remains eligible for a fresh scan.
- Ctrl+C stops the active child, starts no later job, exits **130** (no traceback).

Identity checks include provider, model, and reasoning effort (`none` vs `low`
must not satisfy each other; non-reasoning configs expect `run.reasoning_effort`
null). When schema 0.5.0 batching metadata is present:

- Forced section-aware mode rejects `whole_document`.
- Chunking-enabled forced mode accepts `section_chunks`, or `per_section` when
  automatic packing produces singleton requests.
- `--no-section-chunking` requires `per_section`.

Settings that are **not** stored on the public report (notably
`max_sections_per_request`) rely on collision-safe **prefix-only** identity.
Explicit `max_output_tokens` is verified from the report when present; legacy
null relies on prefix identity (no invented report-level claim).

## Manifest / summary

Each non-dry run writes a machine-readable manifest (atomic replace into a
regular file only — symlinks, directories, FIFOs, sockets, and device nodes are
refused without modifying the target):

- no tag → `<outputs-dir>/bench_manifest.json`
- with tag → `<outputs-dir>/bench_manifest__<tag-slug>.json`

The exact original `--tag` is stored in `benchmark_tag`. A tag identifies one
run definition. Before creating or rewriting a same-tag manifest, the harness
compares the nonvolatile definition (exact tag, ordered documents, ordered
configurations, effective controls including force-sections / chunking /
max-sections-per-request / max-output-tokens / max-cost, plus `git_commit` and
`git_dirty`, including null when Git is unavailable). Volatile fields (creation
time, job status, elapsed time, report metrics, interruption) are ignored for
compatibility. Equivalent resumes merge job rows by identity and preserve prior
executed provenance. **Incompatible same-tag definitions fail closed** — choose
a new `--tag`; the existing manifest and artifacts are left untouched.

**Definitive comparisons should run from a clean committed tree** so Git
provenance is stable and meaningful.

Fields include: `manifest_format_version`, `benchmark_tag`, `created_at_utc`,
`git_commit`, `git_dirty`, selected documents, configurations (with per-config
reasoning effort), effective controls, and per-job `output_prefix`, `status`,
optional `elapsed_seconds`, and report comparison fields when available.
Missing values stay JSON `null` — never fabricated zeros for cost or coverage.
Interrupted runs keep completed/skipped entries and mark the active job
interrupted.

Terminal aggregates distinguish complete totals from partial known subtotals
(for example `actual_cost_usd=$0.120000 (partial: 1/2 jobs)` or
`actual_cost_usd=n/a (0/2 jobs)`). Failed, invalid, interrupted, pending, and
legacy jobs without metrics make coverage incomplete; they are not treated as
zero. Skipped valid reports may contribute metrics; render-only work is not a
paid scan.

## Warnings

- **Candidate count alone is not a model-quality metric.** Different models
  emit different volumes; human review and theme coverage matter more.
- **Contingent repair/correction requests are excluded from preflight
  estimates.** Actual cost may exceed the plan when JSON envelope repair or
  section-local evidence correction runs (`repair_request_count`).

## Planner / runner consistency

Both scripts share `bench_common.py` for configuration parsing, validation,
slugging, command construction, resume helpers, and manifests. They do **not**
share default matrices. The planner does not contact providers.

See also [docs/rfc5905_provider_compare.md](../docs/rfc5905_provider_compare.md)
(single-doc bake-off) and [docs/backlog.md](../docs/backlog.md).
