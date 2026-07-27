import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import text

from worker import diarization_loop


def _insert_diarization_video(db_session, *, duration, diarization_state="pending", stale=False):
    job_id = uuid.uuid4()
    video_id = uuid.uuid4()
    db_session.execute(
        text("INSERT INTO jobs (id, kind, input_url) VALUES (:id, 'single', :url)"),
        {"id": job_id, "url": f"https://youtube.com/watch?v={job_id}"},
    )
    db_session.execute(
        text("""
            INSERT INTO videos (id, job_id, youtube_id, idx, state, wav_path, duration_seconds, diarization_state, updated_at)
            VALUES (:id, :job_id, :youtube_id, 0, 'completed', '/tmp/audio.wav', :duration, :diarization_state,
                    now() - CASE WHEN :stale THEN interval '10 minutes' ELSE interval '0 minutes' END)
            """),
        {
            "id": video_id,
            "job_id": job_id,
            "youtube_id": str(video_id),
            "duration": duration,
            "diarization_state": diarization_state,
            "stale": stale,
        },
    )
    db_session.execute(
        text("INSERT INTO segments (video_id, start_ms, end_ms, text) VALUES (:video_id, 0, 1000, 'segment')"),
        {"video_id": video_id},
    )
    return video_id


def test_unsafe_diarization_jobs_are_skipped_and_duration_boundary_is_claimable(db_session, monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_MAX_DURATION_SECONDS", 10)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_RUNNING_TIMEOUT_MINUTES", 5)
    pending_over_limit = _insert_diarization_video(db_session, duration=11)
    stale_running_over_limit = _insert_diarization_video(
        db_session, duration=11, diarization_state="running", stale=True
    )
    null_duration = _insert_diarization_video(db_session, duration=None)
    at_limit = _insert_diarization_video(db_session, duration=10)
    monkeypatch.setattr(
        diarization_loop.settings,
        "DIARIZATION_ALLOWED_VIDEO_IDS",
        frozenset({pending_over_limit, stale_running_over_limit, null_duration, at_limit}),
    )

    diarization_loop._skip_unsafe_diarization_jobs(db_session)

    states = dict(
        db_session.execute(
            text("SELECT id, diarization_state FROM videos WHERE id = ANY(:ids)"),
            {"ids": [pending_over_limit, stale_running_over_limit, null_duration, at_limit]},
        ).all()
    )
    assert states[pending_over_limit] == "skipped"
    assert states[stale_running_over_limit] == "skipped"
    assert states[null_duration] == "skipped"
    assert states[at_limit] == "pending"

    error = db_session.execute(
        text("SELECT diarization_error FROM videos WHERE id = :id"), {"id": null_duration}
    ).scalar_one()
    assert error == "Skipped diarization: duration_seconds=NULL exceeds DIARIZATION_MAX_DURATION_SECONDS=10"
    assert diarization_loop._claim_video(db_session)["id"] == at_limit


def test_disallowed_jobs_are_never_skipped_requeued_or_claimed(db_session, monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_MAX_DURATION_SECONDS", 10)
    allowed = _insert_diarization_video(db_session, duration=10)
    disallowed_eligible = _insert_diarization_video(db_session, duration=10)
    disallowed_oversized = _insert_diarization_video(db_session, duration=11)
    disallowed_stale = _insert_diarization_video(db_session, duration=10, diarization_state="running", stale=True)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset({allowed}))

    diarization_loop._skip_unsafe_diarization_jobs(db_session)
    diarization_loop._requeue_stale_running(db_session)

    states = dict(
        db_session.execute(
            text("SELECT id, diarization_state FROM videos WHERE id = ANY(:ids)"),
            {"ids": [allowed, disallowed_eligible, disallowed_oversized, disallowed_stale]},
        ).all()
    )
    assert states[disallowed_eligible] == "pending"
    assert states[disallowed_oversized] == "pending"
    assert states[disallowed_stale] == "running"
    assert diarization_loop._claim_video(db_session)["id"] == allowed


