import uuid

from sqlalchemy import text

from worker.job_lifecycle import claim_job_attempt, finish_job_attempt, renew_job_lease


def _job(db_session):
    job_id = uuid.uuid4()
    db_session.execute(
        text("INSERT INTO jobs (id, kind, input_url) VALUES (:id, 'single', :url)"),
        {"id": job_id, "url": f"https://youtube.com/watch?v={job_id}"},
    )
    db_session.commit()
    return job_id


def test_attempt_lease_is_owned_and_finalized_once(db_session):
    job_id = _job(db_session)
    lease = claim_job_attempt(db_session, job_id=job_id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    assert renew_job_lease(db_session, lease, lease_seconds=60)
    assert finish_job_attempt(db_session, lease, outcome="completed", max_attempts=3)
    assert not finish_job_attempt(db_session, lease, outcome="failed", error="late", max_attempts=3)


def test_exhausted_attempt_moves_job_to_needs_attention(db_session):
    job_id = _job(db_session)
    for expected in range(1, 4):
        db_session.execute(text("UPDATE jobs SET next_attempt_at=NULL WHERE id=:id"), {"id": job_id})
        lease = claim_job_attempt(db_session, job_id=job_id, worker_id="worker-a", lease_seconds=30)
        assert lease is not None
        assert lease.attempt_number == expected
        assert finish_job_attempt(db_session, lease, outcome="failed", error="boom", max_attempts=3)

    row = (
        db_session.execute(
            text("SELECT state, stage, attempt_count, last_failure_summary FROM jobs WHERE id=:id"),
            {"id": job_id},
        )
        .mappings()
        .one()
    )
    assert row == {
        "state": "needs_attention",
        "stage": "needs_attention",
        "attempt_count": 3,
        "last_failure_summary": "boom",
    }
    assert claim_job_attempt(db_session, job_id=job_id, worker_id="worker-b", lease_seconds=30) is None


def test_failed_attempt_sets_exponential_retry_window(db_session):
    job_id = _job(db_session)
    lease = claim_job_attempt(db_session, job_id=job_id, worker_id="worker-a", lease_seconds=30)
    assert lease is not None
    assert finish_job_attempt(db_session, lease, outcome="failed", error="temporary", max_attempts=3)
    row = (
        db_session.execute(
            text("SELECT state, stage, next_attempt_at > now() AS delayed FROM jobs WHERE id=:id"), {"id": job_id}
        )
        .mappings()
        .one()
    )
    assert row == {"state": "pending", "stage": "retry_wait", "delayed": True}


def test_cancelled_job_cannot_be_claimed(db_session):
    job_id = _job(db_session)
    db_session.execute(text("UPDATE jobs SET cancellation_requested_at=now() WHERE id=:id"), {"id": job_id})
    assert claim_job_attempt(db_session, job_id=job_id, worker_id="worker-a", lease_seconds=30) is None
