# Production checklist

**Status:** shipped (2026-07-12).

1. Pass `make verify` with Python 3.11 and Node 20.
2. Back up PostgreSQL and Almaz mounts; restore-test both.
3. Apply additive migrations and deploy compatible API/frontend/worker images.
4. Rotate sessions when performing the analytics identity rollout.
5. Backfill the search outbox and compare PostgreSQL/OpenSearch counts.
6. Verify CSP, cache headers, health, lag, leases, event rejection, and errors.
7. Keep additive migrations compatible through the next release.
