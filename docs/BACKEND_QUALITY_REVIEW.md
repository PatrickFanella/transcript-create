# HasanAra Backend Quality Review

*Security, correctness, performance, scalability, maintainability, product gaps, and documentation consistency*

> Overall assessment: NOT PRODUCTION-READY WITHOUT FIXES The backend has a promising foundation, but three critical security boundary failures and several high-risk correctness and operational gaps should be resolved before production use.

Repository: /Users/onnwee/Projects/subcult/hasanara

Reviewed revision: local `main` at `873a78b`

Repository state: Nine commits ahead of origin/main at review time

Review scope: Backend code, workers, SQL schema, deployment configuration, CI, tests, and project documentation

Review date: July 10, 2026

Prepared by: OpenAI Codex

Note: The review itself made no source-code or service changes. This report is the only delivered artifact.

## Executive Summary

HasanAra's backend shows solid product ambition and several good engineering instincts: production settings fail closed, SQL is generally parameterized, job claiming uses SKIP LOCKED, archive administration has explicit role dependencies, and the project contains substantial test, migration, health-check, metrics, and backup scaffolding. The current implementation, however, does not yet provide a safe or dependable production boundary.

The most urgent risks are stored cross-site scripting in search results, persistence and export of raw session credentials, and public caching of authenticated or secret-bearing responses. These are followed by broken authorization and data-flow semantics in the vocabulary subsystem, duplicate-work risks in the worker lease model, cache corruption under Redis, a nonfunctional verification gate, and an incomplete OpenSearch synchronization design.

### Priority snapshot

| Severity | Count | Immediate meaning |
| --- | --- | --- |
| Critical | 3 | Security boundary can expose users, credentials, or privileged responses. |
| High | 7 | Correctness, reliability, or operational failures are likely under normal production conditions. |
| Medium | 7+ | Maintainability, observability, and scale risks will slow delivery or hide failures. |
| Product and docs | Multiple | Important workflow gaps and inaccurate contracts can mislead users and developers. |

### Recommended sequence

| Phase | Focus | Outcome |
| --- | --- | --- |
| 1 | Security boundary | Remove XSS paths, raw session-token persistence and export, and public caching of private responses. |
| 2 | Worker and cache correctness | Add durable leases, repair Redis serialization and invalidation, and make quota/idempotency enforcement atomic. |
| 3 | Verification gate | Restore deterministic test collection, formatting, linting, typing, and CI enforcement. |
| 4 | Search reliability | Implement authoritative OpenSearch synchronization, deletion handling, checkpoints, and explicit fallback behavior. |
| 5 | Product contract and documentation | Close operational UX gaps and align README, API docs, deployment guidance, and actual behavior. |

### Review boundaries

- This was not a documentation-only review; code paths, schema, deployment settings, CI, tests, and frontend sinks were traced where necessary.

- Frontend rendering was inspected only where it established backend-generated XSS exposure.

- No database services were started, so database-dependent test failures are reported separately from verified code failures.

- No React performance or general UI audit was performed.

## Critical Findings

### 1. Stored XSS in search-result rendering  **Severity: Critical**

Search snippets can contain attacker-controlled transcript or title text, are rendered as HTML, and are protected by a Content Security Policy that still permits unsafe inline script behavior.

**Why it matters**

- An authenticated user can ingest arbitrary YouTube content, making malicious transcript or metadata content a realistic input source.

- Search highlighting and raw fallback content cross from the database into dangerouslySetInnerHTML sinks without a reliable sanitization boundary.

- The permissive CSP reduces the browser's ability to contain any successful injection.

> Evidence: app/search/segment_repository.py:81; frontend/src/components/archive/SearchMomentsList.tsx:50; frontend/src/components/archive/TopicMentionCard.tsx:21; frontend/src/routes/TopicPage.tsx:191; frontend/src/components/video/PlainTranscriptTurns.tsx:89; app/middleware.py:32

**Practical remediation**

- Return plain text plus structured match offsets from the backend instead of HTML fragments.

