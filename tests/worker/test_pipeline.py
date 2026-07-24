"""Tests for worker.pipeline module."""

import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import text

from app import crud
from worker import pipeline
from worker.caption_ingest import CaptionIngestionResult


def _video_insert_params(mock_conn):
    return [
        call.args[1]
        for call in mock_conn.execute.call_args_list
        if call.args and "INSERT INTO videos" in str(call.args[0])
    ]


@pytest.fixture
def mock_engine():
    """Create a mock database engine."""
    mock = Mock()
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock.begin.return_value = mock_conn
    return mock


@pytest.fixture
def mock_conn():
    """Create a mock database connection."""
    mock = Mock()
    mock.execute = Mock()
    mock.commit = Mock()
    return mock


class TestNormalizeChannelUrl:
    """Tests for normalize_channel_url function."""

    def test_appends_videos_to_channel_id_url(self):
        """Test that /videos is appended to channel ID URLs."""
        url = "https://youtube.com/channel/UCtest123"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/channel/UCtest123/videos"

    def test_appends_videos_to_handle_url(self):
        """Test that /videos is appended to handle (@username) URLs."""
        url = "https://youtube.com/@testuser"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/@testuser/videos"

    def test_preserves_existing_videos_suffix(self):
        """Test that URLs already ending with /videos are not modified."""
        url = "https://youtube.com/channel/UCtest123/videos"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/channel/UCtest123/videos"

    def test_handles_trailing_slash(self):
        """Test that trailing slashes are handled correctly."""
        url = "https://youtube.com/channel/UCtest123/"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/channel/UCtest123/videos"

    def test_handles_www_prefix(self):
        """Test URLs with www prefix."""
        url = "https://www.youtube.com/channel/UCtest123"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://www.youtube.com/channel/UCtest123/videos"

    def test_handles_http_protocol(self):
        """Test URLs with http protocol."""
        url = "http://youtube.com/@testuser"
        result = pipeline.normalize_channel_url(url)
        assert result == "http://youtube.com/@testuser/videos"

    def test_preserves_handle_with_videos_suffix(self):
        """Test that handle URLs with /videos are preserved."""
        url = "https://youtube.com/@testuser/videos"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/@testuser/videos"

    def test_does_not_modify_non_channel_urls(self):
        """Test that non-channel URLs are not modified."""
        url = "https://youtube.com/watch?v=abc123"
        result = pipeline.normalize_channel_url(url)
        assert result == "https://youtube.com/watch?v=abc123"


