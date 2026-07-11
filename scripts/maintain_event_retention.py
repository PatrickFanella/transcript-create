#!/usr/bin/env python3
"""Aggregate and delete analytics events outside the 90-day raw-data window."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import engine

RETENTION_SQL = """
SELECT pg_advisory_xact_lock(hashtext('hasanara:analytics-retention-finalization'));
SET LOCAL TIME ZONE 'UTC';

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
"""


def maintain_event_retention(database_engine=engine) -> None:
    """Preserve aggregate counts and delete expired raw rows atomically."""

    with database_engine.begin() as connection:
        connection.execute(text(RETENTION_SQL))


if __name__ == "__main__":
    maintain_event_retention()
    print("Analytics events outside the 90-day raw-data window were aggregated and deleted.")
