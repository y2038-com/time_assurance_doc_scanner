# Contributing

Thanks for your interest in the Time Assurance Documentation Scanner (`tads`).

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # only if you will call a real LLM
tads version
pytest
```

New-user workflow (mock scan, providers, privacy): **[QUICK_START.md](QUICK_START.md)**.

## Before you open a PR

- Run `pytest` and keep changes focused.
- Do **not** commit secrets or workspace junk: `.env`, API keys, or contents of
  `inputs/` / `outputs/` (only the committed READMEs belong there).
- Prefer small PRs: code, docs, or tests — avoid mixing large refactors with
  unrelated doc edits.
- Match existing style (Python 3.11+, typed public schemas, Typer CLI).

## What to work on

- Bugs and UX friction in the CLI / docs are always welcome.
- Larger ideas live in [docs/backlog.md](docs/backlog.md) (parked; not a
  commitment to implement).
- Design context: [docs/architecture.md](docs/architecture.md) (current),
  [docs/schemas.md](docs/schemas.md).

## Security

Report vulnerabilities privately — see **[SECURITY.md](SECURITY.md)**. Do not
file public issues for exploitable bugs.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License, Version 2.0](LICENSE).