- If HTML highlighting remains, allow only the mark element and sanitize at the rendering boundary with a maintained sanitizer such as DOMPurify.

- Remove unsafe-inline and unsafe-eval from the production CSP and introduce nonces or hashes where inline code is unavoidable.

- Add regression tests using script tags, event-handler attributes, malformed markup, and encoded payloads in titles and transcripts.

### 2. Raw session credentials are stored and exportable  **Severity: Critical**

The analytics/event pipeline records the tc_session cookie value, persists it as session_token, and exposes it through administration and CSV export paths.

**Why it matters**

- A database, analytics, admin-account, log, or exported-file compromise can become an account takeover path while sessions remain valid.

- CSV exports add a spreadsheet-formula injection risk if cells beginning with =, +, -, or @ are not neutralized.

- The design violates least-privilege and purpose-limitation expectations for analytics data.

> Evidence: app/routes/events.py:35; app/search/analytics.py:93; sql/schema.sql:236; app/routes/admin.py:69

**Practical remediation**

- Stop collecting raw session credentials immediately; use a separate random analytics identifier or a one-way keyed HMAC when correlation is truly necessary.

- Remove session_token from admin APIs and exports, scrub historical values, and rotate affected sessions.

- Generate CSV with the standard csv module and neutralize formula-leading cells.

- Document the analytics data model, retention period, deletion process, and access controls.

### 3. Authenticated and secret-bearing responses can be publicly cached  **Severity: Critical**

The cache-control middleware defaults responses to public caching and identifies private routes with a narrow path heuristic that misses API-key and several user-specific endpoints.

**Why it matters**

- A one-time plaintext API key response can be stored by shared browsers, proxies, gateways, or CDNs.

- User favorites, saved searches, mutations, and other authenticated responses can receive unsafe caching directives.

- Path-name matching is brittle and will regress as new authenticated routes are added.

> Evidence: app/middleware.py:220; app/routes/api_keys.py:90

**Practical remediation**

- Default authenticated requests, all non-GET responses, and every credential-bearing response to Cache-Control: no-store, private.

- Express cache policy explicitly at route or response-class level instead of inferring privacy from URL substrings.

- Add contract tests covering API-key creation, profile and preference endpoints, saved searches, favorites, and mutations.

## High-Risk Findings

### 4. Vocabulary authorization and job semantics are inconsistent  **Severity: High**

Vocabulary routes lack ownership enforcement, while jobs store owner_user_id and vocabulary_ids that the worker does not consume consistently.

**Why it matters**

- Any caller can create global vocabulary data or delete vocabulary records without a dependable ownership boundary.

- Users can select vocabularies for a job, but the native worker reads user_id and ignores the exact vocabulary_ids, so the resulting transcription behavior does not match the submitted job.

- Global and user-scoped vocabulary concepts are not represented by a coherent authorization model.

> Evidence: app/routes/vocabularies.py:15; app/routes/jobs.py:252; worker/native_pipeline.py:292

**Practical remediation**

- Deliver authorization and data-flow correctness as one vertical slice: enforce authentication, ownership, and admin-only global vocabulary management.

- Validate every submitted vocabulary ID and load exactly those IDs during processing.

- Add end-to-end tests proving isolation between users and confirming that selected vocabularies influence the worker.

### 5. Long-running transcriptions can be processed more than once  **Severity: High**

A job is requeued after 900 seconds based on updated_at, but the transcription loop does not reliably renew that timestamp or maintain an explicit worker lease.

**Why it matters**

- Long files can exceed the stale-job threshold and be claimed by another worker while the first worker is still active.

- Duplicate transcription consumes expensive compute and can race during persistence or external side effects.

- There is no durable attempt count, claim identity, lease expiration, or dead-letter state for diagnosis and recovery.

> Evidence: worker/loop.py:251; worker/native_pipeline.py:197

**Practical remediation**

- Add claimed_by, lease_expires_at, attempt_count, and last_error fields or an equivalent job-attempt table.

- Renew the lease during every processing stage and every long-running chunk.

