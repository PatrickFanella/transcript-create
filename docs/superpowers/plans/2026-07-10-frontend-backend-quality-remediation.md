# HasanAra Frontend and Backend Quality Remediation Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Recommended path:
> dispatch a fresh subagent per bounded slice, review each result with
> `review-quality`, then continue. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Implement every finding and recommendation from
`docs/frontend-quality-review.md` and `docs/BACKEND_QUALITY_REVIEW.md` through
dependency-ordered, test-driven vertical slices.

**Architecture:** PostgreSQL remains authoritative, OpenSearch is a derived
index with PostgreSQL fallback, FastAPI/OpenAPI owns API contracts, and React
renders only structured data. Security and verification block production;
reliability and frontend contract repairs form the production-readiness
milestone; product intelligence additions follow as independent slices.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy Core, PostgreSQL/Alembic,
Redis, OpenSearch, React 19, TypeScript, Ky, Vite, Vitest, Playwright, Docker
Compose.

**Reviewed baseline:** `873a78b1cb63e69c84182c58e68be6a8f503c7b4`

---

## Execution Rules

- [ ] Keep each slice reviewable: one behavior test, minimal implementation,
  targeted verification, documentation, then a commit.
- [ ] Do not touch `.codex/`, `.tmp/`, or
  `HasAnAra-Frontend-Quality-Review.docx` without separate authorization.
- [ ] Preserve citation-backed chapters, transcript navigation,
  archive-intelligence ranking, and Almaz bind-mounted data.
- [ ] Update the traceability checklist at the end of every slice.
- [ ] Do not deploy until the Phase 1 security gate and Phase 2 verification
  gate pass.

## Phase 0 — Preserve the Review Baseline

- [x] Track both Markdown quality reviews and this implementation plan.
- [x] Capture the reviewed revision and baseline verification results.
- [x] Add a local ignored deepwork ledger under `.blacktower/deepwork/`.
- [x] Obtain oracle review of the plan and reconcile actionable corrections.

## Phase 1 — Close Critical Security Boundaries

### Slice 1.1: Safe search highlighting and CSP

**Primary files:** `app/search/segment_repository.py`, `app/schemas.py`,
`frontend/src/features/search/` and the four reviewed rendering sinks.

- [x] First deploy a backward-compatible safe frontend renderer that accepts
  optional ranges and parses only exact legacy `b`/`em`/`mark` markers as inert
  data; it must never use DOM HTML insertion.
- [x] Add hostile-snippet API and frontend regression tests first.
- [x] Then make `snippet` plain text and add half-open Unicode-code-point
  `highlights: [{start, end}]`; JavaScript converts with `Array.from` before
  slicing and defensively sorts/clamps/merges ranges.
- [x] Parse PostgreSQL/OpenSearch markers inside backend adapters; never expose
  backend-generated HTML.
- [x] Render highlights as React nodes and remove all four
  `dangerouslySetInnerHTML` calls.
- [x] Finally remove `unsafe-eval` and `unsafe-inline` from production CSP in
  FastAPI, Helm/ingress, and the separately served frontend nginx origin. Move
  first-party inline styles to classes/CSS variables and explicitly allow only
  the required same-origin, Google Font, and YouTube script/frame/image hosts.
- [x] Verify malformed, encoded, Unicode, overlapping, title, and transcript
  cases.

### Slice 1.2: Remove session credentials from analytics

**Primary files:** `app/routes/events.py`, `app/search/analytics.py`,
`app/routes/admin.py`, plus schema and Alembic migration.

- [x] Expand schema with nullable `analytics_subject_id CHAR(64)` plus indexed
  daily UTC aggregate `(day, type, count)` records; keep `session_token`
  nullable during the compatibility release.
- [x] Introduce `ha_analytics`: 32 random bytes encoded base64url, HttpOnly,
  `Secure` in production, `SameSite=Lax`, `Path=/`, one-year TTL. Persist only
  HMAC-SHA256 under a dedicated production-required
  `ANALYTICS_HMAC_SECRET`.
- [x] Keep nullable `user_id` only for authenticated product-operation events;
  daily aggregates contain no user or analytics subject.
