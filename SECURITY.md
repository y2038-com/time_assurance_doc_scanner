# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest tagged release | Yes |
| `main` | Yes, as the development branch |
| Older tagged releases | No |

Security fixes are developed on `main`. A fix for a supported release may appear
as a new tagged release rather than a change to an existing artifact.

Reproducing against the latest tagged release or `main` is helpful. Do not delay
a report if you cannot do that.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Use one of these private channels, in order:

1. [GitHub private vulnerability reporting](https://github.com/y2038-com/time_assurance_doc_scanner/security/advisories/new)
   (requires a GitHub sign-in)
2. Email fallback: [security@y2038.com](mailto:security@y2038.com)

Use email when you cannot use GitHub private reporting.

Do not send API keys, credentials, private documents, or other sensitive
material unless it is necessary and arrangements have been made.

## What to include

Useful reports typically include:

- Affected release, commit, or branch
- Description of the issue and expected security impact
- Reproduction steps or a minimal proof of concept
- Relevant configuration and platform details
- Whether the issue is already public
- Suggested mitigation, if known

Do not delay a report merely because every item is unavailable.

## What is in scope

Examples of issues we want reported privately:

- Credential or secret leakage through reports, provenance, logs, or errors
- Unsafe local-path or output-path handling
- Unsafe remote fetch, redirect, conversion, or SSRF behavior
- Archive, document-conversion, or resource-exhaustion weaknesses
- Prompt injection or model-output handling that crosses a security boundary
  (for example writing outside intended output paths)
- Dependency vulnerabilities with a realistic exploit path in this project

Ordinary model mistakes (incorrect findings, missed issues, or poor wording)
are not security vulnerabilities. See [What is out of scope](#what-is-out-of-scope).

## What is out of scope

Please use normal GitHub issues (not a security advisory) for:

- LLM **false positives / false negatives** or disagreement with a finding
- Model quality, cost, or provider availability
- Missing features, documentation typos, or general product feedback
- Expected transmission of document text to the configured LLM provider.
  That is intended BYOLLM behavior. See [docs/privacy.md](docs/privacy.md).

## Disclosure and response

Maintainers will aim to:

- Acknowledge reports promptly
- Assess severity and affected versions
- Coordinate remediation and disclosure
- Credit reporters when requested and appropriate

Please allow reasonable time for investigation and remediation before public
disclosure. This project does not publish a response SLA, embargo period, bounty,
or legal safe-harbor statement.

## Operational notes

- Never commit `.env`, API keys, or private documents under `inputs/` /
  `outputs/` (those directories are gitignored except short READMEs).
- Treat scan reports as potentially sensitive if the source document was.
- Enabling debug logging in HTTP client or transport libraries may expose
  complete request URLs, including sensitive query parameters.

TADS-owned provenance, notes, errors, and reports remove URL user information,
query strings, and fragments. The complete URL is still used transiently for
validation and the HTTP request. TADS does not sanitize shell history or
independently enabled third-party HTTP logging.

## Remote URL fetch (SSRF and provenance)

TADS may fetch public HTTP(S) URLs for ingest (`fetch` / `convert` / `plan` /
`scan`). Other schemes are rejected. By default it blocks destinations that
resolve to localhost, private, link-local, or other non-public addresses, and it
validates each redirect target before following it.

Trusted local CLI users may opt in with `--allow-private-url`. Hosted
deployments should keep private URL access disabled and should also enforce
infrastructure-level outbound network controls. Application-level DNS/IP checks
reduce SSRF risk but do not fully prevent DNS rebinding; stronger connection
pinning may be needed in hosted environments.

For TADS-owned provenance, notes, errors, and reports, remote document URLs are
stored and displayed without user information, query strings, or fragments. The
complete URL remains available only while rewriting, validating, and performing
the request.

## Archive and document expansion

TADS limits compressed download and local payload size (`--max-download-mb`,
default 100 MiB) and uncompressed archive-member size
(`--max-archive-member-mb`, default 100 MiB). For ZIP members it also applies a
secondary expansion-ratio guard (`--max-archive-expansion-ratio`, default
200:1). Archives that exceed these limits are rejected before excessive memory
use. These controls apply to archive extraction. They do not automatically cover
every container or converter.

Current DOCX parsing and post-conversion text extraction may not have equivalent
expansion limits. For untrusted documents, especially in hosted deployments,
prefer process or container memory and CPU limits.

## Provider HTTP error sanitization

LLM provider error bodies are sanitized and length-limited (default 1000
characters) before being surfaced in exceptions. TADS redacts obvious tokens
(Bearer credentials, common API-key forms, `sk-` secrets) and avoids putting
raw prompts, document bodies, or request headers into provider error messages.
This is separate from remote-document URL sanitation: ingest URL sanitation
protects provenance and fetch messages; provider-error sanitation redacts and
truncates selected provider error content. Neither is perfect secret detection.
Hosted deployments should still treat operational logs as potentially sensitive.
