# scripts/

Optional research harnesses for the **12-document multi-corpus benchmark**. Not part of the core `tads` CLI — prefer `tads plan` / `tads scan` for everyday use.

| Script | Purpose |
|--------|---------|
| `plan_bench_costs.py` | Cost preflight across the bench doc list (no LLM calls) |
| `run_bench_scans.py` | Run OpenAI `gpt-4.1-mini` + Gemini `gemini-3.6-flash` scans |

Outputs land under the chosen `--outputs-dir` (default: gitignored `outputs/`; naming: `<doc>__<provider>__<model>`). Derived child names from `doc_id` and `--tag` are sanitized so they cannot escape that directory. Inputs must already exist under `inputs/` (via `tads fetch` / `tads convert` / local files).

```bash
# From repo root, with .venv active and provider keys in .env:
python scripts/plan_bench_costs.py
python scripts/run_bench_scans.py --dry-run
python scripts/run_bench_scans.py --skip-existing --max-cost-usd 5.00
```

See also [docs/rfc5905_provider_compare.md](../docs/rfc5905_provider_compare.md) (single-doc bake-off) and [docs/backlog.md](../docs/backlog.md) (collections / review orchestration — not implemented yet).
