"""Owner/admin source deletion across durable and derived storage."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import requests  # type: ignore[import-untyped]
from sqlalchemy import text

from app.cache import invalidate_video_data
from app.db import SessionLocal
from app.logging_config import get_logger
from app.settings import settings

logger = get_logger(__name__)


def delete_source(db, *, video_id, deleted_by_user_id):
    """Write the durable deletion/tombstone in the caller-owned transaction."""
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
    tombstone_id = db.execute(
        text("""
            INSERT INTO source_deletions (
                video_id, youtube_id, owner_user_id, deleted_by_user_id, backup_exclusion_until,
                raw_path, wav_path
            ) VALUES (
                :video_id, :youtube_id, :owner_user_id, :deleted_by_user_id,
                now() + interval '90 days', :raw_path, :wav_path
            )
            RETURNING id
        """),
        {
            "video_id": row["id"],
            "youtube_id": row["youtube_id"],
            "owner_user_id": row["owner_user_id"],
            "deleted_by_user_id": deleted_by_user_id,
            "raw_path": row["raw_path"],
            "wav_path": row["wav_path"],
        },
    ).scalar_one()
    db.execute(text("DELETE FROM videos WHERE id=:video_id"), {"video_id": video_id})
    deleted = dict(row)
    deleted["tombstone_id"] = tombstone_id
    return deleted


def cleanup_deleted_source(row: dict, *, lease_token: str | None = None) -> None:
    """Perform independent post-commit cleanup and durably record its result."""
    if lease_token is None:
        lease_token = _claim_specific_cleanup(row["tombstone_id"])
        if lease_token is None:
            return
    errors: list[str] = []
    _attempt("cache", lambda: invalidate_video_data(row["id"], strict=True), errors)
    for label, path in (("raw_file", row.get("raw_path")), ("wav_file", row.get("wav_path"))):
        _attempt(label, lambda path=path: _delete_file(path), errors)
    if settings.SEARCH_BACKEND == "opensearch":
        for index in (settings.OPENSEARCH_INDEX_NATIVE, settings.OPENSEARCH_INDEX_YOUTUBE):
            _attempt("search_index", lambda index=index: _delete_index_documents(row["id"], index), errors)
    _record_cleanup_result(row["tombstone_id"], errors, lease_token=lease_token)


def _attempt(component: str, operation, errors: list[str]) -> None:
    try:
        operation()
    except Exception as exc:
        # Do not include paths, URLs, responses, or exception text in durable logs.
        errors.append(f"{component}:{type(exc).__name__}")
        logger.warning(
            "Deferred source cleanup component", extra={"component": component, "error_type": type(exc).__name__}
        )


def _claim_specific_cleanup(tombstone_id, *, lease_seconds: int = 120) -> str | None:
    """Claim one tombstone for inline cleanup without racing the reconciler."""
    lease_token = str(uuid4())
    db = SessionLocal()
    try:
        claimed = db.execute(
            text("""
            UPDATE source_deletions
            SET cleanup_started_at=now(),
                cleanup_lease_until=now() + make_interval(secs => :lease_seconds),
                cleanup_lease_token=CAST(:lease_token AS uuid)
            WHERE id=:id
              AND cleanup_status='pending'
              AND (cleanup_lease_until IS NULL OR cleanup_lease_until < now())
            RETURNING cleanup_lease_token
        """),
            {"id": tombstone_id, "lease_seconds": lease_seconds, "lease_token": lease_token},
        ).scalar()
        db.commit()
        return str(claimed) if claimed is not None else None
    except Exception as exc:
        db.rollback()
        logger.warning("Source cleanup claim failed", extra={"error_type": type(exc).__name__})
        return None
    finally:
        db.close()


def _record_cleanup_result(tombstone_id, errors: list[str], *, lease_token: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text("""
            UPDATE source_deletions
            SET cleanup_status=CASE WHEN :ok THEN 'completed' ELSE 'pending' END,
                cleanup_attempts=cleanup_attempts + 1,
                cleanup_error=:error,
                cleanup_completed_at=CASE WHEN :ok THEN now() ELSE NULL END,
                cleanup_next_attempt_at=CASE WHEN :ok THEN NULL ELSE now() + make_interval(
                    secs => LEAST(3600, power(2, LEAST(GREATEST(cleanup_attempts, 0), 10) + 1)::integer)) END,
                cleanup_lease_until=NULL,
                cleanup_lease_token=NULL
            WHERE id=:id
              AND cleanup_lease_token=CAST(:lease_token AS uuid)
        """),
            {
                "id": tombstone_id,
                "ok": not errors,
                "error": ";".join(errors)[:1000] or None,
                "lease_token": lease_token,
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Source cleanup state update failed", extra={"error_type": type(exc).__name__})
    finally:
        db.close()


def _delete_file(raw_path) -> None:
    root = Path(settings.WORKDIR).resolve()
    if not raw_path:
        return
    path = Path(raw_path).resolve()
    if path == root or root not in path.parents:
        raise ValueError("source path outside work directory")
    path.unlink(missing_ok=True)


def _delete_index_documents(video_id, index) -> None:
    query = {"query": {"term": {"video_id": str(video_id)}}}
    auth = None
    if settings.OPENSEARCH_USER and settings.OPENSEARCH_PASSWORD:
        auth = (settings.OPENSEARCH_USER, settings.OPENSEARCH_PASSWORD)
    response = requests.post(
        f"{settings.OPENSEARCH_URL.rstrip('/')}/{index}/_delete_by_query",
        auth=auth,
        json=query,
        timeout=10,
        verify=settings.OPENSEARCH_VERIFY_SSL,
    )
    if response.status_code not in {200, 404}:
        response.raise_for_status()


def reconcile_pending_source_deletions(*, limit: int = 10, lease_seconds: int = 120) -> int:
    """Claim and retry bounded tombstones without holding a transaction for I/O."""
    processed = 0
    for _ in range(limit):
        lease_token = str(uuid4())
        db = SessionLocal()
        try:
            row = (
                db.execute(
                    text("""
                WITH claimed AS (
                    SELECT id FROM source_deletions
                    WHERE cleanup_status='pending'
                      AND (cleanup_next_attempt_at IS NULL OR cleanup_next_attempt_at <= now())
                      AND (cleanup_lease_until IS NULL OR cleanup_lease_until < now())
                    ORDER BY deleted_at
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE source_deletions d
                SET cleanup_started_at=now(),
                    cleanup_lease_until=now() + make_interval(secs => :lease_seconds),
                    cleanup_lease_token=CAST(:lease_token AS uuid)
                FROM claimed WHERE d.id=claimed.id
                RETURNING d.id AS tombstone_id, d.video_id AS id, d.raw_path, d.wav_path
            """),
                    {"lease_seconds": lease_seconds, "lease_token": lease_token},
                )
                .mappings()
                .first()
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if row is None:
            break
        cleanup_deleted_source(dict(row), lease_token=lease_token)
        processed += 1
    return processed
