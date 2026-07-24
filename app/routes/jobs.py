import json
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, status
from sqlalchemy import text

from .. import crud
from ..db import get_db
from ..exceptions import JobNotFoundError, RateLimitError, ValidationError
from ..schemas import ErrorResponse, JobAttempt, JobCreate, JobStatus
from ..security import ROLE_ADMIN, get_user_required, get_user_role, require_role
from ..settings import settings

router = APIRouter(prefix="", tags=["Jobs"])


def _extract_youtube_video_id(url: str) -> str | None:
    """Extract a canonical YouTube video ID for duplicate suppression."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate or None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} or host.endswith(".youtube.com"):
        query_video = parse_qs(parsed.query).get("v", [None])[0]
        if query_video:
            return query_video
    return None


def _normalize_job_url(url: str, kind: str) -> str:
    """Normalize a submitted URL enough for same-owner duplicate detection."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if kind == "single":
        youtube_id = _extract_youtube_video_id(url)
        if youtube_id:
            return f"youtube:video:{youtube_id}"
    if kind == "channel":
        path = parsed.path.rstrip("/")
        if path and not path.endswith("/videos") and ("/channel/" in path or path.startswith("/@")):
            path = f"{path}/videos"
        return f"{parsed.scheme.lower()}://{host}{path}"
    return url.rstrip("/")


def _quota_limits_for_user(user: dict) -> tuple[int, int]:
    """Return (total_limit, channel_limit) for a user in the configured quota window."""
    role = get_user_role(user)
    if role == ROLE_ADMIN and settings.JOB_CREATE_ADMIN_BYPASS_QUOTAS:
        return -1, -1
    if str(user.get("plan") or "free").lower() == settings.PRO_PLAN_NAME.lower():
        return settings.JOB_CREATE_PRO_DAILY_LIMIT, settings.JOB_CREATE_PRO_CHANNEL_DAILY_LIMIT
    return settings.JOB_CREATE_DAILY_LIMIT, settings.JOB_CREATE_CHANNEL_DAILY_LIMIT


def _count_recent_user_jobs(db, *, user_id: str, kind: str | None = None) -> int:
    where = ["j.owner_user_id = CAST(:user_id AS uuid)", "j.created_at >= now() - make_interval(hours => :hours)"]
    params: dict[str, object] = {
        "user_id": user_id,
        "hours": settings.JOB_CREATE_QUOTA_WINDOW_HOURS,
    }
    if kind:
        where.append("j.kind = :kind")
        params["kind"] = kind
    row = db.execute(
        text(f"SELECT COUNT(*) FROM jobs j WHERE {' AND '.join(where)}"),
        params,
    ).first()
    return int(row[0] if row else 0)


def _enforce_job_quota(db, *, user: dict, kind: str) -> None:
    user_id = str(user["id"])
    total_limit, channel_limit = _quota_limits_for_user(user)
    window_hours = settings.JOB_CREATE_QUOTA_WINDOW_HOURS

    if total_limit >= 0 and _count_recent_user_jobs(db, user_id=user_id) >= total_limit:
        raise RateLimitError(
            "Job creation quota exceeded. Please try again later.",
            details={
                "limit": total_limit,
                "window_hours": window_hours,
                "quota": "jobs",
            },
        )
    if (
        kind == "channel"
        and channel_limit >= 0
        and _count_recent_user_jobs(db, user_id=user_id, kind="channel") >= channel_limit
    ):
        raise RateLimitError(
            "Channel job creation quota exceeded. Please try again later.",
            details={
                "limit": channel_limit,
                "window_hours": window_hours,
                "quota": "channel_jobs",
            },
        )


def _validate_vocabulary_ids(db, *, user_id: str, vocabulary_ids: list[uuid.UUID] | None) -> None:
    if not vocabulary_ids:
        return
    selected = list(dict.fromkeys(str(vocabulary_id) for vocabulary_id in vocabulary_ids))
    rows = (
        db.execute(
            text(
                "SELECT id FROM user_vocabularies "
                "WHERE id = ANY(CAST(:vocabulary_ids AS uuid[])) "
                "AND (is_global=true OR user_id=:user_id)"
            ),
            {"vocabulary_ids": selected, "user_id": user_id},
        )
        .scalars()
        .all()
    )
    visible = {str(row) for row in rows}
    if len(selected) != len(vocabulary_ids) or visible != set(selected):
        raise ValidationError(
            "Every vocabulary_id must reference a visible global or owner vocabulary",
            field="vocabulary_ids",
        )


