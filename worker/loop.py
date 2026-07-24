import os
import socket
import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Thread

from prometheus_client import start_http_server
from sqlalchemy import create_engine, text

from app.logging_config import configure_logging, get_logger, video_id_ctx
from app.settings import settings, validate_worker_production_settings
from app.source_deletion import reconcile_pending_source_deletions
from app.ytdlp_validation import validate_js_runtime_or_exit
from worker.caption_ingest import ingest_available_captions
from worker.job_lifecycle import claim_job_attempt, finish_job_attempt, maintain_job_lease
from worker.metrics import setup_worker_info, try_collect_gpu_metrics
from worker.pipeline import expand_channel_if_needed
from worker.state_model import (
    IN_PROGRESS_VIDEO_STATES,
    OPEN_CAPTION_INGEST_STATES,
    VideoState,
    pending_video_eligibility_sql,
    sql_string_list,
)
from worker.video_pipeline import ProcessVideoCommand, default_video_processing_pipeline

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
POLL_INTERVAL = 3
HEARTBEAT_INTERVAL = 60  # seconds
youtube_caption_cooldown_until = 0.0
SOURCE_CLEANUP_INTERVAL = 60
SOURCE_CLEANUP_BATCH_SIZE = 10
IN_PROGRESS_VIDEO_STATES_SQL = sql_string_list(IN_PROGRESS_VIDEO_STATES)
OPEN_CAPTION_INGEST_STATES_SQL = sql_string_list(OPEN_CAPTION_INGEST_STATES)

# Configure structured logging for worker service
configure_logging(
    service="worker",
    level=settings.LOG_LEVEL,
    json_format=(settings.LOG_FORMAT == "json"),
)
logger = get_logger(__name__)
video_processing_pipeline = default_video_processing_pipeline(engine)

# Generate a unique worker ID based on hostname and PID
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def update_heartbeat():
    """Update worker heartbeat in database."""
    try:
        with engine.begin() as conn:
            # Upsert heartbeat record
            conn.execute(
                text("""
                    INSERT INTO worker_heartbeat (worker_id, hostname, pid, last_seen, metrics)
                    VALUES (:worker_id, :hostname, :pid, now(), :metrics)
                    ON CONFLICT (worker_id)
                    DO UPDATE SET
                        last_seen = now(),
                        hostname = EXCLUDED.hostname,
                        pid = EXCLUDED.pid,
                        metrics = EXCLUDED.metrics
                """),
                {
                    "worker_id": WORKER_ID,
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "metrics": "{}",  # Can be extended with additional metrics
                },
            )
            logger.debug("Worker heartbeat updated", extra={"worker_id": WORKER_ID})
    except Exception as e:
        logger.warning("Failed to update worker heartbeat", extra={"error": str(e)})


def update_queue_metrics():
    """Update queue metrics from database."""
    from worker.metrics import videos_in_progress, videos_pending

    try:
        with engine.begin() as conn:
            # Count pending videos
            pending_count = conn.execute(
                text(f"SELECT COUNT(*) {pending_video_eligibility_sql()}"),
                {"pending_state": VideoState.PENDING.value},
            ).scalar_one()
            videos_pending.set(pending_count)

            # Count in-progress videos by state
            states = conn.execute(text(f"""
                    SELECT state, COUNT(*)
                    FROM videos
                    WHERE state IN ({IN_PROGRESS_VIDEO_STATES_SQL})
                    GROUP BY state
                """)).all()

            # Reset all in-progress gauges first
            for state in IN_PROGRESS_VIDEO_STATES:
                videos_in_progress.labels(state=state).set(0)

            # Set current counts
            for state, count in states:
                videos_in_progress.labels(state=state).set(count)
    except Exception as e:
        logger.warning("Failed to update queue metrics", extra={"error": str(e)})


def gpu_metrics_collector():
    """Background thread to periodically collect GPU metrics."""
    while True:
        try:
            try_collect_gpu_metrics()
        except Exception as e:
            logger.debug("GPU metrics collection failed", extra={"error": str(e)})
        time.sleep(30)  # Update every 30 seconds


def heartbeat_updater():
    """Background thread to periodically update worker heartbeat."""
    while True:
        try:
            update_heartbeat()
        except Exception as e:
            logger.debug("Heartbeat update failed", extra={"error": str(e)})
        time.sleep(HEARTBEAT_INTERVAL)


def source_cleanup_reconciler():
    """Keep cleanup I/O off the transcription polling lane."""
    while True:
        try:
            reconcile_pending_source_deletions(limit=SOURCE_CLEANUP_BATCH_SIZE)
        except Exception as exc:
            logger.warning("Source deletion reconciliation failed", extra={"error_type": type(exc).__name__})
        time.sleep(SOURCE_CLEANUP_INTERVAL)


