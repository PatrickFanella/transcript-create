from __future__ import annotations

from sqlalchemy import text


class VideoRepository:
    def __init__(self, conn):
        self.conn = conn

    def mark_completed(self, video_id: str, *, diarization_state: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET state='completed', error=NULL, diarization_state=:diarization_state, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id, "diarization_state": diarization_state},
        )
        if result.rowcount != 1:
            raise RuntimeError("video completion did not affect exactly one non-canary video")

    def mark_caption_running(self, video_id: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET caption_ingest_state='running', caption_ingest_error=NULL, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id},
        )
        self._require_non_canary_update(result)

    def mark_caption_pending_with_error(self, video_id: str, error: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET caption_ingest_state='pending', caption_ingest_error=:error, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id, "error": error[:5000]},
        )
        self._require_non_canary_update(result)

    def mark_caption_failed(self, video_id: str, error: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET caption_ingest_state='failed', caption_ingest_error=:error, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id, "error": error[:5000]},
        )
        self._require_non_canary_update(result)

    def mark_caption_unavailable(self, video_id: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET caption_ingest_state='unavailable', caption_ingest_error=NULL, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id},
        )
        self._require_non_canary_update(result)

    def mark_caption_completed(self, video_id: str) -> None:
        result = self.conn.execute(
            text("""
                UPDATE videos
                SET caption_ingest_state='completed', caption_ingest_error=NULL, updated_at=now()
                WHERE id=:video_id AND (diarization_error IS NULL OR diarization_error NOT LIKE 'canary-%')
                """),
            {"video_id": video_id},
        )
        self._require_non_canary_update(result)

    @staticmethod
    def _require_non_canary_update(result) -> None:
        if result.rowcount != 1:
            raise RuntimeError("caption-state update did not affect exactly one non-canary video")