- Use compare-and-set completion so only the active lease holder can finalize a job.

- Define retry ceilings, backoff, and a dead-letter or needs-attention state.

### 6. Redis cache serialization can corrupt result types  **Severity: High**

Production enables Redis, but the cache serializes unsupported values with default=str while repository methods can return database Row objects.

**Why it matters**

- A cold request can return structured Row values while a cache hit returns strings, producing environment-dependent behavior.

- A direct probe produced a fresh Row but a cached wire value resembling a stringified tuple: ["(1, 'hello')"].

- Empty or not-yet-ready results can be cached for an hour, and related caches are not invalidated after persistence.

> Evidence: docker-compose.prod.yml:40; app/cache.py:137; app/crud.py:98

**Practical remediation**

- Cache only explicit JSON DTOs with stable schemas; remove default=str so unsupported types fail loudly.

- Do not cache empty, pending, or transient-error results unless the negative-cache TTL is intentionally short.

- Invalidate transcript, segment, search, and aggregate keys when processing completes or data is reprocessed.

- Run cache parity tests that compare cold and warm response types and bodies.

### 7. The repository verification gate is not currently trustworthy  **Severity: High**

Static checks and test collection fail at the reviewed revision, while important CI checks are nonblocking or do not run for relevant changes.

**Why it matters**

- Ruff reported 349 errors, including nine undefined logging references; Black would reformat 41 files; isort reported 25 files; mypy reported 101 errors.

- Pytest collected 996 tests but stopped with six collection errors, including missing billing symbols, a circular YouTube import, and worker dataclass failures under local Python 3.14.

- A focused test run produced 102 passes, four genuine failures, and seven database setup errors because no database was started.

