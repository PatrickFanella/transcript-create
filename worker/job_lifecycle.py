"""Durable worker leases and compare-and-set attempt finalization."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread

from sqlalchemy import text


@dataclass(frozen=True)
class JobLease:
    attempt_id: uuid.UUID
    job_id: uuid.UUID
    attempt_number: int
    worker_id: str


def claim_job_attempt(conn, *, job_id: uuid.UUID, worker_id: str, lease_seconds: int) -> JobLease | None:
    """Claim a cancellable job and create its next durable attempt atomically."""
    job = (
        conn.execute(
            text("""
                SELECT id, attempt_count
                FROM jobs
                WHERE id=:job_id
                  AND cancellation_requested_at IS NULL
                  AND quarantined_at IS NULL
                  AND state <> 'needs_attention'
                FOR UPDATE
            """),
            {"job_id": job_id},
        )
        .mappings()
        .first()
    )
    if not job:
        return None
    attempt_number = int(job["attempt_count"] or 0) + 1
    attempt = conn.execute(
        text("""
                INSERT INTO job_attempts (
                    job_id, attempt_number, worker_id, lease_expires_at
                ) VALUES (
                    :job_id, :attempt_number, :worker_id,
                    now() + make_interval(secs => :lease_seconds)
                ) RETURNING id
            """),
        {
            "job_id": job_id,
            "attempt_number": attempt_number,
            "worker_id": worker_id,
            "lease_seconds": lease_seconds,
        },
    ).scalar_one()
    conn.execute(
        text("""
            UPDATE jobs SET attempt_count=:attempt_number, heartbeat_at=now(),
                stage='processing', updated_at=now()
            WHERE id=:job_id
        """),
        {"job_id": job_id, "attempt_number": attempt_number},
    )
    return JobLease(uuid.UUID(str(attempt)), uuid.UUID(str(job_id)), attempt_number, worker_id)


def renew_job_lease(conn, lease: JobLease, *, lease_seconds: int) -> bool:
    """Renew only an unfinished attempt still owned by this worker."""
    renewed = conn.execute(
        text("""
            UPDATE job_attempts SET lease_expires_at=now() + make_interval(secs => :lease_seconds)
            WHERE id=:attempt_id AND worker_id=:worker_id AND finished_at IS NULL
            RETURNING job_id
        """),
        {
            "attempt_id": lease.attempt_id,
            "worker_id": lease.worker_id,
            "lease_seconds": lease_seconds,
        },
    ).first()
    if not renewed:
        return False
    conn.execute(text("UPDATE jobs SET heartbeat_at=now() WHERE id=:job_id"), {"job_id": lease.job_id})
    return True


def finish_job_attempt(
    conn,
    lease: JobLease,
    *,
    outcome: str,
    error: str | None = None,
    max_attempts: int,
) -> bool:
    """Finalize once; stale or lost owners cannot overwrite the attempt."""
    finished = conn.execute(
        text("""
            UPDATE job_attempts SET finished_at=now(), outcome=:outcome, error=:error
            WHERE id=:attempt_id AND worker_id=:worker_id AND finished_at IS NULL
            RETURNING job_id
        """),
        {
            "attempt_id": lease.attempt_id,
            "worker_id": lease.worker_id,
            "outcome": outcome,
            "error": error[:5000] if error else None,
        },
    ).first()
    if not finished:
        return False
    if outcome == "failed":
        conn.execute(
            text("""
                UPDATE jobs SET
                    state=CASE WHEN attempt_count >= :max_attempts
                        THEN 'needs_attention'::job_state ELSE 'pending'::job_state END,
                    stage=CASE WHEN attempt_count >= :max_attempts
                        THEN 'needs_attention' ELSE 'retry_wait' END,
                    next_attempt_at=CASE WHEN attempt_count >= :max_attempts THEN NULL
                        ELSE now() + make_interval(secs => LEAST(3600, 30 * power(2, attempt_count - 1))::int) END,
                    last_failure_summary=:error, updated_at=now()
                WHERE id=:job_id
            """),
            {"job_id": lease.job_id, "max_attempts": max_attempts, "error": error[:1000] if error else None},
        )
    else:
        conn.execute(
            text("""
                UPDATE jobs SET heartbeat_at=now(),
                    cancelled_at=CASE WHEN cancellation_requested_at IS NOT NULL THEN now() ELSE cancelled_at END,
                    stage=CASE WHEN cancellation_requested_at IS NOT NULL THEN 'cancelled' ELSE 'finalizing' END,
                    next_attempt_at=NULL, updated_at=now() WHERE id=:job_id
            """),
            {"job_id": lease.job_id},
        )
    return True


@contextmanager
def maintain_job_lease(engine, lease: JobLease, *, lease_seconds: int):
    """Renew a lease in a side transaction while network/GPU work runs."""
    stopped = Event()

    def heartbeat() -> None:
        interval = max(1, lease_seconds // 3)
        while not stopped.wait(interval):
            with engine.begin() as conn:
                if not renew_job_lease(conn, lease, lease_seconds=lease_seconds):
                    stopped.set()
                    return

    thread = Thread(target=heartbeat, name=f"job-lease-{lease.attempt_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)
