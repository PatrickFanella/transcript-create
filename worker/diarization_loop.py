import time
from pathlib import Path
from threading import Event, Thread

from sqlalchemy import create_engine, text

from app.cache import invalidate_video_data
from app.logging_config import configure_logging, get_logger
from app.settings import settings
from worker.diarize import run_diarization

configure_logging(
    service="diarization-worker",
    level=settings.LOG_LEVEL,
    json_format=(settings.LOG_FORMAT == "json"),
)
logger = get_logger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 60


def _allowed_video_ids():
    return list(settings.DIARIZATION_ALLOWED_VIDEO_IDS)


def _allowlist_params() -> dict:
    return {"allowed_video_ids": _allowed_video_ids()}


def _require_valid_allowlist() -> None:
    allowed = _allowed_video_ids()
    if len(allowed) > 5:
        raise ValueError("DIARIZATION_ALLOWED_VIDEO_IDS may contain at most 5 UUIDs")
    if not allowed:
        raise ValueError("standalone diarization worker requires DIARIZATION_ALLOWED_VIDEO_IDS")


def _load_segments(conn, video_id):
    rows = (
        conn.execute(
            text("""
            SELECT id, start_ms, end_ms, text, speaker_label, confidence, avg_logprob, temperature, token_count
            FROM segments
            WHERE video_id = :v
              AND video_id = ANY(:allowed_video_ids)
            ORDER BY start_ms, id
            """),
            {"v": video_id, **_allowlist_params()},
        )
        .mappings()
        .all()
    )
    return [
        {
            "_segment_id": row["id"],
            "start": float(row["start_ms"] or 0) / 1000.0,
            "end": float(row["end_ms"] or 0) / 1000.0,
            "text": row["text"] or "",
            "speaker": row["speaker_label"],
            "speaker_label": row["speaker_label"],
            "confidence": row["confidence"],
            "avg_logprob": row["avg_logprob"],
            "temperature": row["temperature"],
            "token_count": row["token_count"],
        }
        for row in rows
    ]