- The backend CI trigger omits tests/** and scripts/** even though scripts are checked, and mypy, security, audit, and coverage checks are allowed to fail.

> Evidence: .github/workflows/backend-ci.yml:3; .github/workflows/backend-ci.yml:60; pyproject.toml; requirements-dev.txt or equivalent development dependency definition (missing/incomplete contract)

**Practical remediation**

- Create one canonical local verification command, such as make verify, that reproduces required CI gates.

- Pin and document a supported development Python version; fix collection before interpreting the full test suite.

- Add CI triggers for tests, scripts, migrations, and configuration that influence backend behavior.

- Make security, dependency audit, required typing, and coverage gates blocking after establishing an achievable baseline.

- Ratchet mypy and lint debt by module instead of tolerating unbounded new failures.

### 8. OpenSearch indexing and fallback are incomplete  **Severity: High**

The OpenSearch indexer contains a runtime error, search returns 503 without a reliable fallback, and the synchronization model does not consistently delete or replace stale indexed segments.

**Why it matters**

- The indexer references undefined logging despite configuring a logger, preventing reliable execution.

- Documentation promises fallback behavior that the orchestrator does not implement consistently.

- Deleted or reprocessed PostgreSQL segments can remain searchable, producing stale and contradictory results.

- Indexer and service credential expectations are not consistently documented or configured.

> Evidence: scripts/opensearch_indexer.py:111; app/search/orchestrator.py:103; README.md and search documentation sections describing fallback

**Practical remediation**

- Choose and document an authoritative synchronization design: transactional outbox, change-data capture, or explicit indexed-version reconciliation.

- Track checkpoints, retries, index version, and deletion tombstones; expose freshness and lag metrics.

- Implement a tested PostgreSQL fallback or explicitly declare OpenSearch a hard dependency and fail health checks accordingly.

- Align credentials and TLS settings across local, staging, production, indexer, and application configurations.

### 9. Event ingestion permits unbounded write amplification  **Severity: High**

The event batch endpoint accepts an untyped collection with no explicit maximum and inserts events individually.

**Why it matters**

- A single request can trigger a large number of database operations and oversized analytics payload storage.

- Unexpected event names and arbitrarily shaped properties weaken downstream data quality.

- Application-level validation is not backed by proxy or server body-size constraints.

> Evidence: app/routes/events.py:49

**Practical remediation**

- Define strict Pydantic event and batch models with a small maximum batch size.

- Allowlist event names, constrain property keys and value types, and enforce a serialized payload byte limit.

- Use a bulk insert inside one transaction and configure request-body limits at the proxy and application layers.

- Rate-limit per user or analytics identifier and monitor rejected batches.

### 10. Quota and duplicate-job checks race under concurrency  **Severity: High**

Quota counting, duplicate lookup, and insertion happen as separate operations, allowing concurrent requests to exceed quotas or create duplicate work.

**Why it matters**

- Two requests can both observe capacity and absence, then both insert.

- Duplicate detection relies on a JSON expression without a supporting uniqueness constraint or direct indexed column.

- Races become more likely as API instances scale horizontally.

> Evidence: app/routes/jobs.py:73

**Practical remediation**

- Perform quota and duplicate enforcement in one transaction using a per-user advisory lock or a durable quota counter.

- Promote owner_user_id and canonical source identity to real indexed columns.

- Add an idempotency key or a unique constraint covering the canonicalized source and active processing state.

- Return the existing job for safe duplicate submissions when product semantics allow it.

## Additional Correctness, Performance, and Maintainability Issues

| Area | Issue | Practical improvement |
| --- | --- | --- |
| Metrics | Raw request paths are used as Prometheus endpoint labels, creating unbounded high-cardinality series. | Use route templates or named operations, normalize unknown routes, and exclude IDs and query data. |
| Compression | The custom middleware does not compress a 5 KB response because it depends on body_iterator behavior. | Replace it with the framework's tested GZipMiddleware and add content-type and size threshold tests. |
| Transactions | The worker performs expansion, caption fetching, network operations, and idle sleep while holding a database transaction. | Keep claim/update transactions short; perform remote work outside transactions and persist checkpoints explicitly. |
| Retry handling | Database retry logic retries after failures without a guaranteed rollback. | Rollback before retrying and limit retries to transient, classified exceptions. |
| Analytics | Search events are recorded as search_api while analytics queries count search. | Define a typed event taxonomy and centralize constants used by producers and reports. |
| Failure masking | Archive intelligence helpers catch OperationalError and ProgrammingError and return empty data. | Surface an unavailable/error state, emit structured logs and metrics, and reserve empty results for successful zero-row queries. |
| Authorization | users.role is displayed, but admin and paid access are determined through separate email and plan mechanisms. | Create one documented authorization model with centralized policy checks and auditable role/entitlement sources. |
| Configuration | MAX_PARALLEL_JOBS appears unused. | Wire it into worker concurrency or remove it to avoid a false operational control. |
| Architecture | The archive intelligence repository is roughly 2,900 lines and combines many responsibilities. | Split by bounded query concern and expose typed repository interfaces rather than one growing module. |
| Dependencies | The API and worker share a very large dependency set and image surface. | Separate runtime dependency groups and container images to reduce build time, attack surface, and upgrade coupling. |

> Evidence: app/main.py:269; app/middleware.py (compression implementation); worker/loop.py:205; app/crud.py:25; app/search/analytics.py:100; app/routes/analytics.py:112; app/archive/intelligence_repository.py:558; app/security.py:30

## Missing Features and Product Gaps

The backend exposes ingestion and processing capabilities, but important recovery, transparency, and lifecycle workflows are missing. These gaps will be felt directly by users and operators even after the security defects are fixed.

| Gap | User or project impact | Recommended slice |
| --- | --- | --- |
| User job history | Users cannot reliably inspect all submissions, current state, or prior outcomes. | Add a paginated, ownership-filtered job list with source, status, timestamps, progress, and failure summary. |
| Cancel and retry | Users cannot stop accidental work or recover from transient failures without operator intervention. | Add safe cancellation, retry eligibility, and idempotent retry endpoints tied to the lease model. |
| Operator recovery | There is no explicit admin requeue, dead-letter review, or attempt timeline. | Create an auditable operator workflow for retry, quarantine, and terminal failure handling. |
| Structured progress | Channel and long-running jobs provide weak progress visibility. | Persist stage, completed units, total units, heartbeat time, and user-facing status text. |
| API-key scopes | Keys exist, but enforceable least-privilege scopes are incomplete or unclear. | Define scopes, enforce them at route dependencies, display them at creation, and log privileged usage. |
| Retention and deletion | Users and stakeholders lack a clear way to delete ingested content and derived data. | Implement source deletion across PostgreSQL, cache, search index, exports, and backups with documented retention. |
| Search freshness | Users cannot tell whether search is current or degraded. | Expose index lag, fallback state, and last synchronized timestamp in health and admin surfaces. |
| Ingestion idempotency | Repeated submissions can create duplicate work and confusing results. | Use canonical identities, idempotency keys, and explicit existing-job responses. |
| Billing contract | Documentation describes billing behavior that is not implemented. | Either implement the product slice end to end or remove the claims and mark billing as planned. |
| API compatibility | Clients have no clear versioning or deprecation contract. | Publish API stability levels, versioning strategy, and sunset/deprecation behavior. |

## Documentation Review

The documentation is broad and enthusiastic, but several sections describe intended architecture as if it were current behavior. This creates a high support burden and can lead developers or stakeholders to make unsafe deployment and product assumptions.

### Confirmed discrepancies

| Topic | Documented claim | Observed implementation | Correction |
| --- | --- | --- | --- |
| Billing | README and API reference describe Stripe billing behavior. | No matching billing router/settings are present; stale tests and a Stripe dependency remain. | Label billing as planned or implement and test the complete feature before documenting it as available. |
| Authentication | API reference says video, transcript, and search endpoints require authentication. | Several corresponding routes are public. | Generate endpoint auth requirements from OpenAPI/security dependencies and add an explicit access matrix. |
| Schema setup | README says Compose automatically applies sql/schema.sql. | Compose points toward migrations instead. | Document one supported initialization and migration workflow, including first-run and upgrade behavior. |
| Ports | README examples use 5434, 9090, and 3000. | Current Compose configuration uses 5435, 9091, and 3301. | Derive the quickstart from tested Compose commands or keep a checked deployment matrix. |
| Table names | Docs refer to youtube_captions and youtube_caption_segments. | The schema uses youtube_transcripts and youtube_segments. | Update examples and add a schema glossary generated from migrations. |
| Search fallback | Docs promise PostgreSQL fallback when OpenSearch is unavailable. | The orchestrator can return 503 without that fallback. | Implement and test fallback or state OpenSearch as a required dependency. |
| CI quality | Documentation implies comprehensive automated enforcement. | Several checks are nonblocking, important paths do not trigger CI, and collection currently fails. | Publish the exact required gates and keep badges/claims tied to blocking jobs. |

> Evidence: README.md:308; docs/api-reference.md:490; README.md:540; docker-compose.yml:10; .github/workflows/backend-ci.yml

### Recommended documentation architecture

- Make generated OpenAPI the source of truth for request/response schemas, authentication, error models, and examples; fail CI when checked-in API documentation drifts.

- Add a capability matrix distinguishing implemented, experimental, operator-only, disabled-by-default, and planned features.

- Maintain a deployment matrix for local development, test, staging, and production, covering ports, dependencies, credentials, TLS, migrations, and health checks.

- Separate current architecture from historical plans and proposals; every design document should state status, owner, decision date, and superseding document.

- Publish focused operational documents for privacy and retention, worker lease/retry semantics, cache invalidation, OpenSearch freshness, backup restoration, and incident response.

- Add a short developer quickstart that is continuously tested from a clean environment and ends with the canonical verification command.

## Existing Strengths to Preserve

- Production configuration generally fails closed instead of silently using insecure defaults.

- SQL is generally parameterized, reducing injection exposure.

- Job URL validation, quotas, and canonical duplicate detection show the right intent, even though concurrency enforcement needs strengthening.

- SKIP LOCKED job claiming is a sound basis for horizontal worker processing once durable leases are added.

- Archive administration routes use role dependencies rather than relying solely on UI hiding.

- Pydantic models and centralized exception handling provide a foundation for consistent API contracts.

- The repository contains substantial test, migration, health-check, metrics, and backup scaffolding, indicating good operational goals.

- Python bytecode compilation and git diff whitespace validation passed during the review.

## Phased Remediation Roadmap

| Phase | Scope | Acceptance criteria |
| --- | --- | --- |
| 1. Security boundary | Search snippet rendering, CSP, session analytics, admin exports, cache-control defaults. | No raw credential persistence; credential endpoints are no-store; hostile snippet tests pass; production CSP contains no unsafe-eval and avoids unsafe-inline. |
| 2. Worker and cache correctness | Leases, attempts, heartbeat, Redis DTOs, invalidation, atomic quota and idempotency. | A long job cannot be double-finalized; cold/warm cache responses are identical; concurrent submission tests preserve quota and uniqueness. |
| 3. Verification gate | Supported Python version, dependency setup, collection fixes, lint/format/type baseline, CI triggers. | One clean command runs locally and in CI; required jobs are blocking; changes to tests and scripts trigger the backend workflow. |
| 4. Search reliability | Indexer repair, synchronization, deletion, checkpoints, metrics, explicit fallback contract. | Reprocessing and deletion reconcile the index; lag is visible; outage behavior matches documentation and automated tests. |
| 5. Product and docs | Job history, cancel/retry, operator recovery, progress, retention, key scopes, API and deployment docs. | Users can understand and recover work; operators can diagnose attempts; documentation matches generated and deployed behavior. |

### Suggested delivery pattern

Keep each phase bounded: implement one vertical slice, add regression tests, run the canonical verification gate, and update the relevant operational documentation in the same change. The first slice should combine cache-control policy and API-key secret handling because it is small, independently testable, and closes a critical exposure quickly.

## Verification Appendix

| Check | Result | Interpretation |
| --- | --- | --- |
| Python compileall | Passed | Reviewed Python sources compiled successfully. |
| git diff --check origin/main..HEAD | Passed | No whitespace errors were found in the reviewed commit range. |
| Ruff | Failed: 349 findings | Includes nine undefined logging references and substantial existing lint debt. |
| Black | Failed: 41 files | Formatting is not converged at the reviewed revision. |
| isort | Failed: 25 files | Import ordering is not converged. |
| mypy | Failed: 101 findings | Typing is not currently an enforceable repository gate. |
| Pytest collection | Failed after 996 tests collected | Six collection errors prevent a trustworthy full-suite result. |
| Focused tests | 102 passed; 4 failed; 7 setup errors | Four failures were code/contract issues; seven errors required a database that was not started. |
| Docker Compose config | Not completed | The command required a missing .env file, consistent with a quickstart prerequisite but not a clean checkout experience. |
| Bandit | Inconclusive | Local Python 3.14 triggered internal AST errors; this must not be interpreted as a security pass. |

No database services were started and no application service state was changed during the review. Findings based on direct probes are explicitly identified in the relevant sections.

## Key Evidence Index

| Area | Repository paths |
| --- | --- |
| Security and middleware | app/middleware.py; app/routes/api_keys.py; app/routes/events.py; app/routes/admin.py; app/security.py |
| Search | app/search/segment_repository.py; app/search/orchestrator.py; app/search/analytics.py; scripts/opensearch_indexer.py |
| Jobs and workers | app/routes/jobs.py; app/routes/vocabularies.py; worker/loop.py; worker/native_pipeline.py |
| Data and cache | app/cache.py; app/crud.py; app/archive/intelligence_repository.py; sql/schema.sql |
| Frontend XSS sinks | frontend/src/components/archive/SearchMomentsList.tsx; frontend/src/components/archive/TopicMentionCard.tsx; frontend/src/routes/TopicPage.tsx; frontend/src/components/video/PlainTranscriptTurns.tsx |
| Delivery and docs | .github/workflows/backend-ci.yml; docker-compose.yml; docker-compose.prod.yml; README.md; docs/api-reference.md |
