"""Search outbox freshness queries shared by API, health, and operators."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import text


def search_freshness(db) -> dict:
    row = db.execute(text("""
            SELECT c.indexed_at,
                   COUNT(o.id) FILTER (WHERE o.processed_at IS NULL AND o.dead_lettered_at IS NULL) AS pending,
                   MIN(o.created_at) FILTER (WHERE o.processed_at IS NULL AND o.dead_lettered_at IS NULL) AS oldest,
                   COUNT(o.id) FILTER (WHERE o.dead_lettered_at IS NOT NULL) AS dead_letters
            FROM (SELECT MAX(indexed_at) AS indexed_at FROM search_index_checkpoints) c
            LEFT JOIN search_index_outbox o ON true
            GROUP BY c.indexed_at
        """)).mappings().one()
    if not isinstance(row, Mapping):
        return {"indexed_at": None, "index_lag_seconds": 0, "pending_documents": 0, "dead_letter_documents": 0}
    oldest = row["oldest"]
    lag = max(0, int((datetime.now(timezone.utc) - oldest).total_seconds())) if oldest else 0
    return {
        "indexed_at": row["indexed_at"],
        "index_lag_seconds": lag,
        "pending_documents": int(row["pending"] or 0),
        "dead_letter_documents": int(row["dead_letters"] or 0),
    }
