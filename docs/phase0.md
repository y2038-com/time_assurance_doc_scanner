# Phase 0 deliverables and decisions

## Deliverables checklist

| Deliverable | Location |
|-------------|----------|
| Overall architecture | `docs/architecture.md` |
| Time assurance taxonomy | `docs/taxonomy.md` |
| Finding / output schemas | `src/tads/schemas/`, `docs/schemas.md` |
| Corpus abstraction | `src/tads/corpus/` |
| LLM abstraction | `src/tads/llm/` |
| Document / section model | `src/tads/parsing/` |
| Prompt framework | `src/tads/prompts/` |
| Cost model | `src/tads/cost/` |
| Privacy defaults | `src/tads/privacy/`, `docs/privacy.md` |
| Evaluation harness + seed shortlist | `src/tads/eval/`, `eval/corpus/` |
| CLI stub | `src/tads/cli.py` |

## Locked decisions (from PRD + design discussion)

1. Phase 0 + Phase 1 as the initial implementation path; later phases are extension points only.
2. Phase 1 DoD: CLI scans one IETF RFC/I-D → Markdown + JSON → human edits JSON dispositions.
3. Primary MVP users: security / time researchers.
4. Phase 1 recommendations: Level 1 direction only.
5. MVP corpus priority: IETF RFC / Internet-Draft.
6. Language: Python.
7. LLM targets: cloud Ollama, OpenAI, Anthropic, Gemini.
8. Analysis: whole-document when it fits context; otherwise section-aware; every section examined.
9. Finding fields include severity, confidence, evidence, validation status from the start.
10. Deterministic validation: scaffold + a couple of sample checkers in Phase 1; full suite in Phase 4.
11. License: Apache License, Version 2.0 (see `LICENSE`).
12. Cost: preflight estimate + max USD / max tokens caps.
13. Privacy: ephemeral by default; user-controlled artifacts only.

## Bootstrap evaluation shortlist

See `eval/corpus/README.md`. Proposed public RFCs:

1. **RFC 5905** — NTPv4 (era / Y2036)
2. **RFC 4330** — SNTPv4 (related time representation)
3. **RFC 3339** — Date and Time on the Internet
4. **RFC 5280** — X.509 PKI (validity horizons)
5. **RFC 3550** — RTP (32-bit timestamp wraparound)

Phase 0 ships the manifest, labeling guide, and harness. Gold labels are filled incrementally (starting empty / example stubs).

## Explicitly out of Phase 0

- Full LLM scan pipeline and provider SDK wiring (Phase 1)
- Interactive review TUI
- Multi-corpus adapters beyond IETF stubs
- Finding registry, MCP, portfolio analytics
- Hosted y2038.ai platform code