- [ ] Deploy code that never writes, reads, or returns event session tokens,
  drain old pods, then transactionally scrub the column and delete all login
  sessions. Install a temporary database trigger/guard that nulls any legacy
  write before the next-release contract migration drops the column.
  Implementation and guarded rollout tooling are complete; production deploy,
  drain, scrub, and session rotation remain intentionally unexecuted.
- [x] Treat the post-scrub release as roll-forward-only; a downgrade may
  re-create only an empty nullable compatibility column and never credentials.
- [x] Generate CSV through the standard library and neutralize formula-leading
  cells.
- [x] Upsert daily counts before deleting raw events older than 90 days through
  a scheduled Compose/Kubernetes maintenance command; retain only
  non-identifying aggregates longer.

### Slice 1.3: Explicit cache privacy

**Primary files:** `app/middleware.py`, `app/routes/api_keys.py`, and route
contract tests.

- [x] Apply precedence after route handling: errors, non-GET, session/OAuth
  cookies, `Authorization`, `X-API-Key`, `Set-Cookie`, or downstream
  `no-store` always force exact `private, no-store`.
- [x] Allow public caching only for explicitly named safe anonymous GET
  operations; all other GETs default to `private, no-store`.
- [x] Merge `Vary: Cookie, Authorization, X-API-Key` on cacheable responses.
- [x] Add a cache-header matrix that fails when a new private route is unsafe.

### Slice 1.4: Dependency security

- [x] Upgrade vulnerable frontend runtime and build dependencies in bounded
  lockfile changes.
- [x] Clear reachable high/critical npm and Python advisories across the direct,
  constrained, and image-specific dependency manifests.
- [x] Make npm audit, pip-audit, Bandit, and container application-library
  scanning blocking; document expiring,
  evidence-backed exceptions only.

**Phase 1 exit:** hostile content cannot execute, analytics stores no login
credential, private responses cannot be publicly cached, and reachable
high/critical advisories are cleared.

## Phase 2 — Establish a Trustworthy Verification Gate

- [x] Standardize on Python 3.11 and Node >=20.19 <21 with pinned development
  dependencies, and align the frontend Docker image instead of using Node 24.
- [x] Repair pytest collection, focused failures, logger errors, and the skipped
  auth network-error test.
- [x] Converge Ruff, Black, isort, ESLint, and Prettier.
- [x] Make a mypy baseline blocking immediately and reduce it to zero by the
  end of Phase 4.
- [x] Add `make verify` covering backend tests/coverage/static/security checks,
  frontend tests/type/lint/format/build/budgets/audit, and seeded Chromium smoke.
- [x] Add isolated test Compose services with deterministic cleanup.
- [x] Expand CI triggers to tests, scripts, migrations, Compose/configuration,
  locks, and generated contracts; remove nonblocking required checks.
- [x] Replace stale billing/job browser specs with current archive workflows.

## Phase 3 — Repair Backend Authorization and Lifecycle Correctness

### Slice 3.1: Authorization and vocabularies

- [x] Centralize roles, capabilities, entitlements, and API-key scopes.
- [x] Authenticate vocabulary mutations, enforce owners, reserve global
  vocabularies for admins, validate IDs, and load exactly selected IDs.
- [x] Add role/capabilities to the existing `/auth/me` envelope.

### Slice 3.2: Durable jobs and atomic submission

- [x] Add owner, canonical source, idempotency, stage/progress, heartbeat,
  cancellation, attempts, lease, and failure-summary data.
- [x] Renew leases, compare-and-set completion, move remote work outside
  transactions, cap/back off retries, and expose `needs_attention`.
- [x] Wire `MAX_PARALLEL_JOBS` to worker concurrency.
- [x] Add history, cancel/retry, admin requeue/quarantine, and attempt timeline.
- [x] Enforce quota, duplicate detection, and insert atomically with advisory
  locking and indexed uniqueness; return the existing active duplicate.

### Slice 3.3: Redis and analytics ingestion

