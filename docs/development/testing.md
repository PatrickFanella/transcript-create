# Testing

**Status:** shipped and authoritative (2026-07-12).

The canonical gate is:

```bash
TEST_POSTGRES_PORT=55433 mise exec node@20 -- env PYTHON_BIN="$PWD/.venv/bin/python" make verify
```

It starts isolated PostgreSQL, Redis, and OpenSearch; applies all Alembic migrations; runs compile, Ruff, Black, isort, mypy baseline, pytest with coverage, Bandit, pip-audit, generated-contract drift, ESLint, Prettier, TypeScript, Vitest coverage, production build, bundle budgets, npm audit, documentation validation, and seeded Chromium smoke tests. Services and volumes are removed afterward.

Chromium runs on each change. Firefox, WebKit, Mobile Chrome, and Mobile Safari are the nightly and pre-release matrix. Never treat host runtime results from unsupported Python or Node versions as authoritative.
