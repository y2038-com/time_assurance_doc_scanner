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
default 100 MiB). Local files are rejected from `stat()` when already oversize
and are then read incrementally, stopping at the cap plus one byte so growth
after `stat()` cannot cause an unbounded allocation. Remote downloads abort
during streaming at the same cap.

Uncompressed archive-member size (`--max-archive-member-mb`, default 100 MiB)
and the ZIP expansion-ratio guard (`--max-archive-expansion-ratio`, default
200:1) apply to the chosen ZIP/TGZ member and to each DOCX package part.
DOCX packages also have a cumulative uncompressed cap (default 100 MiB) and a
non-directory part-count cap (default 4096). Every DOCX part is stream-drained
during preflight (bytes counted and discarded) before `python-docx` opens the
package again.

A converted-text cap (`--max-converted-chars`, default 20,000,000 Unicode
characters) applies to TXT, HTML, PDF, DOCX, and archive-extracted documents.
It is distinct from analysis-scope `--max-chars`. Oversize conversion fails
closed; TADS does not silently truncate ingest text. Rejected content is not
written through `--save-text` and is not sent to an LLM.

ZIP member-count enforcement runs after the standard library has parsed the
central directory; it does not prevent that initial allocation. TGZ counts
entries during header iteration rather than after building a complete member
list. Zero, negative, or non-finite ingest limit values are rejected.

These in-process checks do not bound every parser or native-library failure.
PyMuPDF may still spend CPU on a PDF with many low-text pages that stays under
the payload and converted-text caps. `python-docx` still parses an allowed
`document.xml` in memory after preflight. For untrusted documents, especially
in hosted deployments, prefer process or container memory and CPU limits.

## Provider HTTP error sanitization

LLM provider error bodies are sanitized and length-limited (default 1000
characters) before being surfaced in exceptions. TADS redacts obvious tokens
(Bearer credentials, common API-key forms, `sk-` secrets) and avoids putting
raw prompts, document bodies, or request headers into provider error messages.
This is separate from remote-document URL sanitation: ingest URL sanitation
protects provenance and fetch messages; provider-error sanitation redacts and
truncates selected provider error content. Neither is perfect secret detection.
Hosted deployments should still treat operational logs as potentially sensitive.
