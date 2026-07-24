import inspect
import uuid

from sqlalchemy import text

from worker.loop import pending_video_claim_sql, run, source_cleanup_reconciler
from worker.state_model import (
    OPEN_CAPTION_INGEST_STATES,
    TERMINAL_CAPTION_INGEST_STATES,
    VideoState,
    pending_video_eligibility_sql,
)


def test_pending_video_claim_sql_gates_staged_work_by_the_video_caption_state():
    sql = pending_video_claim_sql()

    assert "j.meta->>'staged' IS DISTINCT FROM 'true'" in sql
    assert "v.caption_ingest_state IN" in sql
    assert "batch_expected_jobs" not in sql


def test_pending_video_claim_sql_does_not_claim_open_caption_work():
    sql = pending_video_claim_sql()

    for state in OPEN_CAPTION_INGEST_STATES:
        assert f"'{state}'" not in sql


def test_pending_video_claim_sql_treats_caption_failures_as_terminal_for_whisper_fallback():
    sql = pending_video_claim_sql()

    for state in TERMINAL_CAPTION_INGEST_STATES:
        assert f"'{state}'" in sql


def test_pending_video_claim_sql_prioritizes_videos_without_youtube_captions():
    sql = pending_video_claim_sql()

    assert "youtube_transcripts" in sql
    assert "yt.video_id = v.id" in sql
    assert "THEN 1" in sql
    assert "ELSE 0" in sql


def test_pending_video_eligibility_excludes_needs_attention_jobs(db_session):
    excluded_job_id = uuid.uuid4()
    eligible_job_id = uuid.uuid4()
    db_session.execute(
        text("INSERT INTO jobs (id, kind, input_url, state) VALUES (:id, 'single', :url, :state)"),
        {"id": excluded_job_id, "url": f"https://youtube.com/watch?v={excluded_job_id}", "state": "needs_attention"},
    )
    db_session.execute(
        text("INSERT INTO jobs (id, kind, input_url) VALUES (:id, 'single', :url)"),
        {"id": eligible_job_id, "url": f"https://youtube.com/watch?v={eligible_job_id}"},
    )
    db_session.execute(
        text("INSERT INTO videos (id, job_id, youtube_id, idx) VALUES (:id, :job_id, :youtube_id, 0)"),
        {"id": uuid.uuid4(), "job_id": excluded_job_id, "youtube_id": f"excluded-{excluded_job_id}"},
    )
    db_session.execute(
        text("INSERT INTO videos (id, job_id, youtube_id, idx) VALUES (:id, :job_id, :youtube_id, 0)"),
        {"id": uuid.uuid4(), "job_id": eligible_job_id, "youtube_id": f"eligible-{eligible_job_id}"},
    )

    eligible_job_ids = set(
        db_session.execute(
            text(f"SELECT j.id {pending_video_eligibility_sql()}"),
            {"pending_state": VideoState.PENDING.value},
        ).scalars()
    )

    assert eligible_job_id in eligible_job_ids
    assert excluded_job_id not in eligible_job_ids


def test_pending_video_eligibility_allows_non_staged_and_terminal_staged_videos(db_session):
    non_staged_job_id = uuid.uuid4()
    staged_job_id = uuid.uuid4()
    open_staged_job_id = uuid.uuid4()
    for job_id, meta in (
        (non_staged_job_id, "{}"),
        (staged_job_id, '{"staged": true}'),
        (open_staged_job_id, '{"staged": true}'),
    ):
        db_session.execute(
            text("INSERT INTO jobs (id, kind, input_url, meta) VALUES (:id, 'single', :url, CAST(:meta AS jsonb))"),
            {"id": job_id, "url": f"https://youtube.com/watch?v={job_id}", "meta": meta},
        )
    for job_id, caption_state in (
        (non_staged_job_id, "pending"),
        (staged_job_id, "completed"),
        (open_staged_job_id, "running"),
    ):
        db_session.execute(
            text(
                "INSERT INTO videos (id, job_id, youtube_id, idx, caption_ingest_state) "
                "VALUES (:id, :job_id, :youtube_id, 0, :caption_state)"
            ),
            {
                "id": uuid.uuid4(),
                "job_id": job_id,
                "youtube_id": f"video-{job_id}",
                "caption_state": caption_state,
            },
        )

    eligible_job_ids = set(
        db_session.execute(
            text(f"SELECT j.id {pending_video_eligibility_sql()}"),
            {"pending_state": VideoState.PENDING.value},
        ).scalars()
    )

    assert non_staged_job_id in eligible_job_ids
    assert staged_job_id in eligible_job_ids
    assert open_staged_job_id not in eligible_job_ids


def test_source_cleanup_runs_in_its_own_daemon_loop_not_the_poll_lane():
    assert "reconcile_pending_source_deletions" not in inspect.getsource(run)
    assert "reconcile_pending_source_deletions" in inspect.getsource(source_cleanup_reconciler)


def test_worker_run_uses_worker_specific_production_validation():
    source = inspect.getsource(run)

    assert "validate_worker_production_settings(settings)" in source
    assert "validate_production_settings" not in source
