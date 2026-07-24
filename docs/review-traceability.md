# Quality review traceability

**Status:** completed audit (2026-07-12). The original review files remain historical snapshots.

## Frontend review

- [x] Stored HTML injection replaced by plain snippets and React-node highlights.
- [x] Anonymous saved moments reopen locally and synchronize after confirmed persistence.
- [x] Every serialized search filter round-trips or is rejected.
- [x] Query, period, and video requests cancel stale work.
- [x] Player loader/readiness/seek state resets per video/start.
- [x] Authentication has loading/authenticated/anonymous/error states and resilient logout.
- [x] 403, 404, route errors, and missing-video recovery are tested.
- [x] Admin routes require the admin capability before loading their shell.
- [x] Dependency advisories and the single-command verification gate are blocking.
- [x] Routes are lazy, public downloads exclude admin, and bundle budgets pass.
- [x] Transcript playback updates are isolated, indexed, memoized, throttled, and content-visible.
- [x] TanStack Query owns shared freshness/retry/cancellation; suggestions use a small endpoint.
- [x] Five route hotspots were split by responsibility.
- [x] Mobile focus/Escape, landmarks, tabs/button groups, pressed states, targets, transitions, images, forms, reduced motion, and axe coverage are repaired.
- [x] Topic timelines, opinion revisions, related episodes, quoted moments, mention exports/queue, Timeline navigation, shareable Explore state, and operation feedback are shipped.
- [x] Seeded browser flows reflect current routes, envelopes, search fields, archive intelligence, and mobile behavior.
- [x] README, architecture, design, accessibility/PWA, and testing documentation match shipped behavior.

## Backend review

- [x] 1. Stored XSS boundary uses plain snippets and Unicode highlight offsets.
- [x] 2. Analytics uses a separate HMAC subject; credentials are scrubbed/rotated by rollout tooling; CSV is formula-safe.
- [x] 3. Private/credential-bearing responses default to `private, no-store`; public caching is allowlisted.
- [x] 4. Vocabulary mutations enforce authentication, ownership/admin policy, ID validation, and exact worker selection.
- [x] 5. Jobs have leases, heartbeats, attempts, bounded retries/concurrency, cancellation, recovery, and compare-and-set finalization.
- [x] 6. Redis stores versioned JSON DTOs with cold/warm parity and mutation invalidation.
- [x] 7. Python 3.11/Node 20 `make verify` is reproducible and blocking.
- [x] 8. The search outbox, tombstones, classified fallback, freshness state, reconciliation, and lag metrics are implemented.
- [x] 9. Analytics taxonomy, batch/property/body/depth limits, rate limits, bulk insert, rejection monitoring, and 90-day retention are implemented.
- [x] 10. Quota and duplicate submission are atomic under advisory locking and indexed identity/idempotency constraints.
- [x] Metrics use route templates; framework GZip, short transactions, rollback-before-retry, typed taxonomy, explicit unavailable states, centralized policy, real worker concurrency, repository boundaries, and split runtime dependencies are implemented.
- [x] Job history/cancel/retry/operator recovery/progress, API-key scopes, source deletion, search freshness, idempotency, billing retirement, and API stability are shipped.
- [x] Generated OpenAPI, access/deployment matrices, status metadata, and privacy/lease/cache/search/backup/incident runbooks resolve documentation discrepancies.

Production execution items—backup rehearsal, analytics scrub/session rotation, deployment, outbox backfill, and live metric checks—remain explicit release steps rather than repository findings.
