# HasanAra

HasanAra is a citation-first HasanAbi VOD archive. It searches timestamped transcripts, groups evidence by episode, and provides topic timelines, opinion-history revisions, related episodes, quoted moments, and portable mention exports.

## Current status

- **Shipped:** React 19 frontend, FastAPI API, PostgreSQL source of truth, Redis DTO caches, optional OpenSearch acceleration with PostgreSQL fallback, durable ingestion jobs, scoped API keys, pseudonymous analytics, and archive intelligence.
- **Disabled:** billing and PWA/offline installation. There are no Stripe routes or service workers.
- **Planned:** billing may be reconsidered as a future product decision; it has no current contract.
- **Historical:** documents marked historical describe earlier implementations and are not operational guidance.

See [documentation status](docs/STATUS.md) for the authoritative document map.

## Supported development environment

- Python 3.11
- Node.js 20
- Docker with Compose

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm --prefix frontend ci
npm --prefix e2e ci
TEST_POSTGRES_PORT=55433 mise exec node@20 -- env PYTHON_BIN="$PWD/.venv/bin/python" make verify
```

For a fresh local database, copy `.env.example`, then run
`ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION=true docker compose up --build`. The
frontend defaults to `http://localhost:5173`; the API defaults to
`http://localhost:8000`. This opt-in is for controlled local bootstrap only,
not a production rolling upgrade.

## Architecture and contracts

- [Architecture](docs/development/architecture.md)
- [Testing](docs/development/testing.md)
- [API reference](docs/api-reference.md) and generated [OpenAPI](docs/api/openapi.json)
- [Access matrix](docs/access-matrix.md)
- [Deployment matrix](docs/deployment/README.md)
- [Private-beta deployment and evidence](docs/deployment/private-beta.md)
- [Moderated testing and beta protocol](docs/user-testing/private-beta.md)
- [Operations](docs/operations/production-readiness.md)
- [Migrations](docs/MIGRATIONS.md)
- [Accessibility](docs/ACCESSIBILITY.md) and [design system](docs/DESIGN_SYSTEM.md)

`/api` is v1-stable: changes are additive, deprecations remain for at least two releases, and breaking changes require `/api/v2`.
