# Production readiness runbooks

**Status:** shipped operational index (2026-07-12).

- **Worker leases/retries:** inspect job stage, heartbeat, owner, attempts, lease expiry, and last failure. Requeue eligible jobs; quarantine poison work. Compare-and-set prevents stale workers finalizing.
- **Cache invalidation:** mutations invalidate video, transcript, search, archive, and aggregate keys. Compare cold/warm payload bytes when diagnosing drift.
- **Search freshness:** PostgreSQL is authoritative. Monitor outbox pending/dead-letter rows, checkpoints, `indexed_at`, and `index_lag_seconds`. Reconcile tombstones after deletion/reprocessing.
- **Privacy and retention:** analytics stores HMAC subjects, limits payloads/rates, deletes raw events after 90 days, and retains only non-identifying aggregates.
- **Deletion:** owner/admin deletion removes source rows, transcripts, segments, caches, index documents, pending exports, and future snapshots. Existing immutable backups expire under the backup retention schedule.
- **Backup restore:** verify PostgreSQL and Almaz bind-mount backups before migrations; record restore time and integrity evidence.
- **Incident response:** classify auth/validation/quota faults separately from infrastructure outages; do not mask them as fallback results. Preserve audit logs, rotate affected credentials/sessions, and document rollback boundaries.
