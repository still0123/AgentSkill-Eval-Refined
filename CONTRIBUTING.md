# Contributing

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install build
```

For Dashboard changes, use Node.js 20.19+ or 22.12+:

```bash
cd apps/web
npm ci
```

## Required checks

```bash
python -m ruff check .
python -m mypy apps packages
python -m pytest
python -m build --wheel

cd apps/web
npm run typecheck
npm run lint
npm test
npm run build
```

Changes to persisted contracts must preserve explicit schema versions and add migration or
compatibility tests. Changes to evidence collection must preserve deterministic redaction and must
not infer unavailable capabilities.

## Real-agent safety

Pull requests and CI must use Mock or Fake Process Agents. Do not run or commit a paid experiment as
part of routine validation. A real run requires explicit user authorization for Provider, model,
Run count and maximum budget. Commit only reviewed, redacted configurations and result artifacts;
never commit Provider keys, caches, raw HOME directories or unsafe stdout/stderr.

## Pull requests

- Keep one coherent objective per pull request.
- Explain evidence class (`simulated` or real), test coverage and claim limits.
- Update README and the relevant document when behavior or boundaries change.
- Do not rewrite immutable DatasetVersion or completed-run evidence.
