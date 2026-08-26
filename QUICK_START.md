# Quick start — Time Assurance Doc Scanner (`tads`)

This guide gets you from clone → first scan, then shows how to switch LLM providers.
Copy secrets only into a local `.env` (never commit it). See `.env.example` for templates.

## 1. Install

```bash
cd /path/to/doc_scanner
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` for the provider you want (below). Defaults favor **Ollama Cloud**.

## 2. Workspace

| Folder | Purpose |
|--------|---------|
| `inputs/` | Source docs (`tads fetch` writes here by default) |
| `outputs/` | Scan reports (`.json` + `.md`) |

Both are gitignored except short READMEs.

## 3. Minimal workflow

```bash
tads fetch RFC5905
tads plan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections
tads scan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections --overwrite -y
# Review candidates in outputs/RFC5905.json (and .md):
# - disposition: accepted = human-confirmed / validated finding
# - scope_relevance: core / supporting / incidental / out_of_scope (JSON keeps all)
# then:
tads render outputs/RFC5905.json
```

| Flag | Where | Meaning |
|------|--------|---------|
| `--max-sections N` | `plan` / `scan` | Cap body sections (great for smoke tests) |
| `--force-sections` | `plan` / `scan` | Force section-aware mode |
| `--overwrite` / `-f` | `fetch` / `convert` / `scan` | Overwrite existing outputs without prompting |
| `-y` / `--yes` | `scan` | Skip “Proceed with LLM scan?” |
| `--save-text PATH` | `plan` / `scan` / `convert` | Persist converted text |

`plan` accepts `--overwrite` / `-y` / `-o` so the same flags can be shared with `scan` scripts; it ignores them (plan does not write reports).

Offline plumbing check (no API key):

```bash
tads scan inputs/RFC5905.txt --doc-id RFC5905 --provider mock --overwrite -y
```

## 4. Environment knobs

| Variable | Role |
|----------|------|
| `TADS_LLM_PROVIDER` | `ollama` (default), `openai`, `anthropic`, `gemini`, `mock` |
| `TADS_MODEL` | Model id for that provider |
| `TADS_PROVIDER` | Alias for `TADS_LLM_PROVIDER` |

Provider-specific credentials are listed in each section below.

---

## 5. Providers — settings and lessons learned

Smoke tests below used a capped RFC 5905 slice (`--max-sections 2 --force-sections`). Finding counts vary by model; human review remains authoritative.

### Ollama Cloud (default, free-tier friendly)

```bash
TADS_LLM_PROVIDER=ollama
# OLLAMA_HOST unset → https://ollama.com
OLLAMA_API_KEY=...
# Default cloud model when TADS_MODEL unset:
# TADS_MODEL=gpt-oss:120b
```

| Item | Notes |
|------|--------|
| **Tested** | `gpt-oss:120b` on free tier (whole-document RFC 5905) |
| **Avoid as default** | `deepseek-v4-flash:cloud` — often needs a paid subscription |
| **Models catalog** | https://ollama.com/search?c=cloud |
| **Parser note** | Some Cloud models return multi-section `section_id` / `section_title` as JSON arrays; tads coerces them to strings |

### Ollama local (GPU)

```bash
TADS_LLM_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
TADS_MODEL=llama3.2:3b
# or: TADS_MODEL=llama3.1:8b
```

