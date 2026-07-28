"""Row-fence integration coverage; deliberately opt-in to an isolated database."""

import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.environ.get("HASANARA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="HASANARA_TEST_DATABASE_URL is not configured")


def _insert_video(conn, *, state="pending", marker=None):
    job_id, video_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text("INSERT INTO jobs (id, kind, input_url) VALUES (:id, 'single', :url)"),
        {
            "id": job_id,
            "url": f"https://youtube.com/watch?v={job_id}",
        },
    )
    conn.execute(
        text("""
        INSERT INTO videos (id, job_id, youtube_id, idx, state, wav_path, duration_seconds, diarization_state, diarization_error)
        VALUES (:id, :job_id, :youtube_id, 0, 'completed', '/tmp/canary.wav', 60, :state, :marker)
    """),
        {"id": video_id, "job_id": job_id, "youtube_id": str(video_id), "state": state, "marker": marker},
    )
    conn.execute(
        text(
            "INSERT INTO segments (video_id, start_ms, end_ms, text, speaker_label) VALUES (:id, 0, 1000, 'x', 'old')"
        ),
        {"id": video_id},
    )
    return job_id, video_id


def test_row_fence_transitions_recovery_and_normal_writer_exclusion():
    """Exercise global contention, exact markers, rollback and reset label clearing."""
    engine = sa.create_engine(TEST_DATABASE_URL, poolclass=NullPool)
    token, wrong_token = str(uuid.uuid4()), str(uuid.uuid4())
    job_id = None
    video_id = None
    try:
        with engine.begin() as conn:
            job_id, video_id = _insert_video(conn)
            # The global xact lock is contended while the first transaction owns it.
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('hasanara-diarization-canary'))"))
            with engine.connect() as other:
                assert not other.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext('hasanara-diarization-canary'))")
                ).scalar_one()
            assert (
                conn.execute(
                    text("""
                UPDATE videos SET diarization_state='running', diarization_error='canary-lease:' || :token
                WHERE id=:id AND diarization_state='pending' AND diarization_error IS NULL
            """),
                    {"id": video_id, "token": token},
                ).rowcount
                == 1
            )
            # A normal writer cannot complete a fenced row.
            assert (
                conn.execute(
                    text("""
                UPDATE videos SET state='completed'
                WHERE id=:id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
            """),
                    {"id": video_id},
                ).rowcount
                == 0
            )
            # A wrong-token recovery mutates neither marker nor speakers.
            assert (
                conn.execute(
                    text("""
                UPDATE videos SET diarization_state='pending', diarization_error=NULL
                WHERE id=:id AND diarization_error IN ('canary-lease:' || :token, 'canary-failed:' || :token)
            """),
                    {"id": video_id, "token": wrong_token},
                ).rowcount
                == 0
            )
            assert (
                conn.execute(
                    text("SELECT speaker_label FROM segments WHERE video_id=:id"), {"id": video_id}
                ).scalar_one()
                == "old"
            )
            # Exact finalization succeeds only with labels and is clearable.
            conn.execute(
                text(
                    "UPDATE videos SET diarization_state='completed', diarization_error='canary-finalizing:' || :token WHERE id=:id"
                ),
                {"id": video_id, "token": token},
            )
            assert (
                conn.execute(
                    text("""
                UPDATE videos SET diarization_error=NULL
                WHERE id=:id AND diarization_state='completed' AND diarization_error='canary-finalizing:' || :token
                  AND (SELECT count(DISTINCT speaker_label) FROM segments WHERE video_id=:id AND speaker_label IS NOT NULL) BETWEEN 1 AND 20
            """),
                    {"id": video_id, "token": token},
                ).rowcount
                == 1
            )
            # Lease and failed recovery clears labels before the exact marker is reset.
            for marker, state in ((f"canary-lease:{token}", "running"), (f"canary-failed:{token}", "failed")):
                conn.execute(
                    text("UPDATE videos SET diarization_state=:state, diarization_error=:marker WHERE id=:id"),
                    {"id": video_id, "state": state, "marker": marker},
                )
                conn.execute(text("UPDATE segments SET speaker_label='stale' WHERE video_id=:id"), {"id": video_id})
                assert (
                    conn.execute(
                        text("""
                    WITH target AS (SELECT id FROM videos WHERE id=:id AND diarization_state IN ('running','failed') AND diarization_error IN ('canary-lease:' || :token, 'canary-failed:' || :token) FOR UPDATE),
                    cleared AS (UPDATE segments s SET speaker_label=NULL FROM target t WHERE s.video_id=t.id),
                    reset AS (UPDATE videos v SET diarization_state='pending', diarization_error=NULL FROM target t WHERE v.id=t.id RETURNING v.id)
                    SELECT count(*) FROM reset
                """),
                        {"id": video_id, "token": token},
                    ).scalar_one()
                    == 1
                )
                assert (
                    conn.execute(
                        text("SELECT speaker_label FROM segments WHERE video_id=:id"), {"id": video_id}
                    ).scalar_one()
                    is None
                )
    finally:
        if job_id:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": job_id})
                conn.execute(text("DELETE FROM search_index_outbox WHERE video_id=:id"), {"id": video_id})
        engine.dispose()