def pending_video_claim_sql() -> str:
    """SQL for claiming native transcription work.

    Staged batches run in rolling captions-first mode: each individual video is
    eligible for native Whisper as soon as its caption ingest state is terminal.
    Caption failures are terminal by design here: after the staged caption pass
    has exhausted a video, native Whisper may proceed as the fallback while the
    rest of the batch continues caption ingestion.
    """
    return f"""
                SELECT v.id, j.id AS job_id
                {pending_video_eligibility_sql()}
                ORDER BY
                  CASE
                    WHEN EXISTS (SELECT 1 FROM youtube_transcripts yt WHERE yt.video_id = v.id) THEN 1
                    ELSE 0
                  END ASC,
                  v.idx ASC NULLS LAST,
                  v.created_at DESC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """


def process_claimed_video(video_id, lease) -> None:
    """Process one claimed video outside the queue-claim transaction."""
    video_id_ctx.set(str(video_id))
    try:
        logger.info("Starting video processing")
        with maintain_job_lease(engine, lease, lease_seconds=settings.JOB_LEASE_SECONDS):
            video_processing_pipeline.process_video(ProcessVideoCommand(video_id=video_id))
        with engine.begin() as conn:
            finish_job_attempt(conn, lease, outcome="completed", max_attempts=settings.MAX_JOB_ATTEMPTS)
        logger.info("Video processing completed successfully")
        from worker.metrics import videos_processed_total

        videos_processed_total.labels(result="completed").inc()
    except Exception as e:
        logger.exception("Video processing failed", extra={"error": str(e)})
        from worker.metrics import videos_processed_total

        videos_processed_total.labels(result="failed").inc()
        with engine.begin() as conn:
            if lease.attempt_number < settings.MAX_JOB_ATTEMPTS:
                conn.execute(
                    text("UPDATE videos SET state='pending', error=:e, updated_at=now() WHERE id=:i"),
                    {"i": video_id, "e": str(e)[:5000]},
                )
            else:
                row = conn.execute(
                    text(
                        "UPDATE videos SET state='failed', error=:e, updated_at=now() " "WHERE id=:i RETURNING job_id"
                    ),
                    {"i": video_id, "e": str(e)[:5000]},
                ).first()
                if row:
                    from worker.pipeline import refresh_job_state

                    refresh_job_state(conn, row[0], error=str(e))
            finish_job_attempt(
                conn,
                lease,
                outcome="failed",
                error=str(e),
                max_attempts=settings.MAX_JOB_ATTEMPTS,
            )
    finally:
        video_id_ctx.set(None)


