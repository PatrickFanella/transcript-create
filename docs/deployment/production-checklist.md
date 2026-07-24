# Production checklist

**Status:** shipped (2026-07-12).

1. Pass `make verify` with Python 3.11 and Node 20.
2. Complete every ordered, operator-evidenced gate in the [private-beta runbook](private-beta.md), including rotation attestation, external invite-gate proof, and separate-host PITR rehearsal.
3. Deploy only the frozen manifest's image digests with automatic updates absent.
4. Apply additive migrations before starting writers; use the isolated maintenance procedure for analytics/session or hash-only boundaries.
5. Backfill the search outbox and compare PostgreSQL/OpenSearch counts.
6. Verify CSP, cache headers, health, OAuth, search lag/fallback, leases/retries, retention, backup recency, and alerts.
7. Keep additive migrations compatible through the next release; roll forward after either maintenance boundary.