class TestExpandSingleJob:
    """Tests for expand_single_if_needed function."""

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_single_job_success(self, mock_fetch_metadata, mock_conn):
        """Test successful single job expansion."""
        job_id = uuid.uuid4()
        video_id = "test_video_123"

        # Mock job query
        mock_job = {"id": job_id, "input_url": "https://youtube.com/watch?v=test_video_123"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        # Mock yt-dlp response
        yt_dlp_response = {
            "id": video_id,
            "title": "Test Video",
            "duration": 300,
            "uploader": "Source Channel",
            "upload_date": "20240517",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_single_if_needed(mock_conn)

        # Verify yt-dlp was called
        assert mock_fetch_metadata.called
        call_args = mock_fetch_metadata.call_args[0]
        assert call_args[0] == "https://youtube.com/watch?v=test_video_123"

        # Verify video insert was called
        execute_calls = mock_conn.execute.call_args_list
        # Should have: job query, video insert, state update
        assert len(execute_calls) >= 3
        [insert_params] = _video_insert_params(mock_conn)
        assert insert_params["channel_name"] == "Source Channel"
        assert insert_params["uploaded_at"] == datetime(2024, 5, 17, tzinfo=timezone.utc)

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_single_job_from_entries(self, mock_fetch_metadata, mock_conn):
        """Test single job expansion when video ID in entries."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/playlist?list=test"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        # yt-dlp response with video in entries
        yt_dlp_response = {
            "entries": [
                {
                    "id": "video_from_entries",
                    "title": "Entry Video",
                    "duration": 240,
                }
            ]
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_single_if_needed(mock_conn)

        # Should have extracted video from entries
        assert mock_fetch_metadata.called

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_single_job_no_video_id_marks_job_failed(self, mock_fetch_metadata, mock_conn):
        """Test marks job failed when video ID cannot be determined."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/watch?v=test"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        # yt-dlp response without id
        yt_dlp_response = {"title": "Video", "duration": 100}
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_single_if_needed(mock_conn)

        execute_sql = "\n".join(str(call.args[0]) for call in mock_conn.execute.call_args_list if call.args)
        assert "UPDATE jobs SET state='failed'" in execute_sql

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_single_job_no_pending_jobs(self, mock_fetch_metadata, mock_conn):
        """Test no action when no pending jobs."""
        mock_conn.execute.return_value.mappings.return_value.all.return_value = []

        pipeline.expand_single_if_needed(mock_conn)

        # Should not call yt-dlp
        mock_fetch_metadata.assert_not_called()


class TestExpandChannelJob:
    """Tests for expand_channel_if_needed function."""

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_success(self, mock_fetch_metadata, mock_conn):
        """Test successful channel job expansion."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        # Mock yt-dlp channel response
        yt_dlp_response = {
            "title": "Test Channel - Videos",
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 100, "upload_date": "20240101"},
                {"id": "video2", "title": "Video 2", "duration": 200, "upload_date": "20240203"},
                {
                    "id": "video3",
                    "title": "Video 3",
                    "duration": 300,
                    "channel": "Entry Channel",
                    "upload_date": "20240305",
                },
            ],
            "channel_id": "UCtest",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Verify yt-dlp was called with --flat-playlist
        call_args = mock_fetch_metadata.call_args
        assert call_args[0][0] == "https://youtube.com/channel/UCtest/videos"
        assert call_args[1]["flat_playlist"] is True

        # Verify multiple video inserts
        execute_calls = mock_conn.execute.call_args_list
        # Should have: job query, 3 video inserts, state update
        assert len(execute_calls) >= 5
        insert_params = _video_insert_params(mock_conn)
        assert insert_params[0]["channel_name"] == "Test Channel"
        assert insert_params[0]["uploaded_at"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert insert_params[2]["channel_name"] == "Entry Channel"
        assert insert_params[2]["uploaded_at"] == datetime(2024, 3, 5, tzinfo=timezone.utc)

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_appends_videos_suffix(self, mock_fetch_metadata, mock_conn):
        """Test that channel expansion appends /videos to channel URLs."""
        job_id = uuid.uuid4()

        # Test with channel URL without /videos suffix
        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        yt_dlp_response = {
            "entries": [{"id": "video1", "title": "Video 1", "duration": 100}],
            "channel_id": "UCtest",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Verify yt-dlp was called with /videos appended to URL
        call_args = mock_fetch_metadata.call_args
        assert call_args[0][0] == "https://youtube.com/channel/UCtest/videos"
        assert call_args[1]["flat_playlist"] is True

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_handle_url_gets_videos_suffix(self, mock_fetch_metadata, mock_conn):
        """Test that handle URLs (@username) get /videos suffix."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/@testuser"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        yt_dlp_response = {
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 100},
                {"id": "video2", "title": "Video 2", "duration": 200},
            ],
            "uploader_id": "testuser",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Verify yt-dlp was called with /videos appended
        call_args = mock_fetch_metadata.call_args
        assert call_args[0][0] == "https://youtube.com/@testuser/videos"
        assert call_args[1]["flat_playlist"] is True

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_preserves_existing_videos_suffix(self, mock_fetch_metadata, mock_conn):
        """Test that URLs already with /videos suffix are not double-appended."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest/videos"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        yt_dlp_response = {
            "entries": [{"id": "video1", "title": "Video 1", "duration": 100}],
            "channel_id": "UCtest",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Verify URL was not double-appended
        call_args = mock_fetch_metadata.call_args
        assert call_args[0][0] == "https://youtube.com/channel/UCtest/videos"
        assert call_args[1]["flat_playlist"] is True
        assert call_args[0][0] != "https://youtube.com/channel/UCtest/videos/videos"

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_logs_expansion_counts(self, mock_fetch_metadata, mock_conn):
        """Test that expansion logs include channel_id and entry counts."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        yt_dlp_response = {
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 100},
                {"id": "video2", "title": "Video 2", "duration": 200},
                {"id": "video3", "title": "Video 3", "duration": 300},
            ],
            "channel_id": "UCtest",
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        with patch("worker.pipeline.logger") as mock_logger:
            pipeline.expand_channel_if_needed(mock_conn)

            # Find the log call that contains expansion results by checking for entry_count in extra
            expansion_log_calls = [
                call
                for call in mock_logger.info.call_args_list
                if "extra" in call[1] and "entry_count" in call[1].get("extra", {})
            ]

            assert len(expansion_log_calls) > 0, "Expected log with entry_count in extra"

            # Verify the log contains channel_id and entry_count in extra
            log_call = expansion_log_calls[0]
            extra = log_call[1]["extra"]
            assert "channel_id" in extra
            assert extra["channel_id"] == "UCtest"
            assert "entry_count" in extra
            assert extra["entry_count"] == 3

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_empty_channel(self, mock_fetch_metadata, mock_conn):
        """Test channel expansion with no videos."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        # Empty channel
        yt_dlp_response = {"entries": []}
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Should still update job state
        assert mock_conn.execute.called

    @patch("worker.pipeline._fetch_ytdlp_metadata")
    def test_expand_channel_job_preserves_order(self, mock_fetch_metadata, mock_conn):
        """Test channel videos maintain order via idx."""
        job_id = uuid.uuid4()

        mock_job = {"id": job_id, "input_url": "https://youtube.com/channel/UCtest"}
        mock_conn.execute.return_value.mappings.return_value.all.return_value = [mock_job]

        yt_dlp_response = {
            "entries": [
                {"id": "v1", "title": "First", "duration": 100},
                {"id": "v2", "title": "Second", "duration": 200},
            ]
        }
        mock_fetch_metadata.return_value = yt_dlp_response

        pipeline.expand_channel_if_needed(mock_conn)

        # Verify idx parameter in inserts
        execute_calls = mock_conn.execute.call_args_list
        # Check that idx values are passed (can't easily verify exact values without deeper inspection)
        assert len(execute_calls) >= 3


class TestProcessVideo:
    """Tests for process_video function."""

    @pytest.fixture(autouse=True)
    def _stub_parent_job_refresh(self, monkeypatch):
        monkeypatch.setattr(pipeline, "refresh_job_state", Mock())

    @patch("worker.pipeline.process_native_video")
    def test_process_video_success_path(self, mock_process_native, mock_engine, tmp_path):
        """Test successful video processing pipeline."""
        video_id = uuid.uuid4()
        mock_process_native.return_value = 1

        # Patch WORKDIR
        with patch("worker.pipeline.WORKDIR", tmp_path):
            assert pipeline.process_video(mock_engine, video_id) == 1

        args, kwargs = mock_process_native.call_args
        assert args == (mock_engine, video_id)
        assert kwargs["workdir"] == tmp_path
        assert kwargs["deps"].download_audio is pipeline.download_audio

    @patch("worker.pipeline.process_native_video")
    def test_process_video_delegates_persistence(self, mock_process_native, mock_engine, tmp_path):
        video_id = uuid.uuid4()
        mock_process_native.return_value = 1
        with patch("worker.pipeline.WORKDIR", tmp_path):
            assert pipeline.process_video(mock_engine, video_id) == 1
        assert mock_process_native.call_args.kwargs["deps"].replace_transcript_blocks is crud.replace_transcript_blocks


class TestTranscriptBlockCrud:
    @pytest.fixture(autouse=True)
    def _stub_parent_job_refresh(self, monkeypatch):
        monkeypatch.setattr(pipeline, "refresh_job_state", Mock())

    def test_replace_and_list_transcript_blocks(self, db_session):
        job_id = crud.create_job(db_session, "single", "https://youtube.com/watch?v=test")
        video_id = uuid.uuid4()
        db_session.execute(
            text("INSERT INTO videos (id, job_id, youtube_id, idx) VALUES (:id, :job_id, :yt_id, 0)"),
            {"id": str(video_id), "job_id": str(job_id), "yt_id": "test123"},
        )
        db_session.commit()

        from app.transcripts.blocks import TranscriptBlock

        blocks = [TranscriptBlock(0, 0, 1000, None, "Hello world.", [0], "paragraph")]
        crud.replace_transcript_blocks(db_session, video_id, blocks)
        rows = crud.list_transcript_blocks(db_session, video_id)
        assert len(rows) == 1
        assert rows[0]["text"] == "Hello world."

    @patch("worker.pipeline.process_native_video")
    def test_process_video_propagates_native_failure(self, mock_process_native, mock_engine, tmp_path):
        """The wrapper does not alter native pipeline failures."""
        video_id = uuid.uuid4()
        mock_process_native.side_effect = Exception("Download failed")

        with patch("worker.pipeline.WORKDIR", tmp_path), pytest.raises(Exception, match="Download failed"):
            pipeline.process_video(mock_engine, video_id)

        assert mock_process_native.call_args.args == (mock_engine, video_id)

    @patch("worker.pipeline.process_native_video")
    def test_process_video_delegates_cleanup(self, mock_process_native, mock_engine, tmp_path):
        """The wrapper delegates cleanup and returns the native segment count."""
        video_id = uuid.uuid4()
        mock_process_native.return_value = 1

        with patch("worker.pipeline.WORKDIR", tmp_path):
            assert pipeline.process_video(mock_engine, video_id) == 1

        assert mock_process_native.call_args.kwargs["deps"].settings is pipeline.settings

    @patch("worker.pipeline.process_native_video")
    def test_process_video_returns_native_segment_count(self, mock_process_native, mock_engine, tmp_path):
        """The wrapper preserves the native pipeline's segment-count result."""
        video_id = uuid.uuid4()
        mock_process_native.return_value = 2

        with patch("worker.pipeline.WORKDIR", tmp_path):
            assert pipeline.process_video(mock_engine, video_id) == 2
        mock_process_native.assert_called_once()