def _find_duplicate_job(db, *, user_id: str, kind: str, normalized_url: str, youtube_id: str | None):
    params = {
        "user_id": user_id,
        "kind": kind,
        "normalized_url": normalized_url,
        "youtube_id": youtube_id,
    }
    return (
        db.execute(
            text("""
                SELECT j.id
                FROM jobs j
                LEFT JOIN videos v ON v.job_id = j.id
                WHERE j.owner_user_id = CAST(:user_id AS uuid)
                  AND j.kind = :kind
                  AND j.state <> 'failed'
                  AND (
                    j.meta->>'normalized_url' = :normalized_url
                    OR (
                      CAST(:youtube_id AS TEXT) IS NOT NULL
                      AND (j.meta->>'youtube_id' = :youtube_id OR v.youtube_id = :youtube_id)
                    )
                  )
                ORDER BY j.created_at DESC
                LIMIT 1
                """),
            params,
        )
        .mappings()
        .first()
    )


def _enforce_job_shape_limits(payload: JobCreate) -> None:
    if payload.kind == "channel" and settings.JOB_CREATE_MAX_CHANNEL_VIDEOS <= 0:
        raise ValidationError("Channel job creation is disabled", field="kind")
    if (
        payload.batch_expected_jobs is not None
        and payload.batch_expected_jobs > settings.JOB_CREATE_MAX_BATCH_EXPECTED_JOBS
    ):
        raise ValidationError(
            f"batch_expected_jobs cannot exceed {settings.JOB_CREATE_MAX_BATCH_EXPECTED_JOBS}",
            field="batch_expected_jobs",
            details={"max": settings.JOB_CREATE_MAX_BATCH_EXPECTED_JOBS},
        )


