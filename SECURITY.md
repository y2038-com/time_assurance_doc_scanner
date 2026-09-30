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
independently enabled third-party HTTP logging. Independently enabled
httpx/httpcore debug logging may expose complete URLs.

## Remote URL fetch (SSRF and provenance)

TADS may fetch public HTTP(S) URLs for ingest (`fetch` / `convert` / `plan` /
`scan`). Other schemes are rejected. By default it blocks destinations that
resolve to localhost, private, link-local, or other non-public addresses. Each
redirect target (HTTP status 301, 302, 303, 307, or 308) is validated before
the next request. Other 3xx responses, including 304, are not followed.
HTTPS-to-HTTP redirects are refused hop by hop. `--allow-private-url` only
opts into private-address fetches; it does not allow an HTTPS downgrade.

`source_uri` is the sanitized URL of the first HTTP request TADS issues after
any document rewrite or fallback. `retrieved_uri` is the sanitized URL of the
final successful response when that locator differs from `source_uri`. Both
fields omit user information, query strings, and fragments.

Trusted local CLI users may opt in with `--allow-private-url`. Hosted
deployments should keep private URL access disabled and should also enforce
infrastructure-level outbound network controls. Application-level DNS/IP checks
reduce SSRF risk but do not pin the validated address to the TCP/TLS
connection. DNS rebinding between validation and connect remains a residual
risk. Stronger connection pinning may be needed in hosted environments.

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

## Prompt injection and model influence

TADS submits untrusted document text to the configured LLM. Titles, headings,
quotations, tables, comments, and embedded instructions are data, not scanner
policy. A document may try to suppress findings, invent evidence, or dictate
JSON. TADS cannot prevent a model from following those instructions.

Scanner policy, output schema, task instructions, and trusted corpus-profile
guidance are sent on the highest-priority instruction channel each provider
supports (OpenAI/Ollama `system` role, Anthropic `system`, Gemini
`systemInstruction`). Document identifiers, titles, section labels, summaries,
body text, and prior model output used for repair are sent only in the user
message as an untrusted-data record. TADS does not interpolate document text
into trusted system instructions and does not delete or rewrite suspicious
passages.

Structured output must be one complete JSON object with a `findings` array
after think-block and single-fence cleanup. Mixed prose, list-root JSON,
duplicate object keys, or ambiguous extra objects are not accepted by fishing
for the longest candidate. One protected repair attempt may run for invalid
JSON envelopes; the broken payload stays untrusted. Repair is not used to
fix malformed finding items. If envelope repair fails, or if any finding item
is missing required fields, uses the wrong types, carries extra keys, or
fails the canonical enum contract, the scan aborts and does not write a
findings report. A malformed item list is not rewritten as an empty
`findings` array. A literal `{"findings": []}` is a valid zero-finding
result. A section-aware failure aborts the whole scan rather than omitting
that section. Optional raw-on-error persistence is **disabled by default**.
When `--save-raw-on-error` is set, TADS may write a `.raw.txt` file beside the
JSON report. That file is for troubleshooting only. It may contain the complete
model output and document excerpts, is capped at 256 KiB of UTF-8, is sensitive
as a whole, and is not field-sanitized: TADS does not attempt secret or URL
sanitation inside it. Canonical JSON and Markdown reports are separate from
this opt-in file and continue to carry validated finding text and evidence.

Schema-valid output can still be incomplete, misleading, or fabricated.
Model-supplied `disposition`, `validation_status`, `source_verified`,
`horizon_validation`, and other TADS-owned or undocumented item fields are
rejected rather than applied. Human review, source verification, and
deterministic checks remain separate safeguards. Prompt injection here is
not arbitrary code execution: TADS does not give the model tools, shell,
filesystem, or fetch capabilities.

Markdown reports are a projection of that JSON. Untrusted document, model,
and reviewer strings are escaped or shown in literal fences so they cannot
form headings, links, images, or HTML. Canonical JSON is unchanged. Some
Markdown viewers still differ in autolink and HTML behavior; TADS does not
emit clickable links.

Use a trusted or local model for sensitive documents. Hosted or high-risk
deployments should add provider, process, logging, and access controls
appropriate to their threat model.

## Diagnostic artifacts and provider errors

Canonical JSON and Markdown reports intentionally contain findings, evidence
quotes, and document-derived text. Treat them as sensitive when the source was.

Ordinary CLI, progress, and provider-error output is allowlisted. Provider
errors may include the provider name, HTTP status or a safe error category,
retryable versus non-retryable classification, attempt count, and an exception
type category such as connect timeout or read timeout. They do not include
provider response bodies, success JSON, model output, prompt or document
content, request or response headers, API keys, Authorization values, raw
`str(exc)` / `repr(exc)` from third-party libraries, or complete provider URLs.

Provider diagnostic URLs are reduced to scheme, host, and path. Userinfo, the
entire query string, and fragments are dropped. That helper is separate from
document-provenance URL sanitation even when the drop rules match. Token
redaction on controlled strings is defense in depth, not a reason to emit
provider bodies. TADS does not claim perfect secret detection.

`--save-raw-on-error` is opt-in, written beside the JSON report, and disabled
by default. The raw file is installed from a same-directory temporary regular
file with restrictive permissions, then `os.replace`. On POSIX the final mode
is `0600`, including when replacing a previous `0644` or `0666` file. On
Windows, TADS uses best-effort owner-only semantics available through the
standard library; POSIX mode bits are not Windows ACLs. If the `.raw.txt`
destination already exists as a symlink, TADS refuses to save there and does
not follow it. Failure to persist the raw file is reported as a controlled
notice (`Raw diagnostic output could not be saved.`) and does not replace the
original parse or validation error. JSON and Markdown reports are not rewritten
on that path, and this change does not alter JSON or Markdown file permissions.

JSON and Markdown reports are **separate writes**, not an atomic pair. A
Markdown write can fail after JSON has already been written.

TADS does not dump prompts. Independently enabled httpx/httpcore DEBUG logging
and shell history are outside TADS-owned output and may still expose complete
URLs, headers, or bodies.

Retry counts, timeout defaults, and converter limits are unchanged in this
release. Residual risks that are not addressed here include: provider retries
multiplying total wall-clock time; converters that are resource-bounded but
have no independent CPU deadline; provider response loading that is not
byte-capped; DNS rebinding between validation and connect; and third-party
HTTP DEBUG logs.

LLM provider errors include only allowlisted classification (provider name,
status or category, retryable vs not, attempts, timeout/network class). TADS
does not copy provider response-body excerpts into ordinary errors. This is
separate from remote-document URL sanitation: ingest URL sanitation protects
provenance and fetch messages; provider diagnostic sanitation reduces endpoint
URLs to scheme/host/path and drops userinfo, query, and fragment. Neither is
perfect secret detection. Hosted deployments should still treat operational
logs as potentially sensitive.