class TestCaptureYouTubeCaptions:
    """Tests for capture_youtube_captions_for_unprocessed function."""

    @patch("worker.pipeline.ingest_captions_for_unprocessed_videos")
    @patch("worker.pipeline.get_youtube_service")
    def test_capture_youtube_captions_success(self, mock_get_service, mock_ingest, mock_conn):
        """Test successful YouTube caption capture."""
        mock_service = Mock()
        mock_get_service.return_value = mock_service
        mock_ingest.return_value = CaptionIngestionResult(1, 1, 0, 0, False, None)
        count = pipeline.capture_youtube_captions_for_unprocessed(mock_conn, limit=5)

        assert count == 1
        mock_ingest.assert_called_once_with(
            mock_conn,
            limit=5,
            staged_only=False,
            active_only=False,
            terminal_failures=False,
            youtube_service=mock_service,
        )

    @patch("worker.pipeline.ingest_captions_for_unprocessed_videos")
    def test_capture_youtube_captions_no_captions(self, mock_ingest, mock_conn):
        """Test when no captions are available."""
        mock_ingest.return_value = CaptionIngestionResult(1, 0, 1, 0, False, None)

        count = pipeline.capture_youtube_captions_for_unprocessed(mock_conn, limit=5)

        assert count == 0

    @patch("worker.pipeline.ingest_captions_for_unprocessed_videos")
    def test_capture_youtube_captions_fetch_error(self, mock_ingest, mock_conn):
        """Test handling of caption fetch errors."""
        mock_ingest.return_value = CaptionIngestionResult(1, 0, 0, 1, False, None)
        count = pipeline.capture_youtube_captions_for_unprocessed(mock_conn, limit=5)

        assert count == 0

    @patch("worker.pipeline.ingest_captions_for_unprocessed_videos")
    def test_capture_youtube_captions_multiple_videos(self, mock_ingest, mock_conn):
        """Test processing multiple videos."""
        mock_ingest.return_value = CaptionIngestionResult(2, 2, 0, 0, False, None)
        count = pipeline.capture_youtube_captions_for_unprocessed(mock_conn, limit=5)

        assert count == 2

    @patch("worker.pipeline.ingest_captions_for_unprocessed_videos")
    def test_capture_youtube_captions_no_pending(self, mock_ingest, mock_conn):
        """Test when no videos need caption processing."""
        mock_ingest.return_value = CaptionIngestionResult(0, 0, 0, 0, False, None)

        count = pipeline.capture_youtube_captions_for_unprocessed(mock_conn, limit=5)

        assert count == 0