def test_run_exits_after_configured_number_of_processed_attempts(monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "ENABLE_DIARIZATION", True)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_MAX_JOBS_PER_PROCESS", 1)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset({uuid.uuid4()}))
    engine = Mock()

    with (
        patch.object(diarization_loop, "create_engine", return_value=engine),
        patch.object(diarization_loop, "process_one", return_value=True) as process_one,
        patch.object(diarization_loop.time, "sleep") as sleep,
    ):
        diarization_loop.run()

    process_one.assert_called_once_with(engine)
    sleep.assert_not_called()


def test_run_hides_database_parameters_in_sqlalchemy_engine(monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "ENABLE_DIARIZATION", True)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset({uuid.uuid4()}))
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_EXIT_WHEN_IDLE", True)
    with patch.object(diarization_loop, "create_engine", return_value=Mock()) as create_engine:
        with patch.object(diarization_loop, "process_one", return_value=False):
            diarization_loop.run()
    assert create_engine.call_args.kwargs["hide_parameters"] is True


def test_run_requires_nonempty_allowlist_before_opening_database(monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_REQUIRE_ALLOWLIST", True)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset())
    with patch.object(diarization_loop, "create_engine") as create_engine:
        import pytest

        with pytest.raises(ValueError, match="requires"):
            diarization_loop.run()
    create_engine.assert_not_called()


def test_run_exits_immediately_when_bounded_worker_is_idle(monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "ENABLE_DIARIZATION", True)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset({uuid.uuid4()}))
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_EXIT_WHEN_IDLE", True)
    engine = Mock()
    with (
        patch.object(diarization_loop, "create_engine", return_value=engine),
        patch.object(diarization_loop, "process_one", return_value=False),
        patch.object(diarization_loop.time, "sleep") as sleep,
    ):
        diarization_loop.run()
    sleep.assert_not_called()


def test_standalone_worker_never_idles_without_an_allowlist(monkeypatch):
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_REQUIRE_ALLOWLIST", False)
    monkeypatch.setattr(diarization_loop.settings, "DIARIZATION_ALLOWED_VIDEO_IDS", frozenset())
    with patch.object(diarization_loop, "create_engine") as create_engine:
        import pytest

        with pytest.raises(ValueError, match="requires"):
            diarization_loop.run()
    create_engine.assert_not_called()


def test_diarization_heartbeat_query_is_guarded_by_running_state():
    conn = Mock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    conn.execute.return_value.rowcount = 1

    assert diarization_loop._heartbeat_diarization_job(engine, "video-id") is True

    statement, params = conn.execute.call_args.args
    assert "WHERE id=:v AND diarization_state='running'" in str(statement)
    assert "id = ANY(:allowed_video_ids)" in str(statement)
    assert params == {"v": "video-id", "allowed_video_ids": []}


def test_diarization_heartbeat_reports_no_mutation_for_disallowed_or_nonrunning_video():
    conn = Mock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    conn.execute.return_value.rowcount = 0

    assert diarization_loop._heartbeat_diarization_job(engine, "video-id") is False


def test_diarization_heartbeat_rejects_unexpected_rowcount():
    conn = Mock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    conn.execute.return_value.rowcount = 2

    import pytest

    with pytest.raises(RuntimeError, match="more than one"):
        diarization_loop._heartbeat_diarization_job(engine, "video-id")


def test_final_video_mutations_require_a_running_allowed_target():
    source = Path(diarization_loop.__file__).read_text(encoding="utf-8")
    empty_skip = source.split("if not segments:", 1)[1].split("return True", 1)[0]
    failure = source.split("except Exception as e:", 1)[1].split("return True", 1)[0]

    assert "diarization_state='running'" in empty_skip
    assert "skipped.rowcount != 1" in empty_skip
    assert "diarization_state='running'" in failure
    assert "failed.rowcount != 1" in failure


def test_diarization_heartbeat_lifecycle_starts_and_stops_without_waiting():
    engine = Mock()
    with patch.object(diarization_loop, "Thread") as thread_type:
        thread = thread_type.return_value
        stop_event, started_thread = diarization_loop._start_diarization_heartbeat(engine, "video-id")
        diarization_loop._stop_diarization_heartbeat(stop_event, started_thread)

    thread.start.assert_called_once()
    assert stop_event.is_set()
    thread.join.assert_called_once_with()
