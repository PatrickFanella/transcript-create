from worker.repositories import VideoRepository


class FakeResult:
    def __init__(self, rows=None, rowcount=1):
        self.rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return FakeResult()


def test_mark_video_completed_updates_diarization_state():
    conn = FakeConn()
    repo = VideoRepository(conn)
    repo.mark_completed("video-1", diarization_state="pending")
    sql, params = conn.calls[-1]
    assert "UPDATE videos" in sql
    assert "state='completed'" in sql
    assert params == {"video_id": "video-1", "diarization_state": "pending"}


def test_mark_video_completed_rejects_fenced_or_missing_row():
    conn = FakeConn()
    conn.execute = lambda *_args, **_kwargs: FakeResult(rowcount=0)
    repo = VideoRepository(conn)

    import pytest

    with pytest.raises(RuntimeError, match="exactly one"):
        repo.mark_completed("video-1", diarization_state="pending")


def test_mark_caption_running_records_state():
    conn = FakeConn()
    repo = VideoRepository(conn)
    repo.mark_caption_running("video-2")
    sql, params = conn.calls[-1]
    assert "caption_ingest_state='running'" in sql
    assert "diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%'" in sql
    assert params == {"video_id": "video-2"}


def test_mark_caption_unavailable_clears_stale_error():
    conn = FakeConn()
    repo = VideoRepository(conn)
    repo.mark_caption_unavailable("video-3")
    sql, params = conn.calls[-1]
    assert "caption_ingest_state='unavailable'" in sql
    assert "caption_ingest_error=NULL" in sql
    assert params == {"video_id": "video-3"}


def test_mark_caption_pending_truncates_error():
    conn = FakeConn()
    repo = VideoRepository(conn)
    repo.mark_caption_pending_with_error("video-4", "x" * 6000)
    _sql, params = conn.calls[-1]
    assert params == {"video_id": "video-4", "error": "x" * 5000}


def test_caption_writers_reject_fenced_or_missing_row():
    conn = FakeConn()
    conn.execute = lambda *_args, **_kwargs: FakeResult(rowcount=0)
    repo = VideoRepository(conn)

    import pytest

    for writer, args in (
        (repo.mark_caption_running, ("video",)),
        (repo.mark_caption_pending_with_error, ("video", "error")),
        (repo.mark_caption_failed, ("video", "error")),
        (repo.mark_caption_unavailable, ("video",)),
        (repo.mark_caption_completed, ("video",)),
    ):
        with pytest.raises(RuntimeError, match="exactly one non-canary"):
            writer(*args)
