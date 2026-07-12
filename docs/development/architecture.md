# Architecture

**Status:** shipped and authoritative (2026-07-12).

React 19 and React Router provide lazy-loaded public and capability-gated admin routes. TanStack Query owns shared request freshness, cancellation, and deduplication; Ky is the HTTP transport. Search snippets are plain text with Unicode code-point highlight ranges.

FastAPI exposes the stable `/api` contract. PostgreSQL is the source of truth. Redis stores versioned JSON DTOs only. OpenSearch is an optional read accelerator maintained by a transactional PostgreSQL outbox; classified outages fall back to PostgreSQL and expose degraded/freshness metadata.

API and worker dependencies/images are separate. Workers claim durable leases, renew heartbeats, record attempts, and use compare-and-set finalization. Network and GPU work occurs outside database transactions.

Authentication uses server-side sessions. Authorization policy centralizes roles, capabilities, entitlements, vocabulary ownership, and API-key scopes. Analytics uses a separate random cookie and stores only an HMAC-derived subject.
