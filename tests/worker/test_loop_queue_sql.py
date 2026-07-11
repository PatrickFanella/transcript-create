from worker.loop import pending_video_claim_sql
from worker.state_model import OPEN_CAPTION_INGEST_STATES, TERMINAL_CAPTION_INGEST_STATES


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
