"""Owner/admin source deletion across durable and derived storage."""

from __future__ import annotations

from pathlib import Path

import requests  # type: ignore[import-untyped]
from sqlalchemy import text

from app.cache import invalidate_video_data
from app.logging_config import get_logger
from app.settings import settings

logger = get_logger(__name__)


def delete_source(db, *, video_id, deleted_by_user_id):
    row = (
        db.execute(
            text("""
                SELECT v.id, v.youtube_id, v.raw_path, v.wav_path, j.owner_user_id
                FROM videos v JOIN jobs j ON j.id=v.job_id WHERE v.id=:video_id
                FOR UPDATE
            """),
            {"video_id": video_id},
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    db.execute(
        text("""
            INSERT INTO source_deletions (
                video_id, youtube_id, owner_user_id, deleted_by_user_id, backup_exclusion_until
            ) VALUES (
                :video_id, :youtube_id, :owner_user_id, :deleted_by_user_id,
                now() + interval '90 days'
            )
        """),
        {
            "video_id": row["id"],
            "youtube_id": row["youtube_id"],
            "owner_user_id": row["owner_user_id"],
            "deleted_by_user_id": deleted_by_user_id,
        },
    )
    db.execute(text("DELETE FROM videos WHERE id=:video_id"), {"video_id": video_id})
    db.commit()

    invalidate_video_data(video_id)
    _delete_files(row["raw_path"], row["wav_path"])
    _delete_index_documents(video_id)
    return dict(row)


def _delete_files(*paths) -> None:
    root = Path(settings.WORKDIR).resolve()
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        if path == root or root not in path.parents:
            logger.error("Refused source deletion outside work directory", extra={"path": str(path)})
            continue
        path.unlink(missing_ok=True)


def _delete_index_documents(video_id) -> None:
    query = {"query": {"term": {"video_id": str(video_id)}}}
    for index in (settings.OPENSEARCH_INDEX_NATIVE, settings.OPENSEARCH_INDEX_YOUTUBE):
        try:
            response = requests.post(
                f"{settings.OPENSEARCH_URL.rstrip('/')}/{index}/_delete_by_query",
                json=query,
                timeout=10,
            )
            if response.status_code not in {200, 404}:
                response.raise_for_status()
        except requests.RequestException as exc:
            # PostgreSQL remains authoritative. The durable source_deletions
            # tombstone is consumed by reconciliation after OpenSearch recovers.
            logger.warning(
                "Deferred OpenSearch source deletion",
                extra={"video_id": str(video_id), "index": index, "error": str(exc)},
            )