- [x] Cache versioned JSON DTOs only; remove `default=str`.
- [x] Avoid caching pending/errors and invalidate every derived key after
  writes, reprocessing, metadata changes, or deletion.
- [x] Define an allowlisted event taxonomy and enforce: 50 events/batch,
  32 properties/event, 8 KiB/event, 256 KiB/request, depth 2, and 120
  events/minute/subject or IP fallback.
- [x] Bulk-insert events and automatically delete raw data after 90 days.

### Slice 3.4: Operational correctness and maintainability

- [x] Normalize metrics to route templates.
- [x] Replace custom compression with framework GZip middleware.
- [x] Roll back before classified database retries.
- [x] Return unavailable states instead of masking database failures as empty.
- [x] Split archive intelligence by bounded query concern.
- [x] Separate API and worker dependency/image surfaces.

### Slice 3.5: API keys and deletion

- [x] Add `search:read`, `videos:read`, `exports:read`, `jobs:read`, and
  `jobs:write` scopes; admin scopes require admin issuance/auditing.
- [x] Migrate current keys to equivalent non-admin scopes.
- [x] Add owner/admin source deletion across PostgreSQL, caches, OpenSearch,
  exports, and future backup snapshots.

## Phase 4 — Make Search and API Contracts Reliable

- [x] Add a transactional search-index outbox with versions, tombstones,
  retries/dead letters, checkpoints, and lag metrics.
- [x] Repair indexer reprocessing/deletion reconciliation.
- [x] Fall back to PostgreSQL only for classified OpenSearch outages/timeouts.
- [x] Add `backend`, `degraded`, `indexed_at`, and `index_lag_seconds` to search
  responses and health/admin surfaces.
- [ ] Generate frontend types from FastAPI OpenAPI and fail CI on drift.
- [ ] Round-trip every search filter through UI, URL, saved storage, and API;
  reject unsupported values explicitly.
- [ ] Adopt TanStack Query and propagate AbortSignals into Ky.
- [ ] Add a lightweight suggestions endpoint.
- [ ] Declare current `/api` additive v1 stability with two-release
  deprecations and future breaking changes under `/api/v2`.

## Phase 5 — Repair Frontend Behavior, Accessibility, and Performance

- [ ] Make saved/favorites anonymous-capable and merge local records only after
  confirmed authenticated persistence.
- [ ] Model auth as loading/authenticated/anonymous/error and surface logout
  failures without clearing valid local state.
- [ ] Capability-gate admin routes and add tested 403 behavior.
- [ ] Add wildcard 404, route error boundaries, missing-video state, and
  accurate login content.
- [ ] Centralize YouTube loading and safely reset player state per video/start.
- [ ] Add Timeline navigation, URL-backed Explore state, and operation feedback.
- [ ] Fix mobile focus/Escape, landmarks, tabs, pressed states, 44 px targets,
  transitions, image dimensions, form metadata, reduced motion, and axe tests.
- [ ] Lazy-load routes, exclude admin code from public pages, and enforce
  150 KiB shell+initial and 100 KiB lazy-route gzip budgets.
- [ ] Isolate playback time, memoize/index transcript work, throttle scroll, and
  use content visibility.
- [ ] Split the five oversized routes into focused hooks/adapters/controllers/
  view models/presentational sections.

## Phase 6 — Complete Product Recommendations

- [ ] Add accessible topic-over-time data, visualization, table, shareable
  range, counts, and timestamped evidence.
- [ ] Add automatic opinion history with subject/claim/stance/summary,
  confidence >= 0.90, model/prompt version, time bucket, direct evidence,
  revision history, model-generated labels, and admin correction/retraction.
- [ ] Add explainable related episodes and most-quoted moments.
- [ ] Export every-mention collections as JSON, CSV, M3U/deep-link playlist,
  and in-app playback queue.
- [ ] Cover empty/loading/degraded/error/correction/keyboard/mobile/reduced-motion
  states for every new view.

## Phase 7 — Documentation, Retirement, and Release Hardening

- [ ] Remove stale Stripe dependencies/tests/pricing claims and retain billing
  only as a clearly planned possibility.