| Item | Notes |
|------|--------|
| **Tested** | `llama3.2:3b` and `llama3.1:8b` on RTX 4060 Laptop (8 GB), official Linux install |
| **Verify GPU** | While running: `ollama ps` → expect `100% GPU` (not `100% CPU`) |
| **Install tip** | Prefer the [official installer](https://ollama.com/download); the Ubuntu **Snap** package often falls back to CPU even when `nvidia-smi` works |
| **Context** | Default local context is often ~4k tokens. Whole-document RFC 5905 (~60k) is truncated → often **0 findings**. Prefer `--force-sections --max-sections N` locally, or raise `num_ctx` via an Ollama Modelfile / `/set parameter num_ctx` |
| **Thermals** | Laptop dGPUs can get very hot under local LLM load; prefer Cloud/API providers if the machine overheats |
| **Quality** | Small local models are fine for plumbing; Cloud/API models are better for real reviews |

### Google Gemini

```bash
TADS_LLM_PROVIDER=gemini
GOOGLE_API_KEY=...          # or GEMINI_API_KEY
TADS_MODEL=gemini-3.6-flash
```

| Item | Notes |
|------|--------|
| **Tested** | `gemini-3.6-flash` (whole-document RFC 5905) |
| **Recommended setup** | Create/select a GCP project → **import it into [AI Studio](https://aistudio.google.com/)** → create the API key **in AI Studio for that project**. Prepaid credits alone are not enough. |
| **Enable the API** | New projects must enable **Generative Language API** / Gemini API or you get `403 SERVICE_DISABLED`. Console: APIs & Services → Library, or the activation URL in the error. Wait a minute after enabling. |
| **Billing** | Depleted prepay credits → `429 RESOURCE_EXHAUSTED` (“prepayment credits are depleted”). Top up at [AI Studio projects](https://ai.studio/projects). |
| **Blocked for many new keys** | `gemini-2.5-flash` returns 404 (“no longer available to new users”) |
| **Also works** | `gemini-3.5-flash`, `gemini-flash-latest` |
| **Default in tads** | `gemini-3.6-flash` |

### OpenAI

```bash
TADS_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
TADS_MODEL=gpt-4.1-mini
```

| Item | Notes |
|------|--------|
| **Tested** | `gpt-4.1-mini` |
| **Keys** | https://platform.openai.com/api-keys |
| **Billing** | New keys often return **429 `insufficient_quota`** until a billing account / credits exist |
| **Misleading error** | tads may mention TLS/timeouts after retries; for 429, check OpenAI billing/usage first |
| **VPN / connect hang** | Connect/TLS fails fast (~10s, 1 attempt by default). Override with `TADS_HTTP_CONNECT_TIMEOUT` / `TADS_HTTP_CONNECT_RETRIES` |

### Anthropic (Claude)

```bash
TADS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
TADS_MODEL=claude-sonnet-4-5
```

| Item | Notes |
|------|--------|
| **Tested** | `claude-sonnet-4-5` |
| **Keys** | https://console.anthropic.com/settings/keys |
| **Cheaper option** | `claude-haiku-4-5` (in pricing table; not yet smoke-tested here) |

### Mock

```bash
tads scan … --provider mock --overwrite -y
```

No key required; deterministic offline findings for CI / plumbing.

---

## 6. Cross-provider comparison runs

Use the same document and analysis mode, and put provider/model in `-o`. Sanitize model ids for filenames (`:` → `-`). Append a **run tag** (e.g. `__pf0.5.0`) when you need to keep an earlier bake-off.

```bash
export DOC=inputs/RFC5905.txt
export ID=RFC5905

tads plan "$DOC" --doc-id "$ID" --provider ollama --model gpt-oss:120b \
  --overwrite -y -o outputs/RFC5905__ollama__gpt-oss-120b__pf0.5.0
tads scan "$DOC" --doc-id "$ID" --provider ollama --model gpt-oss:120b \
  --overwrite -y -o outputs/RFC5905__ollama__gpt-oss-120b__pf0.5.0
```

`plan` accepts `--overwrite` / `-y` / `-o` and ignores them so scripts can share flags with `scan`. After `scan`, the suggested render command is a **single line** you can copy/paste.

Full four-provider commands, theme tables, and pf0.5.0 vs historical notes: [docs/rfc5905_provider_compare.md](docs/rfc5905_provider_compare.md).

## 7. Ingest tips

- IETF: prefer `tads fetch RFC5905` or `https://www.rfc-editor.org/rfc/rfcNNNN.txt`
- W3C: `tads fetch hr-time-3` (latest TR HTML → text); see [docs/fetch_tier2_plan.md](docs/fetch_tier2_plan.md)
- ECMA: `tads fetch ECMA-404` (JSON negative control); `tads fetch ECMA-262` (large — use scan caps)
- OASIS: `tads fetch OpenFormula` (ODF v1.4 Part 4 OS PDF); `OpenFormula-1.3` for v1.3
- NIST: `tads fetch "SP 800-57 Part 1 Rev. 5"` (or `SP-800-57pt1r5`); also `FIPS-140-3`
- `tools.ietf.org` / datatracker PDF URLs are rewritten to the RFC Editor text mirror (those hosts often redirect to login)
- `tads convert` / `plan` / `scan` accept local `.txt`, `.docx`, `.pdf`, `.html`, `.zip` / `.tgz`, or `http(s)` URLs
- Other non-fetch corpora (ETSI/3GPP/ITU/IEEE/ISO): local file or `tads convert <url>`
- Large specs (e.g. 3GPP, ECMA-262): start with `--max-sections` / `--max-input-tokens`

## 8. More docs

| Doc | Purpose |
|-----|---------|
| [README.md](README.md) | Project overview |
| [docs/phase1.md](docs/phase1.md) | MVP commands and in-scope backlog |
| [docs/phase2.md](docs/phase2.md) | Corpus adapters |
| [docs/backlog.md](docs/backlog.md) | Parked ideas (progress UI, counterevidence pass, Markdown provenance, …) |
| [docs/rfc5905_provider_compare.md](docs/rfc5905_provider_compare.md) | RFC 5905 multi-provider bake-off (incl. pf0.5.0) |
| `.env.example` | Copy-paste provider blocks |