def _claim_video(conn):
    row = (
        conn.execute(
            text("""
            SELECT v.id, v.wav_path
            FROM videos v
            WHERE v.state = 'completed'
              AND v.wav_path IS NOT NULL
              AND v.diarization_state = 'pending'
              AND v.duration_seconds IS NOT NULL
              AND v.duration_seconds <= :max_duration_seconds
              AND v.id = ANY(:allowed_video_ids)
              AND EXISTS (SELECT 1 FROM segments s WHERE s.video_id = v.id)
            ORDER BY v.updated_at, v.created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """),
            {"max_duration_seconds": settings.DIARIZATION_MAX_DURATION_SECONDS, **_allowlist_params()},
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    logger.info("Claiming diarization job", extra={"video_id": str(row["id"]), "wav_path": row["wav_path"]})
    result = conn.execute(
        text(
            "UPDATE videos SET diarization_state='running', diarization_error=NULL, updated_at=now() WHERE id=:v AND id = ANY(:allowed_video_ids)"
        ),
        {"v": row["id"], **_allowlist_params()},
    )
    if result.rowcount != 1:
        raise RuntimeError("diarization claim was no longer scoped to one allowed video")
    return row


def _requeue_stale_running(conn):
    rows = conn.execute(
        text("""
            UPDATE videos
            SET diarization_state='pending',
                diarization_error='Requeued stale running diarization job',
                updated_at=now()
            WHERE diarization_state='running'
              AND now() - updated_at > (:timeout_minutes * interval '1 minute')
              AND id = ANY(:allowed_video_ids)
            RETURNING id
            """),
        {"timeout_minutes": settings.DIARIZATION_RUNNING_TIMEOUT_MINUTES, **_allowlist_params()},
    ).fetchall()
    if rows:
        logger.warning(
            "Requeued stale running diarization jobs",
            extra={"count": len(rows), "timeout_minutes": settings.DIARIZATION_RUNNING_TIMEOUT_MINUTES},
        )


def _heartbeat_diarization_job(engine, video_id) -> bool:
    """Keep a live diarization claim from being recovered as stale."""
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE videos
                SET updated_at=now()
                WHERE id=:v AND diarization_state='running'
                  AND id = ANY(:allowed_video_ids)
                """),
            {"v": video_id, **_allowlist_params()},
        )
    rowcount = result.rowcount
    if not isinstance(rowcount, int) or rowcount not in (0, 1):
        raise RuntimeError("diarization heartbeat affected more than one video")
    return rowcount == 1


def _run_diarization_heartbeat(engine, video_id, stop_event):
    while not stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
        try:
            if not _heartbeat_diarization_job(engine, video_id):
                return
        except Exception:
            logger.warning("Diarization heartbeat failed", extra={"video_id": str(video_id)}, exc_info=True)


def _start_diarization_heartbeat(engine, video_id):
    stop_event = Event()
    thread = Thread(
        target=_run_diarization_heartbeat,
        args=(engine, video_id, stop_event),
        name=f"diarization-heartbeat-{video_id}",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _stop_diarization_heartbeat(stop_event, thread):
    stop_event.set()
    thread.join()


def _skip_unsafe_diarization_jobs(conn):
    """Atomically skip otherwise eligible jobs that could exhaust pyannote memory."""
    rows = conn.execute(
        text("""
            UPDATE videos v
            SET diarization_state = 'skipped',
                diarization_error = 'Skipped diarization: duration_seconds=' ||
                    COALESCE(v.duration_seconds::text, 'NULL') ||
                    ' exceeds DIARIZATION_MAX_DURATION_SECONDS=' || CAST(:max_duration_seconds AS text),
                updated_at = now()
            WHERE v.state = 'completed'
              AND v.wav_path IS NOT NULL
              AND EXISTS (SELECT 1 FROM segments s WHERE s.video_id = v.id)
              AND (
                  v.diarization_state = 'pending'
                  OR (
                      v.diarization_state = 'running'
                      AND now() - v.updated_at > (:timeout_minutes * interval '1 minute')
                  )
              )
              AND v.id = ANY(:allowed_video_ids)
              AND (
                  v.duration_seconds IS NULL
                  OR v.duration_seconds > :max_duration_seconds
              )
            RETURNING v.id, v.duration_seconds
            """),
        {
            "max_duration_seconds": settings.DIARIZATION_MAX_DURATION_SECONDS,
            "timeout_minutes": settings.DIARIZATION_RUNNING_TIMEOUT_MINUTES,
            **_allowlist_params(),
        },
    ).fetchall()
    if rows:
        logger.warning(
            "Skipped diarization jobs exceeding duration limit",
            extra={"count": len(rows), "max_duration_seconds": settings.DIARIZATION_MAX_DURATION_SECONDS},
        )


def process_one(engine) -> bool:
    with engine.begin() as conn:
        _skip_unsafe_diarization_jobs(conn)
        _requeue_stale_running(conn)
        row = _claim_video(conn)
    if not row:
        return False

    video_id = row["id"]
    wav_path = Path(row["wav_path"])
    logger.info("Starting diarization job", extra={"video_id": str(video_id), "wav_path": str(wav_path)})
    heartbeat_stop, heartbeat_thread = _start_diarization_heartbeat(engine, video_id)

    try:
        if not wav_path.exists():
            raise FileNotFoundError(f"WAV file missing for diarization: {wav_path}")
        with engine.begin() as conn:
            segments = _load_segments(conn, video_id)
        logger.info(
            "Loaded transcript segments for diarization",
            extra={"video_id": str(video_id), "segments": len(segments)},
        )
        if not segments:
            with engine.begin() as conn:
                skipped = conn.execute(
                    text(
                        "UPDATE videos SET diarization_state='skipped', updated_at=now() WHERE id=:v AND diarization_state='running' AND id = ANY(:allowed_video_ids)"
                    ),
                    {"v": video_id, **_allowlist_params()},
                )
                if skipped.rowcount != 1:
                    raise RuntimeError("diarization empty-segment skip did not affect the running target video")
            return True

        logger.info(
            "Calling pyannote diarization",
            extra={"video_id": str(video_id), "device": settings.DIARIZATION_DEVICE},
        )
        diar_list, friendly = run_diarization(wav_path)
        logger.info(
            "pyannote diarization returned",
            extra={"video_id": str(video_id), "speaker_regions": len(diar_list), "speakers": len(friendly)},
        )
        with engine.begin() as conn:
            assigned = 0
            for seg in segments:
                mid = (seg["start"] + seg["end"]) / 2.0
                speaker = None
                for start, end, raw_label in diar_list:
                    if start <= mid <= end:
                        speaker = friendly.get(raw_label, raw_label)
                        break
                if speaker:
                    assigned += 1
                result = conn.execute(
                    text("""
                        UPDATE segments SET speaker_label=:spk
                        WHERE id=:sid AND video_id=:v AND video_id = ANY(:allowed_video_ids)
                    """),
                    {"sid": seg["_segment_id"], "v": video_id, "spk": speaker, **_allowlist_params()},
                )
                if result.rowcount != 1:
                    raise RuntimeError("diarization segment update did not affect exactly one target segment")
            if assigned == 0:
                raise RuntimeError("Diarization completed without assigning any speaker labels")
            completed = conn.execute(
                text("""
                    UPDATE videos
                    SET diarization_state='completed', diarization_error=NULL, updated_at=now()
                    WHERE id=:v AND diarization_state='running' AND id = ANY(:allowed_video_ids)
                    """),
                {"v": video_id, **_allowlist_params()},
            )
            if completed.rowcount != 1:
                raise RuntimeError("diarization completion did not affect the running target video")
        logger.info(
            "Diarization job completed",
            extra={"video_id": str(video_id), "speakers_assigned": assigned},
        )
        invalidate_video_data(video_id)
        return True
    except Exception as e:
        logger.exception("Diarization job failed", extra={"video_id": str(video_id), "error": str(e)})
        with engine.begin() as conn:
            failed = conn.execute(
                text("""
                    UPDATE videos
                    SET diarization_state='failed', diarization_error=:e, updated_at=now()
                    WHERE id=:v AND diarization_state='running' AND id = ANY(:allowed_video_ids)
                    """),
                {"v": video_id, "e": str(e)[:5000], **_allowlist_params()},
            )
            if failed.rowcount != 1:
                raise RuntimeError("diarization failure did not affect the running target video")
        return True
    finally:
        _stop_diarization_heartbeat(heartbeat_stop, heartbeat_thread)


def run():
    _require_valid_allowlist()
    if not settings.ENABLE_DIARIZATION:
        logger.warning("ENABLE_DIARIZATION is false; diarization worker will idle")
    engine = create_engine(settings.DATABASE_URL, future=True, pool_pre_ping=True, hide_parameters=True)
    logger.info(
        "Diarization worker started",
        extra={"device": settings.DIARIZATION_DEVICE, "inline": settings.DIARIZATION_INLINE},
    )
    processed_attempts = 0
    while True:
        try:
            did_work = process_one(engine) if settings.ENABLE_DIARIZATION else False
        except Exception as e:
            logger.exception("Diarization worker loop failed", extra={"error": str(e)})
            did_work = False
        if did_work:
            processed_attempts += 1
            if (
                settings.DIARIZATION_MAX_JOBS_PER_PROCESS > 0
                and processed_attempts >= settings.DIARIZATION_MAX_JOBS_PER_PROCESS
            ):
                logger.info(
                    "Diarization worker reached process job limit; exiting for restart",
                    extra={
                        "processed_attempts": processed_attempts,
                        "max_jobs_per_process": settings.DIARIZATION_MAX_JOBS_PER_PROCESS,
                    },
                )
                return
        if not did_work:
            if settings.DIARIZATION_EXIT_WHEN_IDLE:
                logger.info("Diarization worker is idle; exiting by configuration")
                return
            time.sleep(settings.DIARIZATION_POLL_INTERVAL)


def main():
    run()


if __name__ == "__main__":
    main()
