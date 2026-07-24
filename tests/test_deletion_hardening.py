"""Transaction boundaries for account deletion and deferred source cleanup."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

from app.accounts import (
    _prepared_deletions,
    delete_prepared_account,
    discard_prepared_account_deletion,
    prepare_account_deletion,
)


def _tombstone(db, *, video_id=None, youtube_id=None):
    video_id = video_id or uuid.uuid4()
    youtube_id = youtube_id or f"cleanup-{video_id}"
    tombstone_id = db.execute(text("""
        INSERT INTO source_deletions
            (video_id, youtube_id, backup_exclusion_until, raw_path, wav_path)
        VALUES (:video_id, :youtube_id, now(), '/tmp/raw', '/tmp/wav') RETURNING id
    """), {"video_id": str(video_id), "youtube_id": youtube_id}).scalar_one()
    return tombstone_id, video_id


def test_prepared_deletion_capability_is_session_and_transaction_bound(db_session, test_engine):
    user_id = uuid.uuid4()
    db_session.execute(text("INSERT INTO users (id, role) VALUES (:id, 'user')"), {"id": str(user_id)})
    prepared = prepare_account_deletion(db_session, user_id)

    with pytest.raises(RuntimeError):
        delete_prepared_account(db_session, object())
    with test_engine.connect() as connection:
        other = connection.begin()
        from app.db import SessionLocal
        other_session = SessionLocal(bind=connection)
        try:
            with pytest.raises(RuntimeError):
                delete_prepared_account(other_session, prepared)
        finally:
            other_session.close()
            other.rollback()

    delete_prepared_account(db_session, prepared)
    with pytest.raises(RuntimeError):
        delete_prepared_account(db_session, prepared)


def test_prepared_deletion_rejects_savepoints_and_can_be_discarded(db_session):
    user_id = uuid.uuid4()
    db_session.execute(text("INSERT INTO users (id, role) VALUES (:id, 'user')"), {"id": str(user_id)})
    baseline = len(_prepared_deletions)
    with db_session.begin_nested():
        with pytest.raises(RuntimeError, match="savepoint"):
            prepare_account_deletion(db_session, user_id)
    prepared = prepare_account_deletion(db_session, user_id)
    assert len(_prepared_deletions) == baseline + 1
    discard_prepared_account_deletion(prepared)
    assert len(_prepared_deletions) == baseline
    with pytest.raises(RuntimeError):
        delete_prepared_account(db_session, prepared)


def test_prepared_deletion_locks_ownership_updates_until_commit(test_engine):
    """Separate connections prove the locks prevent stale owner UUID/JSON writes."""
    from app.db import SessionLocal

    user_id, job_id = uuid.uuid4(), uuid.uuid4()
    with test_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, role) VALUES (:id, 'user')"), {"id": str(user_id)})
        connection.execute(text("INSERT INTO jobs (id, kind, input_url, owner_user_id, meta) VALUES (:job, 'single', 'https://example.test', :user, CAST(:meta AS jsonb))"), {"job": str(job_id), "user": str(user_id), "meta": '{"owner_user_id": "' + str(user_id) + '"}'})
        tombstone_id, _ = _tombstone(connection, youtube_id=f"lock-{user_id}")

    prepared_ready, release = Event(), Event()

    def delete_in_a():
        with SessionLocal(bind=test_engine) as session:
            prepared = prepare_account_deletion(session, user_id)
            prepared_ready.set()
            assert release.wait(5)
            delete_prepared_account(session, prepared)
            session.commit()

    def stale_update_in_b():
        assert prepared_ready.wait(5)
        with SessionLocal(bind=test_engine) as session:
            try:
                session.execute(text("UPDATE jobs SET owner_user_id=:user, meta=CAST(:meta AS jsonb) WHERE id=:job"), {"user": str(user_id), "job": str(job_id), "meta": '{"owner_user_id": "' + str(user_id) + '"}'})
                session.execute(text("UPDATE source_deletions SET owner_user_id=:user WHERE id=:id"), {"user": str(user_id), "id": str(tombstone_id)})
                session.commit()
                return "committed"
            except Exception:
                session.rollback()
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        deletion = executor.submit(delete_in_a)
        update = executor.submit(stale_update_in_b)
        assert prepared_ready.wait(5)
        assert not update.done(), "ownership update committed while deletion locks were held"
        release.set()
        deletion.result(timeout=10)
        assert update.result(timeout=10) == "rejected"
    with test_engine.connect() as connection:
        job = connection.execute(text("SELECT owner_user_id, meta FROM jobs WHERE id=:id"), {"id": str(job_id)}).mappings().one()
        tombstone = connection.execute(text("SELECT owner_user_id FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).scalar_one()
        assert job == {"owner_user_id": None, "meta": {}}
        assert tombstone is None


def test_cleanup_records_independent_failures_and_reconciles(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    with test_engine.begin() as connection:
        tombstone_id, video_id = _tombstone(connection)

    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))
    monkeypatch.setattr(source_deletion.settings, "SEARCH_BACKEND", "opensearch")
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda _id, strict=False: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(source_deletion, "_delete_file", lambda _path: None)
    monkeypatch.setattr(source_deletion, "_delete_index_documents", lambda _id, _index: (_ for _ in ()).throw(ValueError()))
    source_deletion.cleanup_deleted_source({"id": video_id, "tombstone_id": tombstone_id, "raw_path": "/tmp/raw", "wav_path": "/tmp/wav"})
    with test_engine.connect() as connection:
        row = connection.execute(text("SELECT cleanup_status, cleanup_attempts, cleanup_error FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).mappings().one()
        assert row["cleanup_status"] == "pending"
        assert row["cleanup_attempts"] == 1
        assert row["cleanup_error"] == "cache:RuntimeError;search_index:ValueError;search_index:ValueError"
        assert connection.execute(text("SELECT cleanup_next_attempt_at > now() FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).scalar_one()

    with test_engine.begin() as connection:
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_until=now() + interval '5 minutes'
            WHERE cleanup_status='pending' AND id != :id
        """), {"id": str(tombstone_id)})
    assert source_deletion.reconcile_pending_source_deletions(limit=1, lease_seconds=30) == 0
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda _id, strict=False: None)
    monkeypatch.setattr(source_deletion, "_delete_index_documents", lambda _id, _index: None)
    # The shared integration database can contain unrelated pending tombstones;
    # lease those rows so the bounded claim deterministically selects our seed.
    with test_engine.begin() as connection:
        connection.execute(text("UPDATE source_deletions SET cleanup_next_attempt_at=NULL WHERE id=:id"), {"id": str(tombstone_id)})
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_until=now() + interval '5 minutes'
            WHERE cleanup_status='pending' AND id != :id
        """), {"id": str(tombstone_id)})
    assert source_deletion.reconcile_pending_source_deletions(limit=1, lease_seconds=30) == 1
    with test_engine.connect() as connection:
        row = connection.execute(text("SELECT cleanup_status, cleanup_attempts, cleanup_error, cleanup_completed_at, cleanup_lease_until, cleanup_lease_token FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).mappings().one()
        assert row["cleanup_status"] == "completed"
        assert row["cleanup_attempts"] == 2
        assert row["cleanup_error"] is None and row["cleanup_completed_at"] is not None and row["cleanup_lease_until"] is None and row["cleanup_lease_token"] is None


def test_cleanup_skips_search_deletion_when_opensearch_is_disabled(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    with test_engine.begin() as connection:
        tombstone_id, video_id = _tombstone(connection, youtube_id="postgres-cleanup")
    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))
    monkeypatch.setattr(source_deletion.settings, "SEARCH_BACKEND", "postgres")
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source_deletion, "_delete_file", lambda _path: None)
    calls = []
    monkeypatch.setattr(source_deletion, "_delete_index_documents", lambda *_args: calls.append(_args))

    source_deletion.cleanup_deleted_source(
        {"id": video_id, "tombstone_id": tombstone_id, "raw_path": "/tmp/raw", "wav_path": "/tmp/wav"}
    )

    assert calls == []
    with test_engine.connect() as connection:
        assert connection.execute(
            text("SELECT cleanup_status FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}
        ).scalar_one() == "completed"


def test_opensearch_cleanup_uses_configured_request_and_retries_failures(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    class FailedResponse:
        status_code = 500

        def raise_for_status(self):
            raise RuntimeError("OpenSearch unavailable")

    with test_engine.begin() as connection:
        tombstone_id, video_id = _tombstone(connection, youtube_id="opensearch-cleanup")
    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))
    monkeypatch.setattr(source_deletion.settings, "SEARCH_BACKEND", "opensearch")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_URL", "https://search.example.test/base/")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_INDEX_NATIVE", "native-index")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_INDEX_YOUTUBE", "youtube-index")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_USER", "cleanup-user")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_PASSWORD", "cleanup-password")
    monkeypatch.setattr(source_deletion.settings, "OPENSEARCH_VERIFY_SSL", False)
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source_deletion, "_delete_file", lambda _path: None)
    calls = []
    monkeypatch.setattr(
        source_deletion.requests,
        "post",
        lambda *args, **kwargs: (calls.append((args, kwargs)) or FailedResponse()),
    )

    source_deletion.cleanup_deleted_source(
        {"id": video_id, "tombstone_id": tombstone_id, "raw_path": "/tmp/raw", "wav_path": "/tmp/wav"}
    )

    expected_payload = {"query": {"term": {"video_id": str(video_id)}}}
    assert calls == [
        (
            ("https://search.example.test/base/native-index/_delete_by_query",),
            {"auth": ("cleanup-user", "cleanup-password"), "json": expected_payload, "timeout": 10, "verify": False},
        ),
        (
            ("https://search.example.test/base/youtube-index/_delete_by_query",),
            {"auth": ("cleanup-user", "cleanup-password"), "json": expected_payload, "timeout": 10, "verify": False},
        ),
    ]
    with test_engine.connect() as connection:
        row = connection.execute(
            text("SELECT cleanup_status, cleanup_attempts, cleanup_error FROM source_deletions WHERE id=:id"),
            {"id": str(tombstone_id)},
        ).mappings().one()
    assert row == {
        "cleanup_status": "pending",
        "cleanup_attempts": 1,
        "cleanup_error": "search_index:RuntimeError;search_index:RuntimeError",
    }


def test_cleanup_result_is_fenced_and_expired_lease_is_reclaimable(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    with test_engine.begin() as connection:
        tombstone_id, _ = _tombstone(connection, youtube_id="fenced-cleanup")
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_token=gen_random_uuid(),
                cleanup_lease_until=now() - interval '1 second' WHERE id=:id
        """), {"id": str(tombstone_id)})
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_until=now() + interval '5 minutes'
            WHERE cleanup_status='pending' AND id != :id
        """), {"id": str(tombstone_id)})
    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda _id, strict=False: None)
    monkeypatch.setattr(source_deletion, "_delete_file", lambda _path: None)
    monkeypatch.setattr(source_deletion, "_delete_index_documents", lambda _id, _index: None)
    assert source_deletion.reconcile_pending_source_deletions(limit=1) == 1
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT cleanup_status FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).scalar_one() == "completed"


def test_cleanup_backoff_caps_high_attempt_counts(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    with test_engine.begin() as connection:
        tombstone_id, _ = _tombstone(connection, youtube_id="high-cleanup-attempts")
        token = str(uuid.uuid4())
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_attempts=100,
                cleanup_lease_token=CAST(:token AS uuid),
                cleanup_lease_until=now() + interval '5 minutes'
            WHERE id=:id
        """), {"id": str(tombstone_id), "token": token})
    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))

    source_deletion._record_cleanup_result(tombstone_id, ["cache:RuntimeError"], lease_token=token)

    with test_engine.connect() as connection:
        row = connection.execute(text("""
            SELECT cleanup_status, cleanup_attempts,
                   EXTRACT(EPOCH FROM cleanup_next_attempt_at - now()) AS backoff_seconds
            FROM source_deletions WHERE id=:id
        """), {"id": str(tombstone_id)}).mappings().one()
    assert row["cleanup_status"] == "pending"
    assert row["cleanup_attempts"] == 101
    assert 0 < row["backoff_seconds"] <= 3600


