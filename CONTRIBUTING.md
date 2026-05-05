# Contributing

Thanks for considering a contribution.

## Workflow

1. Open an issue first for non-trivial changes — agreement on the approach is faster than rework.
2. Fork, branch, push, open a PR.
3. Sign your commits with `-s` (DCO sign-off) — see [Developer Certificate of Origin](https://developercertificate.org/).
4. CI must be green: `ruff check`, `ruff format --check`, `pytest`.

## Running locally

```bash
pip install -e ".[dev,notebook,judge-anthropic]"
ruff check src tests
ruff format --check src tests
pytest
```

## Adding a new AI agent signature

The simplest contribution: add a new entry to `src/ai_dev_effectiveness/data/agents.yaml`. See [`docs/agent-registry.md`](docs/agent-registry.md) for the schema and how to test detection.

## Adding a new language family

Edit `src/ai_dev_effectiveness/defaults.py`. Include a defensible per-LOC rate with a comment justifying it.

## Reporting issues

Use the templates under `.github/ISSUE_TEMPLATE/`.