def run():
    validate_worker_production_settings(settings)

    # Validate JavaScript runtime for yt-dlp before starting worker
    validate_js_runtime_or_exit()

    logger.info("Worker service started", extra={"worker_id": WORKER_ID})

    # Initialize PO token manager with default providers
    from worker.po_token_manager import get_token_manager
    from worker.po_token_providers import initialize_default_providers

    token_manager = get_token_manager()
    providers = initialize_default_providers()
    for provider in providers:
        token_manager.add_provider(provider)
    logger.info("PO token manager initialized", extra={"provider_count": len(providers)})

    # Initialize worker info metrics
    setup_worker_info(
        whisper_model=settings.WHISPER_MODEL,
        whisper_backend=settings.WHISPER_BACKEND,
        force_gpu=settings.FORCE_GPU,
    )

    # Start Prometheus metrics HTTP server on port 8001
    try:
        start_http_server(8001)
        logger.info("Prometheus metrics server started", extra={"port": 8001})
    except Exception as e:
        logger.warning("Failed to start metrics server", extra={"error": str(e)})

    # Start GPU metrics collector thread
    gpu_thread = Thread(target=gpu_metrics_collector, daemon=True)
    gpu_thread.start()

    # Start heartbeat updater thread
    heartbeat_thread = Thread(target=heartbeat_updater, daemon=True)
    heartbeat_thread.start()
    logger.info("Worker heartbeat thread started")

    cleanup_thread = Thread(target=source_cleanup_reconciler, daemon=True)
    cleanup_thread.start()
    logger.info("Source cleanup reconciliation thread started")

    # Initial heartbeat
    update_heartbeat()

    max_parallel = max(1, settings.MAX_PARALLEL_JOBS)
    executor = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="video-job")
    active: set[Future] = set()

    while True:
        active = {future for future in active if not future.done()}
        if len(active) >= max_parallel:
            time.sleep(POLL_INTERVAL)
            continue
        logger.debug("Polling for work: expand jobs and pick a video")
        with engine.begin() as conn:
            # Expand pending jobs into videos
            from worker.pipeline import expand_single_if_needed

            try:
                expand_single_if_needed(conn)
                expand_channel_if_needed(conn)
            except Exception as e:
                logger.exception("Error expanding jobs", extra={"error": str(e)})

            try:
                from worker.pipeline import reconcile_terminal_jobs

                reconcile_terminal_jobs(conn)
            except Exception as e:
                logger.warning("Job lifecycle reconciliation failed", extra={"error": str(e)})

            # Opportunistically capture YouTube captions early for videos without captions yet
            try:
                global youtube_caption_cooldown_until
                now = time.time()
                if now >= youtube_caption_cooldown_until:
                    ingest_result = ingest_available_captions(conn, limit=10)
                    if ingest_result.rate_limited:
                        youtube_caption_cooldown_until = time.time() + (ingest_result.cooldown_seconds or 0)
                        logger.warning(
                            "YouTube caption ingest rate-limited; entering cooldown",
                            extra={
                                "cooldown_seconds": ingest_result.cooldown_seconds,
                                "cooldown_until": youtube_caption_cooldown_until,
                            },
                        )
                    else:
                        from worker.pipeline import promote_staged_batches_if_ready

                        promote_staged_batches_if_ready(conn)
                else:
                    logger.info(
                        "Skipping YouTube caption ingest during cooldown",
                        extra={"cooldown_remaining_seconds": round(youtube_caption_cooldown_until - now)},
                    )
            except Exception as e:
                logger.warning("YouTube captions capture step failed", extra={"error": str(e)})

            # Rescue stuck videos: if a video has been in a non-terminal state for too long, mark it pending again
            try:
                rescue_seconds = int(getattr(settings, "RESCUE_STUCK_AFTER_SECONDS", 0) or 0)
                if rescue_seconds > 0:
                    logger.debug("Rescue check: requeue videos stuck", extra={"threshold_seconds": rescue_seconds})
                    conn.execute(
                        text("""
                        UPDATE videos
                        SET state = 'pending', updated_at = now()
                        WHERE state IN ('downloading','transcoding','transcribing')
                          AND now() - updated_at > make_interval(secs => :secs)
                        RETURNING id
                        """),
                        {"secs": rescue_seconds},
                    )
            except Exception as e:
                logger.warning("Rescue check failed", extra={"error": str(e)})

            # Model upgrade requeue: reprocess completed videos if current model is larger/better
            try:
                current_model = settings.WHISPER_MODEL
                model_hierarchy = {
                    "tiny": 1,
                    "base": 2,
                    "small": 3,
                    "medium": 4,
                    "large": 5,
                    "large-v2": 6,
                    "large-v3": 7,
                }
                current_rank = model_hierarchy.get(current_model, 0)
                if current_rank > 0:
                    requeue_result = conn.execute(
                        text("""
                        UPDATE videos v
                        SET state = 'pending', updated_at = now()
                        FROM transcripts t
                        WHERE v.id = t.video_id
                          AND v.state = :completed_state
                          AND t.model IS NOT NULL
                          AND (
                            -- Requeue if transcript model rank is lower than current
                            CASE
                              WHEN t.model = 'tiny' THEN 1
                              WHEN t.model = 'base' THEN 2
                              WHEN t.model = 'small' THEN 3
                              WHEN t.model = 'medium' THEN 4
                              WHEN t.model = 'large' THEN 5
                              WHEN t.model = 'large-v2' THEN 6
                              WHEN t.model = 'large-v3' THEN 7
                              ELSE 0
                            END
                          ) < :current_rank
                        RETURNING v.id, t.model
                    """),
                        {"current_rank": current_rank, "completed_state": VideoState.COMPLETED.value},
                    )
                    requeued = requeue_result.fetchall()
                    if requeued:
                        logger.info(
                            "Model upgrade requeue",
                            extra={
                                "count": len(requeued),
                                "from_models": ", ".join(set(r[1] for r in requeued)),
                                "to_model": current_model,
                            },
                        )
            except Exception as e:
                logger.warning("Model upgrade requeue failed", extra={"error": str(e)})

            # Update queue metrics
            update_queue_metrics()

            row = conn.execute(
                text(pending_video_claim_sql()),
                {"pending_state": VideoState.PENDING.value},
            ).first()
            if not row:
                logger.debug("No pending videos found. Sleeping", extra={"sleep_seconds": POLL_INTERVAL})
                time.sleep(POLL_INTERVAL)
                continue
            video_id, job_id = row
            lease = claim_job_attempt(
                conn,
                job_id=job_id,
                worker_id=WORKER_ID,
                lease_seconds=settings.JOB_LEASE_SECONDS,
            )
            if not lease:
                continue

            logger.info("Picked video for processing")
            conn.execute(text("UPDATE videos SET state='downloading', updated_at=now() WHERE id=:i"), {"i": video_id})
        active.add(executor.submit(process_claimed_video, video_id, lease))


def main():
    run()


if __name__ == "__main__":
    main()
