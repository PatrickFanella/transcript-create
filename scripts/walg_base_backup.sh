#!/usr/bin/env bash
# Create a physical PostgreSQL base backup and prune only after it succeeds.
set -Eeuo pipefail

: "${WALG_S3_PREFIX:?WALG_S3_PREFIX is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
: "${AWS_ENDPOINT:?AWS_ENDPOINT is required}"
: "${PGDATA:?PGDATA is required}"

command -v flock >/dev/null || { echo "ERROR: flock is required" >&2; exit 1; }
command -v wal-g >/dev/null || { echo "ERROR: wal-g is required" >&2; exit 1; }
[[ -d "$PGDATA" ]] || { echo "ERROR: PGDATA is not a directory: $PGDATA" >&2; exit 1; }

exec 9>/tmp/hasanara-walg-base-backup.lock
flock -n 9 || { echo "ERROR: another WAL-G base backup is already running" >&2; exit 1; }

wal-g backup-push "$PGDATA"
wal-g delete retain FULL "${WALG_RETAIN_FULL:-5}" --confirm
