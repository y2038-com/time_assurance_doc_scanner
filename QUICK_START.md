# Quick start — Time Assurance Doc Scanner (`tads`)

Goal: **clone → install → first report in about 15 minutes.**

1. Install the CLI  
2. Run an **offline mock scan** (no API key)  
3. Optionally run a **real LLM scan** (Ollama Cloud by default)

Copy secrets only into a local `.env` (never commit it). Templates: [`.env.example`](.env.example).

**Privacy:** ephemeral processing ≠ “stays on your laptop.” Real providers receive document text. See [docs/privacy.md](docs/privacy.md).

---

## 1. Install

```bash
cd /path/to/doc_scanner
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # optional until you use a real provider
tads version
```

| Folder | Purpose |
|--------|---------|
| `inputs/` | Source docs (`tads fetch` writes here by default) |
| `outputs/` | Reports (`.json` + `.md`) |

Both are gitignored except short READMEs.

---

## 2. First success (offline mock — no API key)

Needs network **once** to download RFC 5905; the scan itself does not call a paid/cloud LLM.

```bash
tads fetch RFC5905 --overwrite
tads scan inputs/RFC5905.txt --doc-id RFC5905 --provider mock \
  --max-sections 2 --force-sections --overwrite -y
```

You should get:

- `outputs/RFC5905.json` — canonical report  
- `outputs/RFC5905.md` — human-readable **candidates for review**

Open the Markdown file. Mock findings are deterministic plumbing fixtures, not a real review of NTP.

Useful flags:

| Flag | Meaning |
|------|---------|
| `--max-sections N` | Cap body sections (keep first runs small/cheap) |
| `--force-sections` | Force section-aware mode |
| `--overwrite` / `-f` | Overwrite existing outputs without prompting |
| `-y` / `--yes` | Skip “Proceed with LLM scan?” |

`tads plan` (no LLM) shows coverage and cost estimates before a real scan:

```bash
tads plan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections
```

---

## 3. Reading and reviewing reports

- **Markdown:** primary candidates (core + supporting). Incidental is lower; `out_of_scope` is omitted from Markdown but kept in JSON. Headers may include Content SHA-256, Scanner, and Prompt framework versions.
- **JSON:** source of truth — all candidates, evidence, `scope_relevance`, dispositions, provenance.
- Edit `disposition` in JSON (`accepted` = human-confirmed / **validated finding**). Then:

```bash
tads render outputs/RFC5905.json -f
```

---

## 4. Real LLM scan (Ollama Cloud)

1. Get an API key from [ollama.com](https://ollama.com) (cloud).  
2. Put it in `.env`:

```bash
TADS_LLM_PROVIDER=ollama
# OLLAMA_HOST unset → https://ollama.com
OLLAMA_API_KEY=your_key_here
# Optional; default cloud model:
# TADS_MODEL=gpt-oss:120b
```

3. Run a **capped** scan (cheap smoke test):

```bash
tads plan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections
tads scan inputs/RFC5905.txt --doc-id RFC5905 --max-sections 2 --force-sections \
  --overwrite -y -o outputs/RFC5905__ollama__gpt-oss-120b
```

Finding counts vary by model; treat results as candidates. For whole-document runs, drop `--max-sections` / `--force-sections` and set `--max-cost-usd` if you want a hard spend cap.

---

## 5. Other providers

Set `TADS_LLM_PROVIDER` / `TADS_MODEL` (or pass `--provider` / `--model`). Alias: `TADS_PROVIDER`.

### Ollama local

```bash
TADS_LLM_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
TADS_MODEL=llama3.2:3b
```

Prefer the [official installer](https://ollama.com/download) (Snap builds often stay on CPU). Default local context is often ~4k tokens — **always use section caps** for RFCs, or raise `num_ctx`. Check `ollama ps` for GPU use.

### Google Gemini

```bash
TADS_LLM_PROVIDER=gemini
GOOGLE_API_KEY=...          # or GEMINI_API_KEY
TADS_MODEL=gemini-3.6-flash
```

Import a GCP project into [AI Studio](https://aistudio.google.com/), enable **Generative Language API**, create the key there, and keep prepaid credits topped up. Avoid `gemini-2.5-flash` for many new keys (404).

### OpenAI

```bash
TADS_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
TADS_MODEL=gpt-4.1-mini
```

Billing/credits required or you get `429 insufficient_quota`. VPN/TLS issues: see `TADS_HTTP_CONNECT_TIMEOUT` in `.env.example`.

### Anthropic

```bash
TADS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
TADS_MODEL=claude-sonnet-4-5
```

`claude-haiku-4-5` is in the pricing table but not smoke-tested here.

### Mock

```bash
tads scan … --provider mock --overwrite -y
```

---

## 6. Troubleshooting

| Symptom | What to try |
|---------|-------------|
| `tads: command not found` | Activate `.venv` and re-run `pip install -e ".[dev]"` |
| Fetch fails / login walls | Prefer `tads fetch RFC5905` or `rfc-editor.org` text URLs (tools.ietf.org PDFs often redirect) |
| Real scan returns 0 findings on local Ollama | Context too small — use `--max-sections` / `--force-sections` or raise `num_ctx` |
| OpenAI / Gemini 429 | Check billing / prepaid credits before blaming TLS |
| Gemini 403 `SERVICE_DISABLED` | Enable Generative Language API; wait a minute |
| Want a cheaper first LLM run | Keep `--max-sections 2 --force-sections` and `--max-cost-usd` |

---

## 7. Ingest tips (when you leave RFC 5905)

| Corpus | Example |
|--------|---------|
| IETF | `tads fetch RFC5905` |
| W3C | `tads fetch hr-time-3` |
| ECMA | `tads fetch ECMA-404` (small); `ECMA-262` (large — use caps) |
| OASIS | `tads fetch OpenFormula` |
| NIST | `tads fetch "SP 800-57 Part 1 Rev. 5"` |
| ETSI / 3GPP / ITU / IEEE / ISO | Local file or `tads convert <url>` (no curated fetch yet) |

`plan` / `scan` / `convert` also accept local `.txt`, `.docx`, `.pdf`, `.html`, archives, or `http(s)` URLs. Large specs: start with `--max-sections` or `--max-input-tokens`.

More fetch detail: [docs/fetch_tier2_plan.md](docs/fetch_tier2_plan.md).

---

## 8. Optional: cross-provider comparison

Same document and caps; put provider/model in `-o` (replace `:` in model ids with `-`). Append a run tag if you must keep an older bake-off:

```bash
tads scan inputs/RFC5905.txt --doc-id RFC5905 --provider openai --model gpt-4.1-mini \
  --max-sections 2 --force-sections --overwrite -y \
  -o outputs/RFC5905__openai__gpt-4.1-mini
```

Full bake-off notes: [docs/rfc5905_provider_compare.md](docs/rfc5905_provider_compare.md). Optional multi-doc harness: [scripts/README.md](scripts/README.md).

---

## 9. More docs

| Doc | Purpose |
|-----|---------|
| [README.md](README.md) | Overview, limitations, privacy |
| [docs/phase1.md](docs/phase1.md) | MVP commands and review workflow |
| [docs/phase2.md](docs/phase2.md) | Corpus adapters |
| [docs/privacy.md](docs/privacy.md) | Privacy defaults |
| [docs/schemas.md](docs/schemas.md) | Report / finding schemas |
| `.env.example` | Copy-paste provider blocks |
