#!/usr/bin/env bash
# Guarded, post-deploy analytics credential scrub and login-session rotation.
# Do not run until token-free application code is deployed and old pods drain.

set -euo pipefail

EXPECTED_CONFIRMATION="rotate-all-sessions-and-scrub-event-credentials"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL is required" >&2
    exit 1
fi

if [[ "${CONFIRM_ANALYTICS_CREDENTIAL_ROTATION:-}" != "${EXPECTED_CONFIRMATION}" ]]; then
    echo "ERROR: refusing to invalidate sessions without explicit confirmation" >&2
    echo "Set CONFIRM_ANALYTICS_CREDENTIAL_ROTATION=${EXPECTED_CONFIRMATION}" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "ERROR: psql is required" >&2
    exit 1
fi

# psql accepts PostgreSQL URLs but not SQLAlchemy's driver-qualified scheme.
PSQL_DATABASE_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

psql "${PSQL_DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- Serialize finalization attempts and fail if the expand migration is absent.
SELECT pg_advisory_xact_lock(hashtext('hasanara:analytics-retention-finalization'));
SET LOCAL TIME ZONE 'UTC';

DO $assert_expand_migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'events'
          AND column_name = 'analytics_subject_id'
    ) THEN
        RAISE EXCEPTION 'analytics identity expand migration is not installed';
    END IF;
END
$assert_expand_migration$;

-- Preserve non-identifying counts before enforcing the 90-day raw-event
-- retention window. Summing with the existing aggregate handles late events;
-- deletion in this same transaction makes reruns idempotent.
INSERT INTO event_daily_aggregates (day, type, count, updated_at)
SELECT day, aggregate_type, COUNT(*), CURRENT_TIMESTAMP
FROM (
    SELECT
        (created_at AT TIME ZONE 'UTC')::date AS day,
        CASE
            WHEN type IN (
                'search',
                'result_click',
                'seek',
                'favorite_add',
                'favorite_remove',
                'video_open',
                'export_click',
                'export',
                'search_api'
            ) THEN type
            ELSE 'other'
        END AS aggregate_type
    FROM events
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '90 days'
) AS expired_events
GROUP BY day, aggregate_type
ON CONFLICT (day, type) DO UPDATE
SET count = event_daily_aggregates.count + EXCLUDED.count,
    updated_at = CURRENT_TIMESTAMP;

DELETE FROM events WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '90 days';

-- Irreversible privacy boundary: remove stored credentials and invalidate
-- every credential that could previously have appeared in analytics data.
UPDATE events SET session_token = NULL WHERE session_token IS NOT NULL;
UPDATE events SET payload = '{}'::jsonb;
UPDATE events
SET type = 'other'
WHERE type NOT IN (
    'search',
    'result_click',
    'seek',
    'favorite_add',
    'favorite_remove',
    'video_open',
    'export_click',
    'export',
    'search_api',
    'other'
);
DELETE FROM sessions;

-- Guard the compatibility window against a drained/rolled-back legacy writer.
CREATE OR REPLACE FUNCTION null_legacy_event_session_token()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.session_token := NULL;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS events_null_legacy_session_token ON events;
CREATE TRIGGER events_null_legacy_session_token
BEFORE INSERT OR UPDATE OF session_token ON events
FOR EACH ROW
EXECUTE FUNCTION null_legacy_event_session_token();

COMMIT;
SQL

echo "Analytics credentials scrubbed, login sessions rotated, and legacy writes guarded."