- [ ] Remove service-worker/cache-clearing runtime, obsolete PWA assets, and
  offline/install/background-sync claims.
- [ ] Align README, architecture, testing, design, accessibility, API, schema,
  migration, and deployment docs with shipped behavior.
- [ ] Publish route/capability/access, generated OpenAPI, deployment, lease,
  cache, search freshness, privacy/retention/deletion, restore, and incident
  documentation.
- [ ] Validate links, commands, route inventory, generated types, and OpenAPI
  drift in CI.

## Public Interface Checklist

- [ ] `SearchHit.snippet` is plain text; `highlights` contains Unicode offsets.
- [ ] Search responses expose backend/degradation/freshness metadata.
- [ ] `/auth/me` preserves `{user: ...}` and adds role/capabilities.
- [ ] Jobs add list, cancel, retry, admin attempts/requeue/quarantine endpoints
  and progress/lease/failure fields.
- [ ] Events use strict request schemas; API keys use scopes.
- [ ] Add topic timeline/opinion, related/quoted video, and mention export APIs.
- [ ] Use additive migrations first; scrub credentials immediately and defer
  destructive compatibility cleanup until dependents are deployed.

## Verification and Rollout

- [ ] Run focused security, backend concurrency/cache, search parity/fallback,
  frontend contract/a11y/performance, and seeded browser scenarios described
  in the source reviews.
- [ ] Run Chromium on every PR and the full desktop/mobile browser matrix
  nightly and before release.
- [ ] Back up and restore-verify PostgreSQL before migrations.
- [ ] Apply additive migrations, scrub credentials, and rotate sessions.
- [ ] Deploy API/frontend/worker while preserving Almaz bind mounts.
- [ ] Backfill/compare the outbox before enabling OpenSearch primary mode.
- [ ] Verify CSP, cache headers, health/lag/lease/event rejection/error metrics.
- [ ] Keep migrations backward-compatible for independent image rollback.
- [ ] Exception: after analytics credential scrub/session rotation, do not roll
  back to token-writing images; keep the temporary database guard and roll
  forward with a patched image.

## Traceability Checklist

| Review area | Implemented by | Status |
| --- | --- | --- |
| Stored HTML injection and highlight mismatch | Slice 1.1 | Complete |
| Session credential persistence/export and CSV injection | Slice 1.2 | Implementation complete; production scrub/session rotation pending |
| Public caching of private/secret responses | Slice 1.3 | Complete |
| Dependency advisories | Slice 1.4 | Complete; independently re-reviewed READY |
| Broken repository/frontend verification gates | Phase 2 | Complete; clean-state `make verify` passes |
| Vocabulary authorization/data flow | Slice 3.1 | Complete |
| Worker leases, retries, progress, recovery | Slice 3.2 | Complete |
| Quota/idempotency races | Slice 3.2 | Complete |
| Redis serialization/invalidation | Slice 3.3 | Complete |
| Event write amplification/taxonomy/retention | Slice 3.3 | Complete |
| Metrics/compression/transactions/retries/error masking | Slice 3.4 | Complete |
| Authorization model, repository size, runtime dependencies | Slices 3.1/3.4 | Complete |
| API-key scopes and source deletion | Slice 3.5 | Complete |
| OpenSearch sync/fallback/freshness | Phase 4 | Pending |
| API compatibility and generated contracts | Phase 4 | Pending |
| Anonymous saves, auth/admin/errors/player/filters/stale requests | Phases 4/5 | Pending |
| Accessibility and UI consistency findings | Phase 5 | Pending |
| Bundles, transcript rendering, data fetching, route hotspots | Phase 5 | Pending |
| Topic/opinion/related/quoted/mention-export features | Phase 6 | Pending |
| Billing/PWA retirement and documentation drift | Phase 7 | Pending |

## Final Acceptance

- [ ] Phases 1–5 meet the production-readiness milestone.
- [ ] Phase 6 product recommendations are shipped and browser-tested.
- [ ] Phase 7 documentation and release validation pass.
- [ ] `make verify` passes from a clean checkout.
- [ ] Both review traceability lists contain no unresolved finding.
