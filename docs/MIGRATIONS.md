# Database migrations

**Status:** shipped operational guidance (2026-07-12).

Use additive Alembic migrations first. Verify the full history on an empty PostgreSQL database with `make verify`. Before production migration, back up PostgreSQL and complete a restore rehearsal.

Deploy additive schema, then compatible API/frontend/worker images, backfills, and finally deferred destructive cleanup in a later release. Historical billing columns remain dormant compatibility fields; they do not imply a billing contract.

Analytics credential scrub and session rotation are irreversible boundaries: after rollout, do not restore an image that writes session credentials to events. Search outbox backfills must reconcile PostgreSQL/OpenSearch counts before OpenSearch becomes primary.
