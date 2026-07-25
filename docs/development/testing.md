# Testing

**Status:** shipped and authoritative (2026-07-12).

The canonical gate is:

```bash
TEST_POSTGRES_PORT=55433 mise exec node@20 -- env PYTHON_BIN="$PWD/.venv/bin/python" make verify
```

It starts isolated PostgreSQL, Redis, and OpenSearch; applies all Alembic migrations; runs compile, Ruff, Black, isort, mypy baseline, pytest with coverage, Bandit, pip-audit, generated-contract drift, ESLint, Prettier, TypeScript, Vitest coverage, production build, bundle budgets, npm audit, documentation validation, and seeded Chromium smoke tests. Services and volumes are removed afterward.

Chromium runs on each change. Firefox, WebKit, Mobile Chrome, and Mobile Safari are the nightly and pre-release matrix. Never treat host runtime results from unsupported Python or Node versions as authoritative.

Before the first RC tag, a trusted release operator must dispatch
`.gitea/workflows/release.yaml` on the protected release branch. That validation
run must pass this canonical gate, the complete seeded browser matrix, exact
registry-digest scans, and Cosign signature/attestation verification without
creating a release. RC and beta tags repeat those checks before creating a Gitea
prerelease. Image publication is guarded by an explicit aggregate of four browser
success markers because runner matrix dependency semantics alone are not
fail-closed. Automated checks are only the first release gate; restore, ingress,
moderated-testing, and private-beta evidence are tracked in the
[private-beta deployment runbook](../deployment/private-beta.md).