def test_inline_and_daemon_cleanup_claims_are_exclusive_and_fenced(test_engine, monkeypatch):
    import app.source_deletion as source_deletion
    from app.db import SessionLocal

    with test_engine.begin() as connection:
        tombstone_id, video_id = _tombstone(connection, youtube_id="exclusive-cleanup")
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_until=now() + interval '5 minutes'
            WHERE cleanup_status='pending' AND id != :id
        """), {"id": str(tombstone_id)})
    monkeypatch.setattr(source_deletion, "SessionLocal", lambda: SessionLocal(bind=test_engine))
    calls = []
    monkeypatch.setattr(source_deletion, "invalidate_video_data", lambda *_args, **_kwargs: calls.append("cache"))
    monkeypatch.setattr(source_deletion, "_delete_file", lambda _path: None)
    monkeypatch.setattr(source_deletion, "_delete_index_documents", lambda _id, _index: None)

    inline_token = source_deletion._claim_specific_cleanup(tombstone_id)
    assert inline_token is not None
    source_deletion.cleanup_deleted_source({"id": video_id, "tombstone_id": tombstone_id})
    assert calls == []
    assert source_deletion.reconcile_pending_source_deletions(limit=1) == 0

    with test_engine.begin() as connection:
        connection.execute(text("""
            UPDATE source_deletions SET cleanup_lease_until=now() - interval '1 second'
            WHERE id=:id
        """), {"id": str(tombstone_id)})
    assert source_deletion.reconcile_pending_source_deletions(limit=1) == 1
    source_deletion._record_cleanup_result(tombstone_id, ["cache:RuntimeError"], lease_token=inline_token)
    with test_engine.connect() as connection:
        row = connection.execute(text("SELECT cleanup_status, cleanup_attempts FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).mappings().one()
    assert row == {"cleanup_status": "completed", "cleanup_attempts": 1}
    source_deletion._record_cleanup_result(tombstone_id, ["cache:RuntimeError"], lease_token=str(uuid.uuid4()))
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT cleanup_status FROM source_deletions WHERE id=:id"), {"id": str(tombstone_id)}).scalar_one() == "completed"