def _row_to_status(row):
    return JobStatus(
        id=row["id"],
        kind=row["kind"],
        state=row["state"],
        error=row["error"],
        stage=row.get("stage") or "queued",
        completed_units=row.get("completed_units") or 0,
        total_units=row.get("total_units"),
        heartbeat_at=row.get("heartbeat_at"),
        cancellation_requested_at=row.get("cancellation_requested_at"),
        cancelled_at=row.get("cancelled_at"),
        attempt_count=row.get("attempt_count") or 0,
        last_failure_summary=row.get("last_failure_summary"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post(
    "/jobs",
    response_model=JobStatus,
    status_code=status.HTTP_200_OK,
    summary="Create a transcription job",
    description="""
    Create a new transcription job for a YouTube video or channel.

    Authentication is required via session cookie or API key. Creation is subject
    to configurable per-user quotas and channel-size caps to protect GPU, disk,
    and YouTube quota.

    The job will be processed asynchronously by the worker. Use the returned job ID
    to check status via GET /jobs/{job_id}.

    **Job Types:**
    - `single`: Transcribe one video
    - `channel`: Transcribe all videos from a channel (may create many video jobs)

    **Job States:**
    - `pending`: Job created, waiting to be expanded
    - `expanded`: Videos identified and queued for transcription
    - `completed`: All videos transcribed successfully
    - `failed`: Job encountered an error

    **Quality Settings:**
    Optionally specify quality settings for transcription:
    - `preset`: 'fast', 'balanced', or 'accurate'
    - `language`: Language code (e.g., 'en', 'es') or omit for auto-detection
    - `model`: Whisper model size (overrides preset)
    - `beam_size`: Beam search size (1-10)
    - `temperature`: Sampling temperature (0.0-1.0)
    - `word_timestamps`: Extract word-level timestamps (default: true)
    """,
    responses={
        200: {
            "description": "Job created successfully",
            "content": {
                "application/json": {
                    "example": {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "kind": "single",
                        "state": "pending",
                        "error": None,
                        "created_at": "2025-10-25T10:30:00Z",
                        "updated_at": "2025-10-25T10:30:00Z",
                    }
                }
            },
        },
        401: {
            "description": "Authentication required",
            "model": ErrorResponse,
        },
        409: {
            "description": "Duplicate active job",
            "model": ErrorResponse,
        },
        429: {
            "description": "Job creation quota exceeded",
            "model": ErrorResponse,
        },
        422: {
            "description": "Validation error - invalid URL or parameters",
            "model": ErrorResponse,
        },
    },
)
def create_job(payload: JobCreate, db=Depends(get_db), user=Depends(get_user_required)):
    """Create a new transcription job."""
    _enforce_job_shape_limits(payload)
    owner_user_id = str(user["id"])
    source_url = str(payload.url)
    normalized_url = _normalize_job_url(source_url, payload.kind)
    youtube_id = _extract_youtube_video_id(source_url) if payload.kind == "single" else None

    # Serialize a user's quota check, duplicate lookup, and insertion in one
    # transaction. This prevents concurrent requests from both passing quota.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"), {"owner": owner_user_id})
    _validate_vocabulary_ids(db, user_id=owner_user_id, vocabulary_ids=payload.vocabulary_ids)
    _enforce_job_quota(db, user=user, kind=payload.kind)

    if payload.idempotency_key:
        idempotent = (
            db.execute(
                text("SELECT * FROM jobs WHERE owner_user_id=:owner AND idempotency_key=:key LIMIT 1"),
                {"owner": owner_user_id, "key": payload.idempotency_key},
            )
            .mappings()
            .first()
        )
        if idempotent:
            return _row_to_status(idempotent)

    duplicate = _find_duplicate_job(
        db,
        user_id=owner_user_id,
        kind=payload.kind,
        normalized_url=normalized_url,
        youtube_id=youtube_id,
    )
    if duplicate:
        return _row_to_status(crud.fetch_job(db, duplicate["id"]))

    # Build job metadata with quality settings and vocabulary
    meta: dict[str, object] = {
        "created_by": "api_key" if user.get("api_key_id") else "session",
        "normalized_url": normalized_url,
    }
    if youtube_id:
        meta["youtube_id"] = youtube_id
    if payload.kind == "channel":
        meta["max_channel_videos"] = settings.JOB_CREATE_MAX_CHANNEL_VIDEOS
    if payload.quality:
        meta["quality"] = payload.quality.model_dump(exclude_none=True)
    if payload.vocabulary_ids:
        meta["vocabulary_ids"] = [str(vid) for vid in payload.vocabulary_ids]
    if payload.batch_id:
        meta["batch_id"] = payload.batch_id
    if payload.batch_expected_jobs:
        meta["batch_expected_jobs"] = payload.batch_expected_jobs
    if payload.staged:
        meta["staged"] = True
    if payload.idempotency_key:
        meta["idempotency_key"] = payload.idempotency_key
    meta = crud.normalize_job_ownership_meta(meta, owner_user_id=owner_user_id, api_key_id=user.get("api_key_id"))

    job_id = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO jobs (
                id, kind, input_url, meta, owner_user_id, canonical_source, idempotency_key
            ) VALUES (
                :id, :kind, :url, CAST(:meta AS jsonb), :owner, :canonical, :idempotency_key
            )
        """),
        {
            "id": job_id,
            "kind": payload.kind,
            "url": source_url,
            "meta": json.dumps(meta),
            "owner": owner_user_id,
            "canonical": normalized_url,
            "idempotency_key": payload.idempotency_key,
        },
    )
    db.commit()
    job = crud.fetch_job(db, job_id)
    return _row_to_status(job)


@router.get("/jobs", response_model=list[JobStatus], summary="List the current user's jobs")
def list_jobs(db=Depends(get_db), user=Depends(get_user_required)):
    rows = (
        db.execute(
            text("SELECT * FROM jobs WHERE owner_user_id=:owner ORDER BY created_at DESC"),
            {"owner": str(user["id"])},
        )
        .mappings()
        .all()
    )
    return [_row_to_status(row) for row in rows]


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    summary="Get job status",
    description="""
    Retrieve the current status of a transcription job.

    Poll this endpoint to monitor job progress. Check the `state` field to determine
    if the job is still processing or has completed.
    """,
    responses={
        200: {
            "description": "Job status retrieved successfully",
        },
        404: {
            "description": "Job not found",
            "model": ErrorResponse,
            "content": {
                "application/json": {
                    "example": {
                        "error": "job_not_found",
                        "message": "Job with ID 123e4567-e89b-12d3-a456-426614174000 not found",
                        "details": {},
                    }
                }
            },
        },
    },
)
def get_job(job_id: uuid.UUID, db=Depends(get_db), user=Depends(get_user_required)):
    """Get the status of a specific job."""
    job = (
        db.execute(
            text("SELECT * FROM jobs WHERE id=:id AND owner_user_id=:owner"),
            {"id": job_id, "owner": str(user["id"])},
        )
        .mappings()
        .first()
    )
    if not job:
        raise JobNotFoundError(str(job_id))
    return _row_to_status(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobStatus)
def cancel_job(job_id: uuid.UUID, db=Depends(get_db), user=Depends(get_user_required)):
    row = (
        db.execute(
            text("""
                UPDATE jobs
                SET cancellation_requested_at=COALESCE(cancellation_requested_at, now()),
                    cancelled_at=CASE WHEN state='pending' THEN now() ELSE cancelled_at END,
                    stage=CASE WHEN state='pending' THEN 'cancelled' ELSE 'cancellation_requested' END,
                    updated_at=now()
                WHERE id=:id AND owner_user_id=:owner
                  AND state NOT IN ('completed', 'failed', 'needs_attention')
                RETURNING *
            """),
            {"id": job_id, "owner": str(user["id"])},
        )
        .mappings()
        .first()
    )
    if not row:
        raise JobNotFoundError(str(job_id))
    db.commit()
    return _row_to_status(row)


@router.post("/jobs/{job_id}/retry", response_model=JobStatus)
def retry_job(job_id: uuid.UUID, db=Depends(get_db), user=Depends(get_user_required)):
    row = (
        db.execute(
            text("""
                UPDATE jobs SET state='pending', stage='queued', error=NULL,
                    last_failure_summary=NULL, cancellation_requested_at=NULL,
                    cancelled_at=NULL, quarantined_at=NULL, updated_at=now()
                WHERE id=:id AND owner_user_id=:owner
                  AND state IN ('failed', 'needs_attention')
                RETURNING *
            """),
            {"id": job_id, "owner": str(user["id"])},
        )
        .mappings()
        .first()
    )
    if not row:
        raise JobNotFoundError(str(job_id))
    db.commit()
    return _row_to_status(row)


@router.get("/admin/jobs/{job_id}/attempts", response_model=list[JobAttempt])
def list_job_attempts(job_id: uuid.UUID, db=Depends(get_db), user=Depends(require_role(ROLE_ADMIN))):
    del user
    return (
        db.execute(text("SELECT * FROM job_attempts WHERE job_id=:id ORDER BY attempt_number"), {"id": job_id})
        .mappings()
        .all()
    )


def _admin_reset_job(job_id: uuid.UUID, db, *, quarantine: bool):
    row = (
        db.execute(
            text("""
                UPDATE jobs SET state=:state, stage=:stage, error=NULL,
                    cancellation_requested_at=NULL, cancelled_at=NULL,
                    quarantined_at=CASE WHEN :quarantine THEN now() ELSE NULL END,
                    updated_at=now()
                WHERE id=:id RETURNING *
            """),
            {
                "id": job_id,
                "state": "needs_attention" if quarantine else "pending",
                "stage": "quarantined" if quarantine else "queued",
                "quarantine": quarantine,
            },
        )
        .mappings()
        .first()
    )
    if not row:
        raise JobNotFoundError(str(job_id))
    db.commit()
    return _row_to_status(row)


@router.post("/admin/jobs/{job_id}/requeue", response_model=JobStatus)
def admin_requeue_job(job_id: uuid.UUID, db=Depends(get_db), user=Depends(require_role(ROLE_ADMIN))):
    del user
    return _admin_reset_job(job_id, db, quarantine=False)


@router.post("/admin/jobs/{job_id}/quarantine", response_model=JobStatus)
def admin_quarantine_job(job_id: uuid.UUID, db=Depends(get_db), user=Depends(require_role(ROLE_ADMIN))):
    del user
    return _admin_reset_job(job_id, db, quarantine=True)
