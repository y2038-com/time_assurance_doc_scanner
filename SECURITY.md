# Security Policy

## Supported versions

This project is under active development. Security fixes are applied on the
default branch (`main`) of
[time_assurance_doc_scanner](https://github.com/y2038-com/time_assurance_doc_scanner).
Please test against the latest `main` before reporting.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Prefer one of these private channels:

1. **GitHub private vulnerability reporting** (preferred when enabled):  
   Repository → **Security** → **Advisories** → **Report a vulnerability**  
   https://github.com/y2038-com/time_assurance_doc_scanner/security/advisories/new
2. If private reporting is unavailable, contact the maintainers via a **private**
   GitHub channel (for example a draft security advisory on a fork, or a direct
   message to the repository owner) and wait for acknowledgment before any
   public discussion.

Include enough detail to reproduce the issue (affected version/commit, steps,
impact). We will aim to acknowledge reports promptly and coordinate disclosure.

## What is in scope

Examples of issues we want reported privately:

- Secret or credential leakage (logs, reports, error messages, committed files)
- Unsafe handling of local paths, downloads, archives, or URL fetch/convert
- Prompt-injection or output-handling bugs that could escalate beyond “bad
  finding text” (for example writing outside intended output paths)
- Dependency vulnerabilities with a realistic exploit path in this project

## What is out of scope

Please use normal issues or discussions (not a security advisory) for:

- LLM **false positives / false negatives** or disagreement with a finding
- Model quality, cost, or provider availability
- Missing features, documentation typos, or general product feedback
- “The scanner sent my document to my configured LLM provider” — that is
  expected BYOLLM behavior; see [docs/privacy.md](docs/privacy.md)

## Operational notes

- Never commit `.env`, API keys, or private documents under `inputs/` /
  `outputs/` (those directories are gitignored except short READMEs).
- Treat scan reports as potentially sensitive if the source document was.

## Remote URL fetch (SSRF controls)

TADS may fetch **public HTTP(S) URLs** for ingest (`fetch` / `convert` / `plan` /
`scan`). By default it **blocks** destinations that resolve to localhost, private,
link-local, or other non-public addresses, and it validates **each redirect
target** before following it.

Trusted local CLI users may opt in with `--allow-private-url`. Hosted
deployments should keep private URL access **disabled** and
should also enforce **infrastructure-level outbound network controls**. 
Application-level DNS/IP checks reduce SSRF risk but do not fully prevent DNS
rebinding; stronger connection pinning may be needed in hosted environments.

## Archive expansion limits

TADS limits both **compressed download/local payload size**
(`--max-download-mb`, default 100 MiB) and **uncompressed archive-member size**
(`--max-archive-member-mb`, default 100 MiB). For ZIP members it also applies a
secondary **expansion-ratio** guard (`--max-archive-expansion-ratio`, default
200:1). Archives that exceed these safety limits are rejected before excessive
memory use. Hosted deployments may choose stricter limits based on available
memory.
